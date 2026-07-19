"""Byte-exactness differential fuzzer for the deity lifter/VM/recorder.

Three legs over a seeded synthetic-player corpus (``_fuzzgen``): ``PcodeVM``
writes + ``wlog``, recorder ``replay``, and the sidtrace oracle. Legs 1-2 run
in-process over the corpus; leg 3 is a small skippable subset (docs/differential-fuzz.md).
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

import pytest

from deity_informant import PcodeVM, RecVM, lift, record, run_sub

import _fuzzgen as G

_PER = 8
_PLAYERS = G.players(_PER)
_IDS = [f"{p.name}-{p.seed[1]}" for p in _PLAYERS]
_SID_OUTS = {G.SID + r for r in range(0x19)}


class _Log(PcodeVM):
    """PcodeVM recording ordered writes to ``outs`` (independent leg-1 oracle)."""

    def __init__(self, mem, outs):
        super().__init__(mem)
        self.outs = set(outs)
        self.log = []

    def _wr(self, addr, val, sz):
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if a in self.outs:
                self.log.append((a, (val >> (8 * i)) & 0xFF))
        super()._wr(addr, val, sz)


def _image(cells):
    m = bytearray(0x10000)
    for a, v in cells.items():
        m[a] = v
    return m


def _run_legs(p):
    """Leg 1 (``_Log`` ordered logs + ``wlog``), leg 1b (``RecVM`` concrete), leg 2 (replay)."""
    cells = p.image_data()
    vm = _Log(_image(cells), p.outputs)
    vm.wlog = []
    cache = {}
    logs = []
    for _ in range(p.frames):
        vm.log = []
        run_sub(vm, p.org, cache, lift)
        logs.append(list(vm.log))
    rvm = RecVM(_image(cells))
    rvm.wlog = []
    rcache = {}
    for _ in range(p.frames):
        rvm.reset_invocation()
        run_sub(rvm, p.org, rcache, lift)
    rec = record(_image(cells), run_sub, p.org, p.outputs, p.frames)
    return logs, vm, rvm, rec


@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_leg1_vs_leg2_byte_exact(p):
    """Recorder replay reproduces the VM's ordered write stream, per frame."""
    logs, vm, rvm, rec = _run_legs(p)
    for i in range(p.frames):
        assert rec.replay(i) == logs[i], (p.name, p.seed, "frame", i)
    assert rvm.wlog == vm.wlog, (p.name, p.seed, "wlog")  # recorder VM cycle-exact
    assert bytes(rvm.mem) == bytes(vm.mem), (p.name, p.seed, "mem")


def test_class_coverage_exhaustive():
    """Every required idiom class is exercised by >0 generated players."""
    cc = Counter()
    for p in _PLAYERS:
        for c in p.classes:
            cc[c] += 1
    missing = G.REQUIRED_CLASSES - set(cc)
    assert not missing, f"idiom classes with 0 players: {sorted(missing)}"
    for c in G.REQUIRED_CLASSES:
        assert cc[c] > 0


def test_corpus_is_deterministic():
    """The seeded corpus is byte-reproducible run to run."""
    a = G.players(_PER)
    b = G.players(_PER)
    assert [(x.name, x.prog, sorted(x.data.items())) for x in a] == [
        (y.name, y.prog, sorted(y.data.items())) for y in b
    ]


# ---- leg 3: Dockerized sidtrace oracle (expensive; small subset; skippable) ---
_VOLUME_REG, _DRIVER_VOLUME = 0x18, 0x0F
_ORACLE_PLAYERS = [
    p for p in _PLAYERS if p.name in G.ORACLE_SAFE and p.outputs & _SID_OUTS and p.seed[1] < 2
]


def _change_stream(writes, reg_count=25):
    """Ordered register-*changing* SID writes from a cold start (volume pre-seeded)."""
    st = [0] * reg_count
    st[_VOLUME_REG] = _DRIVER_VOLUME
    out = []
    for r, v in writes:
        if 0 <= r < reg_count and st[r] != v:
            st[r] = v
            out.append((r, v))
    return out


def _to_psid(p):
    from pysidtracker import write_psid  # pylint: disable=import-outside-toplevel

    load = min(p.org, p.init_org)
    cells = p.image_data()
    end = max(cells) + 1
    image = bytearray(end - load)
    for a, v in cells.items():
        if load <= a < end:
            image[a - load] = v
    body = bytes((load & 0xFF, load >> 8)) + bytes(image)
    return write_psid(load=0, init=p.init_org, play=p.org, image=body, kind="PSID")


def _render_sidtrace(tune_path, out_path, seconds=4):
    from pysidtracker.oracle import SIDTRACE_IMAGE  # pylint: disable=import-outside-toplevel

    name = "trace.csv.zst"

    def d(args):
        return subprocess.run(
            ["docker", *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )

    created = d(
        [
            "create",
            "-w",
            "/work",
            "--entrypoint",
            "sidtrace",
            SIDTRACE_IMAGE,
            name,
            Path(tune_path).name,
            f"-t{seconds}",
        ]
    )
    cid = created.stdout.decode().strip()
    try:
        d(["cp", str(tune_path), f"{cid}:/work/{Path(tune_path).name}"])
        d(["start", "-a", cid])
        d(["cp", f"{cid}:/work/{name}", str(out_path)])
    finally:
        d(["rm", "-f", cid])
    return out_path


def _in_process_stream(p, frames):
    cells = p.image_data()
    vm = _Log(_image(cells), _SID_OUTS)
    cache = {}
    run_sub(vm, p.init_org, cache, lift)
    vm.mem[G.SID + _VOLUME_REG] = _DRIVER_VOLUME  # PSID cold-start volume
    vm.wlog = []
    for _ in range(frames):
        run_sub(vm, p.org, cache, lift)
    return _change_stream([(r, v) for _c, r, v in vm.wlog])


@pytest.mark.oracle
@pytest.mark.parametrize(
    "p", _ORACLE_PLAYERS, ids=[f"{q.name}-{q.seed[1]}" for q in _ORACLE_PLAYERS]
)
def test_leg3_sidtrace_oracle(p, tmp_path):
    """In-process SID change stream matches the sidtrace oracle (prefix, skippable)."""
    pytest.importorskip("pysidtracker")
    from pysidtracker.oracle import read_sidtrace  # pylint: disable=import-outside-toplevel

    tune = tmp_path / f"{p.name}.sid"
    tune.write_bytes(_to_psid(p))
    try:
        csv = _render_sidtrace(tune, tmp_path / "t.csv.zst")
        rows = read_sidtrace(csv)
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip(f"sidtrace oracle unavailable: {exc}")
    orc = [(row.reg, row.value) for row in rows if row.chip == 0 and 0 <= row.reg < 25]
    if orc and orc[0] == (_VOLUME_REG, _DRIVER_VOLUME):
        orc = orc[1:]
    mine = _in_process_stream(p, frames=200)
    n = min(len(mine), len(orc))
    assert n > 0, f"{p.name}: empty overlap (mine={len(mine)} orc={len(orc)})"
    assert mine[:n] == orc[:n], f"{p.name}: first divergence within {n} changes"
