"""The canonical end-to-end example, gated (docs/register-model-lift-impl.md).

The plan's stages MUST keep this pipeline green: playroutine -> decompile ->
e-graph minimize -> Z3-proved u16 folds -> role-typed state machine ->
frame-oracle equality."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from deity_informant import roles
from examples import state_machine_lift as sml
from examples.state_machine_lift import (
    FRAMES,
    SHADOW,
    TEST_BIT,
    VOICES,
    WAVEF,
    ZPV,
    adsr_before_gate,
    change_stream,
    classify_roles,
    framelog,
    grids_from_writes,
    minimized_wav,
    note_starts,
    oscillator_reset_frames,
    pipeline,
    pretty,
    render,
    restart_shape,
    sidplayfp_wav,
    sidtrace_stream,
    to_psid,
    image_end,
)


@pytest.fixture(scope="module", name="art")
def _art():
    return pipeline()


@pytest.fixture(scope="module", name="min_grids")
def _min_grids(art):
    return grids_from_writes(art["init_writes"], art["min_frames"])


def test_folds_all_proved(art):
    kinds = {p.split("(")[0] for p in art["proofs"]}
    assert kinds == {"forward_shadow", "pair_store", "pair_set", "advance"}
    got = set(art["proofs"])
    for b in ZPV:  # the three voices fold per voice, on their own cursor and note pair
        assert "pair_set(ptr_%04X)" % b in got
        assert "pair_store(zp_%02X,zp_%02X)" % (b + 4, b + 5) in got
        assert "advance(ptr_%04X,+2,nocarry)" % b in got
    assert any(p.endswith(",wide)") for p in got), "no observed page cross to fold"


def test_shadow_forwards_off_the_sid_path(art):
    """The RAM SID shadow is looked through: no sink reads it back."""
    fwd = [p for p in art["proofs"] if p.startswith("forward_shadow")]
    assert len(fwd) == 3 * VOICES  # ad, sr and ctrl per voice
    shadow = re.compile(r"m_0[0-9A-F]{3}")
    text = render(art["folded"], classify_roles(art["folded"]))
    assert not [n for n in shadow.findall(text) if SHADOW <= int(n[2:], 16) < SHADOW + 7 * VOICES]
    for v in range(1, VOICES + 1):
        assert "sid.v%d.ctrl = v%d_ctl" % (v, v) in text
        assert "sid.v%d.attack_decay = v%d_ad" % (v, v) in text
    assert "m_034" in art["eqlift_text"], "the emitter's own text keeps the read-back"


def test_minimized_matches_vm_frame_projection(art):
    assert framelog.canonical(art["min_frames"]) == framelog.canonical(art["orig_frames"])


def test_minimized_grid_matches_vm(art, min_grids):
    assert min_grids == art["orig_grids"]


def test_hard_restart_survives_minimization(art, min_grids):
    """Both orderings hold on both sides: ADSR before the gate in a frame, and
    ADSR-zero then TEST across the two frames before each attack."""
    assert adsr_before_gate(art["orig_frames"]) and adsr_before_gate(art["min_frames"])
    for v in range(VOICES):
        want = ((0, 0, WAVEF[v]), (0, 0, WAVEF[v] | TEST_BIT))
        shapes = restart_shape(art["orig_grids"], v)
        assert len(shapes) > 8 and all(s == want for s in shapes)
        assert restart_shape(min_grids, v) == shapes
        attacks, b = note_starts(art["orig_grids"], v), 7 * v
        reset = oscillator_reset_frames(art["orig_grids"], v)
        assert reset and all(f - 1 in reset for f in attacks if f)
        for i in reset:  # TEST is held for exactly one frame, over a zeroed envelope
            g = art["orig_grids"][i]
            assert (g[b + 5], g[b + 6], g[b + 4]) == (0, 0, WAVEF[v] | TEST_BIT)
            assert i + 1 == FRAMES or not art["orig_grids"][i + 1][b + 4] & TEST_BIT
        assert oscillator_reset_frames(min_grids, v) == reset
        assert note_starts(min_grids, v) == attacks


def test_roles_are_the_plan_s_own_and_the_field_line_is_the_dialect_s(art):
    """The example's roles are ``roles.ROLES`` and it spells them as the grammar does."""
    got = classify_roles(art["folded"])
    assert set(got.values()) <= set(roles.ROLES)
    text = render(art["folded"], got)
    by_voice = {pretty(n): r for n, r in got.items()}
    for v in range(1, VOICES + 1):
        assert by_voice["v%d_pos" % v] == "cursor"
        assert by_voice["v%d_dur" % v] == "counter"
        assert by_voice["v%d_phase" % v] == "accumulator"
        fields = ("note_lo", "note_hi", "vib", "ctl", "ad", "sr")
        assert {by_voice["v%d_%s" % (v, f)] for f in fields} == {"parameter"}
        assert "v%d_pos: cursor u16" % v in text and "v%d_dur: counter u8" % v in text
        assert "v%d_pos:u16 += 2" % v in text and "sid.v%d.freq:u16" % v in text


def test_independent_engine_grid(art):
    oracle_mod = pytest.importorskip("pysidtracker.oracle")
    psid = to_psid(art["mem"], image_end(art["labels"]))
    oracle = [g[:25] for g in oracle_mod.register_grid(psid, FRAMES)]
    assert oracle == art["orig_grids"]


@pytest.mark.oracle
def test_sidplayfp_sidtrace_oracle(art):
    pytest.importorskip("pysidtracker")
    try:
        stream = sidtrace_stream(art["mem"], art["labels"])
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip("sidtrace oracle unavailable: %s" % exc)
    if stream and stream[0] == (24, 0x0F):
        stream = stream[1:]
    mine = change_stream(art["init_writes"], art["min_frames"], volume=0x0F)
    n = min(len(stream), len(mine))
    assert n and mine[:n] == stream[:n]


@pytest.mark.oracle
def test_wav_renders(art, tmp_path):
    """The tune is audible: the minimized write stream and sidplayfp both render."""
    wave = pytest.importorskip("wave")
    pytest.importorskip("pysidtracker.audio")

    def seconds(path):
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()

    mine = minimized_wav(art, tmp_path / "minimized.wav")
    assert seconds(mine) > 10
    try:
        theirs = sidplayfp_wav(art["mem"], art["labels"], tmp_path / "tune.wav", seconds=12)
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip("sidplayfp unavailable: %s" % exc)
    assert seconds(theirs) > 10


# The goal pinned: properties enumerated from the artifact, xfails naming their stage.
XFAIL = dict(strict=True)
ARCH = frozenset(("a", "x", "y", "sp", "cflag", "nflag", "zflag", "vflag"))
LINE_PIN, COST_PIN = 243, 679  # pinned 2026-08-10 at 015a2e3; a stage lowers these, never raises
EVIDENCE = {  # per role, the clause its declaration owes (sidprog.lark statedef)
    "cursor": r"\bin\s+\w+",
    "accumulator": r"\b(?:observed|mask|bound)\b",
    "vm": r"\b(?:ops|operators)\b",
}
REEMIT = ("reemit_6502", "emit_6502", "assemble_6502")
SEED_CODE = (
    "from examples.state_machine_lift import classify_roles, pipeline, render;"
    "a = pipeline(frames=%d);"
    "import sys; sys.stdout.write(render(a['folded'], classify_roles(a['folded'])))"
)
DECL = re.compile(r"^\s{2}(\w+):\s*(\w+)\s+(\S+)(.*)$")
OPSET = re.compile(r"^[^\n]*\b(?:operators|ops)\b[^\n]*\{$", re.M)
OPDECL = re.compile(r"\bop\s+(\w+)\b[^\n]*?\barity\s+(\d+)\b[^\n]*?\bwrites\b")


@pytest.fixture(scope="module", name="role_map")
def _role_map(art):
    return classify_roles(art["folded"])


@pytest.fixture(scope="module", name="role_text")
def _role_text(art, role_map):
    return render(art["folded"], role_map)


@pytest.fixture(scope="module", name="post_init_ram")
def _post_init_ram(art):
    return sml.run_vm(art["mem"], 0)[1]


def _sub(s):
    if s[0] == "if":
        return [s[2], s[3]]
    if s[0] == "loop":
        return [s[1]]
    if s[0] == "switch":
        return [b for _l, b in s[1]]
    return []


def _walk(stmts):
    for s in stmts:
        yield s
        for b in _sub(s):
            yield from _walk(b)


def _cells(term, out):
    """The state-cell names a term reads; an index base is an address, not a read."""
    if not isinstance(term, tuple):
        return out
    if term and term[0] == "name" and sml.cell_addr(term[1]) is not None:
        out.add(term[1])
        return out
    for kid in term:
        _cells(kid, out)
    return out


def _reads_by_kind(stmts):
    """Cell names read by data statements, and by control transfers."""
    data, ctrl = set(), set()
    for s in _walk(stmts):
        into = ctrl if s[0] == "dgoto" else data
        for part in s[1:]:
            if isinstance(part, tuple):
                _cells(part, into)
        if s[0] == "st16":
            into.update(n for n in s[2:4] if sml.cell_addr(n) is not None)
        elif s[0] == "adv16":
            into.add(s[1])
    return data, ctrl


def _addrs(name):
    """The RAM addresses a state-cell name covers; a pair covers both lanes."""
    a = sml.cell_addr(name)
    if a is not None:
        return (a,)
    lo = sml.cell_addr(name + "_lo")
    return () if lo is None else (lo, lo + 1)


def _state_decls(text):
    """``{cell: (role, type, rest of the declaration)}`` off the rendered state block."""
    out, inside = {}, False
    for line in text.splitlines():
        if line.startswith("state {"):
            inside = True
        elif inside and line.startswith("}"):
            break
        elif inside:
            m = DECL.match(line)
            if m:
                out[m.group(1)] = m.groups()[1:]
    return out


def _term_cost(x):
    if isinstance(x, tuple):
        return 1 + sum(_term_cost(k) for k in x)
    if isinstance(x, list):
        return sum(_term_cost(k) for k in x)
    return 0


class _TracedRam(bytearray):
    """Machine RAM recording whether each address is first read or first written."""

    def __init__(self, *args):
        super().__init__(*args)
        self.first = {}

    def __getitem__(self, i):
        if isinstance(i, int):
            self.first.setdefault(i, "r")
        return super().__getitem__(i)

    def __setitem__(self, i, value):
        if isinstance(i, int):
            self.first.setdefault(i, "w")
        super().__setitem__(i, value)


def _carried_addrs(art, ram0):
    """Addresses some frame reads before writing: what genuinely crosses the boundary."""
    machine = sml.Machine(sml.Flat(art["folded"]), ram0)
    ram = _TracedRam(ram0)
    machine.ram = ram
    carried = set()
    for _ in art["min_frames"]:
        ram.first = {}
        machine.frame()
        carried |= {a for a, kind in ram.first.items() if kind == "r"}
    return carried


def _seeded_render(seed, frames):
    got = subprocess.run(
        [sys.executable, "-c", SEED_CODE % frames],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=dict(os.environ, PYTHONHASHSEED=seed),
        check=True,
        stdout=subprocess.PIPE,
    )
    return got.stdout


@pytest.mark.xfail(reason="register-model-lift stage 3c/4: zero machine shapes", **XFAIL)
def test_no_architectural_register_survives_as_a_value(role_text):
    """Every value flows through declared state or a width-typed local.

    At pin time the lifted text still names ``a`` and ``y``."""
    names = set(re.findall(r"[A-Za-z_]\w*", re.sub(r"\$[0-9A-Fa-f]+|;.*", "", role_text)))
    assert not ARCH & names


@pytest.mark.xfail(reason="register-model-lift stage 3c/4: typed handler switch", **XFAIL)
def test_smc_dispatch_cells_are_not_data_state(art, role_map):
    """A cell whose only reads are the computed transfer is a JMP operand, not state."""
    data, ctrl = _reads_by_kind(art["folded"])
    assert not sorted(set(role_map) & (ctrl - data))


@pytest.mark.xfail(reason="register-model-lift stage 3d/4: VM operator sets (_ARITY)", **XFAIL)
def test_vm_family_operator_set_is_emitted(art, role_text):
    """The interpreter's grammar is declared and its scripts print decoded."""
    want = {m.group(1) for k in art["labels"] for m in [re.fullmatch(r"v\d+_c_(\w+)", k)] if m}
    got = OPSET.search(role_text)
    decls = dict(OPDECL.findall(role_text[got.end() :].split("\n}")[0])) if got else {}
    assert set(decls) == want, "no operator set declaring each op's arity and effect cells"
    alt = "|".join(sorted(want))
    for name in sorted(k for k in art["labels"] if re.fullmatch(r"script\d+", k)):
        pat = r"^\s*%s\b[^\n]*\b(?:%s)\b" % (re.escape(name), alt)
        assert re.search(pat, role_text, re.M), "%s prints as raw bytes" % name


@pytest.mark.xfail(reason="register-model-lift stage 3b/3c: scratch is demoted", **XFAIL)
def test_state_block_holds_no_scratch(art, role_map, post_init_ram):
    """Every declared cell is read before its first write in some frame.

    At pin time the six SMC JMP-operand cells (``m_103F``/``m_1040``,
    ``m_111D``/``m_111E``, ``m_11FB``/``m_11FC``) are written before every read,
    which makes them per-frame scratch the extractor owes a demotion."""
    carried = _carried_addrs(art, post_init_ram)
    assert not sorted(n for n in role_map if _addrs(n) and not set(_addrs(n)) & carried)


@pytest.mark.xfail(reason="register-model-lift stage 4: roles carry their evidence", **XFAIL)
def test_roles_carry_their_evidence(role_map, role_text):
    """A cursor names the block it walks, an accumulator its bound, a vm cell its ops."""
    decls = _state_decls(role_text)
    assert not sorted(
        pretty(n)
        for n, r in role_map.items()
        if r in EVIDENCE and not re.search(EVIDENCE[r], decls.get(pretty(n), ("", "", ""))[2])
    )


@pytest.mark.xfail(reason="register-model-lift stage 4: init emits declared initializers", **XFAIL)
def test_init_lifts_to_declared_initial_values(art, role_map, role_text, post_init_ram):
    """Each cell's post-init value is its declaration's initializer; only SID writes stay."""
    assert all(0 <= r < 25 for r, _v in art["init_writes"]), "init writes off the SID boundary"
    decls, want = _state_decls(role_text), {}
    for n in role_map:
        addrs = _addrs(n)
        if addrs:
            want[pretty(n)] = sum(post_init_ram[a] << (8 * i) for i, a in enumerate(addrs))
    got = {}
    for name, decl in decls.items():
        m = re.search(r"=\s*\$([0-9A-Fa-f]+)", decl[2])
        if m:
            got[name] = int(m.group(1), 16)
    assert {k: got.get(k) for k in want} == want


@pytest.mark.xfail(reason="register-model-lift stage 4: the round-trip witness", **XFAIL)
def test_round_trip_witness_is_frame_identical(art):
    """Minimal 6502 re-emitted from the minimized program replays frame-for-frame.

    No evaluator in the trust chain. The re-emitter is stage 4's work, so today
    this fails on the missing capability and not on a divergence."""
    emit = next(
        (f for m in (sml, sml.eqlift_mem) for n in REEMIT if callable(f := getattr(m, n, None))),
        None,
    )
    assert emit is not None, "no minimal-6502 re-emission capability"
    mem, _labels = emit(art["folded"], art["labels"])
    frames = sml.run_vm(mem, len(art["min_frames"]))[2]
    assert framelog.canonical(frames) == framelog.canonical(art["orig_frames"])


def test_render_is_hash_seed_independent():
    """adoption §10's closed defect, kept closed: no consumer sees the extraction order."""
    frames = FRAMES // 4
    assert _seeded_render("0", frames) == _seeded_render("12345", frames)


def test_size_ratchet(art, role_text):
    """Emitted size and extracted term cost: a stage lowers them or holds them."""
    assert len(role_text.splitlines()) <= LINE_PIN
    assert _term_cost(art["folded"]) <= COST_PIN
