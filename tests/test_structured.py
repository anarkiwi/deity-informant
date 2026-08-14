"""Structured decompiler: full-length real-tune acceptance (cycle-stamped
bit-exact model replay), fuzz-corpus development checks, loud faults.
Artifact-layer laws live in test_frameprog."""

from pathlib import Path

import pytest

from deity_informant import frameprog
from deity_informant import structured as S
from deity_informant import vm
from deity_informant.c64 import load_psid

import _fuzzgen as G

from _corpus import corpus_params

HVSC = Path(__file__).resolve().parent.parent / ".oracle-cache" / "hvsc"
SONGLENGTHS = HVSC / "Songlengths.md5"

_PLAYERS = G.players(3)
_IDS = [f"{p.name}-{p.seed[1]}" for p in _PLAYERS]


def _image(p):
    m = bytearray(0x10000)
    for a, v in p.image_data().items():
        m[a] = v
    if p.init is None:
        m[0x0F00] = 0x60  # RTS: empty init
    return m


def _init(p):
    return p.init_org if p.init is not None else 0x0F00


def _verify(mem, init, play, frames, subtune=0, img=None):
    model, ev = S.decompile(mem, init, play, frames, subtune, img=img)
    assert model.prologue == ev.prologue  # init's SID writes, order-preserved
    for site, pr in model.proofs.items():
        obs = model.pcs.get(site) if pr.kind == "opcode" else model.ev_targets.get(site)
        assert set(pr.targets) == set(obs or ()), "committed set != observed at $%04X" % site
    w = S.Walker(model)
    assert w.run(frames) == ev.wlog  # play-phase log from the post-init image
    assert bytes(w.m) == ev.end_mem
    return model


@pytest.mark.parametrize("p", _PLAYERS, ids=_IDS)
def test_fuzz_walker_bit_exact(p):
    """Development aid (acceptance is the real-tune gate): every idiom class
    replays with identical cycle-stamped log, end memory, and end registers."""
    _verify(_image(p), _init(p), p.org, p.frames)


def test_opcode_byte_outside_observed_set_faults():
    """Observed-primary: the committed dispatch set IS the observed opcode set;
    any other byte at the cell faults in the walker."""
    p = next(q for q in _PLAYERS if q.name == "smc_opcode")
    model, _ev = S.decompile(_image(p), _init(p), p.org, p.frames)
    (site,) = {pc for pc in model.dispatch_pcs if pc >= p.org}
    assert model.dispatch_sets[site] == model.pcs[site]
    m = bytearray(model.mem0)
    m[site] = 0x02  # JAM: never observed at the cell
    with pytest.raises(S.WalkError):
        model.lookup(site, m)


def test_collect_unreachable_drops_residue_blocks_and_site_records():
    """Blocks outside the final-closure reachable set are dropped along with
    their stale dyn_targets/proofs entries; replay is untouched."""
    p = next(q for q in _PLAYERS if q.name == "jump_table")
    model, ev = S.decompile(_image(p), _init(p), p.org, p.frames)
    baseline = set(model.blocks)
    model.build(0x0F00, 0x60)  # init RTS: never reachable from play
    model.dyn_targets[0x0F00] = []
    model.proofs[0x0F00] = S.Proof(0x0F00, "jump", "observed", (), "residue")
    S.collect_unreachable(model)
    assert (0x0F00, 0x60) not in model.blocks and not model.variants(0x0F00)
    assert 0x0F00 not in model.dyn_targets and 0x0F00 not in model.proofs
    assert set(model.blocks) == baseline
    w = S.Walker(model)
    assert w.run(p.frames) == ev.wlog


def test_dynamic_jump_onto_fallthrough_is_observed():
    """A patched JMP landing exactly on the next instruction is an observed
    edge (recorded despite equalling fallthrough): the committed set carries
    it and the walker replays without a guard fault."""
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60
    code = [0xA9, 0x0A, 0x8D, 0x08, 0x10, 0xA9, 0x41]  # patch JMP operand lo
    code += [0x4C, 0x0A, 0x10]  # $1007: JMP $100A == fallthrough
    code += [0x8D, 0x00, 0xD4, 0x60]  # $100A: STA $D400; RTS
    mem[0x1000 : 0x1000 + len(code)] = bytes(code)
    model, ev = S.decompile(mem, 0x0F00, 0x1000, 3)
    assert 0x100A in model.ev_targets[0x1007] and 0x100A in model.dyn_targets[0x1007]
    w = S.Walker(model)
    assert w.run(3) == ev.wlog


def test_dynamic_branch_onto_fallthrough_replays():
    """A patched branch whose displacement resolves to zero lands on its own
    fallthrough — a static edge, so the dynamic-target guard admits it."""
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60
    code = [0xA9, 0x00, 0x8D, 0x08, 0x10, 0xA9, 0x01, 0xD0, 0x00]  # BNE +0 (patched)
    code += [0x8D, 0x00, 0xD4, 0x60]
    mem[0x1000 : 0x1000 + len(code)] = bytes(code)
    model, ev = S.decompile(mem, 0x0F00, 0x1000, 3)
    w = S.Walker(model)
    assert w.run(3) == ev.wlog


def _two_depth_image(dispatch):
    """One routine entered at two stack depths: one call deep through a static
    ``JMP``, two deep through ``dispatch`` (``rts`` push-and-return, or a ``jmp``
    whose operand another block patches). Returns ``(image, labels)``."""
    a = G.Asm(0x1000)
    a.i("INC", "zp", 0x02).i("LDA", "zp", 0x02).i("AND", "imm", 1)
    a.i("BEQ", "rel", ("L", "pathB")).i("JSR", "abs", ("L", "midA")).i("RTS")
    a.label("pathB").i("JSR", "abs", ("L", "midB")).i("RTS")
    a.label("midA").i("JMP", "abs", ("L", "shared"))
    a.label("midB")
    if dispatch == "rts":
        a.i("LDA", "imm", ("HIL", "callR", -1)).i("PHA")
        a.i("LDA", "imm", ("LOL", "callR", -1)).i("PHA").i("RTS")
    else:
        a.i("LDX", "imm", 0)
        a.i("LDA", "imm", ("LOL", "callR")).i("STA", "absx", ("L", "disp", 1))
        a.i("LDA", "imm", ("HIL", "callR")).i("STA", "absx", ("L", "disp", 2))
        a.label("disp").i("JMP", "abs", 0)
    a.label("callR").i("JSR", "abs", ("L", "shared")).i("RTS")
    a.label("shared").i("LDA", "zp", 0x02).i("PHA").i("LDA", "imm", 0x55)
    a.i("STA", "abs", 0xD401).i("PLA").i("STA", "abs", 0xD400).i("RTS")
    prog = a.assemble()
    mem = bytearray(0x10000)
    mem[0x1000 : 0x1000 + len(prog)] = prog
    mem[0x0F00] = 0x60  # init: RTS
    return mem, a.labels


@pytest.mark.parametrize("dispatch", ("rts", "jmp"))
def test_shared_routine_entered_at_two_stack_depths(dispatch, monkeypatch):
    """SP-constant flow runs over the committed successor relation, so a site
    whose static set is unavailable still contributes its observed targets. Miss
    that edge and the routine's push/pull cells fold at one depth only: the
    walker's stack writes land on a return address and it leaves the model."""
    mem, labels = _two_depth_image(dispatch)
    if dispatch == "jmp":  # static resolution succeeds here; force the refusal
        real, site = S.Analysis.term_targets, labels["disp"]

        def refuse(self, blk):
            if blk.pcs[-1] == site:
                raise S.DecompileError("forced refusal at $%04X" % site)
            return real(self, blk)

        monkeypatch.setattr(S.Analysis, "term_targets", refuse)
    model, ev = S.decompile(mem, 0x0F00, 0x1000, 8)
    key = (labels["shared"], mem[labels["shared"]])
    assert model.analysis.sp_in[key] == "bot", "two entry depths must defeat concretization"
    w = S.Walker(model)
    assert w.run(8) == ev.wlog and bytes(w.m) == ev.end_mem


def test_cia_icr_read_modeled_as_zero_source():
    """$DC0D reads are constant-0 under the per-frame driver, exactly as in
    PcodeVM; the decompiled model replays them rather than refusing."""
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60  # init: RTS
    mem[0x1000:0x1006] = bytes((0xAD, 0x0D, 0xDC, 0x8D, 0x00, 0xD4))  # LDA $DC0D; STA $D400
    mem[0x1006] = 0x60
    model, ev = S.decompile(mem, 0x0F00, 0x1000, 2)
    w = S.Walker(model)
    assert w.run(2) == ev.wlog


# ---- paired-index lemma (cross-block single-writer-pair) -----------------------
def _paired_base():
    """Dispatch skeleton: SMC'd ``JMP`` at $1020 (operands $1021/$1022), five
    handlers, split lo/hi tables at $1100/$1108."""
    m = bytearray(0x10000)
    m[0x0F00] = 0x60
    for k in range(5):
        m[0x1030 + 8 * k : 0x1036 + 8 * k] = bytes((0xA9, 0x11 * (k + 1), 0x8D, k, 0xD4, 0x60))
    m[0x1020:0x1023] = bytes((0x4C, 0x30, 0x10))
    m[0x1100:0x1108] = bytes((0x30, 0x38, 0x40, 0x48) * 2)
    m[0x1108:0x1110] = bytes((0x10,) * 8)
    return m


def _paired_verify(m, frames):
    model, ev = S.decompile(m, 0x0F00, 0x1000, frames)
    w = S.Walker(model)
    assert w.run(frames) == ev.wlog and bytes(w.m) == ev.end_mem
    return model


def test_paired_cross_block_writer_pair_proves():
    """Operand stores in a predecessor block of the dispatch block: the paired
    lemma zips the tables over the index domain (4 targets, not 16)."""
    m = _paired_base()
    code = [0xE6, 0xF0, 0xA5, 0xF0, 0x29, 0x03, 0xAA, 0xBD, 0x00, 0x11, 0x8D, 0x21, 0x10]
    code += [0xBD, 0x08, 0x11, 0x8D, 0x22, 0x10, 0xA5, 0xF0, 0x29, 0x01, 0xF0, 0x07]
    code += [0x4C, 0x20, 0x10]
    m[0x1000 : 0x1000 + len(code)] = bytes(code)
    model = _paired_verify(m, 12)
    pr = model.proofs[0x1020]
    assert pr.status == "certified" and pr.lemma.startswith("paired-index")
    assert set(pr.targets) == {0x1030, 0x1038, 0x1040, 0x1048}


def test_paired_unpaired_stores_refuse():
    """lo and hi stored in different blocks: the pairing premise refuses with
    the per-site diagnostic (the site falls back to per-cell closure)."""
    m = _paired_base()
    code = [0xE6, 0xF0, 0xA5, 0xF0, 0x29, 0x03, 0xAA, 0xBD, 0x00, 0x11, 0x8D, 0x21, 0x10]
    code += [0xA5, 0xF0, 0x29, 0x01, 0xF0, 0x00, 0xBD, 0x08, 0x11, 0x8D, 0x22, 0x10]
    code += [0x4C, 0x20, 0x10]
    m[0x1000 : 0x1000 + len(code)] = bytes(code)
    model = _paired_verify(m, 12)
    assert "stores $1021 but not $1022" in model.analysis.pair_refusals[0x1020]
    assert not model.proofs[0x1020].lemma.startswith("paired-index")
    assert set(model.proofs[0x1020].targets) == set(model.ev_targets[0x1020])


def test_paired_mutable_table_spill_refuses():
    """A value-preserving self-write makes a table cell mutable: the
    immutability gate refuses rather than reading through model.written."""
    m = _paired_base()
    code = [0xE6, 0xF0, 0xAD, 0x03, 0x11, 0x8D, 0x03, 0x11, 0xA5, 0xF0, 0x29, 0x03, 0xAA]
    code += [0xBD, 0x00, 0x11, 0x8D, 0x21, 0x10, 0xBD, 0x08, 0x11, 0x8D, 0x22, 0x10]
    code += [0xF0, 0x05, 0x4C, 0x20, 0x10]
    m[0x1000 : 0x1000 + len(code)] = bytes(code)
    model = _paired_verify(m, 12)
    assert "table read hits mutable/IO cell $1103" in model.analysis.pair_refusals[0x1020]
    assert not model.proofs[0x1020].lemma.startswith("paired-index")


def test_paired_constant_pair_and_indexed_mix_proves():
    """One writer stores a constant pair, another an indexed pair: both satisfy
    the premise and the target set is their union."""
    m = _paired_base()
    code = [0xE6, 0xF0, 0xA5, 0xF0, 0x29, 0x01, 0xF0, 0x58, 0xA5, 0xF0, 0x4A, 0x29, 0x03]
    code += [0xAA, 0xBD, 0x00, 0x11, 0x8D, 0x21, 0x10, 0xBD, 0x08, 0x11, 0x8D, 0x22, 0x10]
    code += [0x4C, 0x20, 0x10]
    m[0x1000 : 0x1000 + len(code)] = bytes(code)
    arm = [0xA9, 0x50, 0x8D, 0x21, 0x10, 0xA9, 0x10, 0x8D, 0x22, 0x10, 0x4C, 0x20, 0x10]
    m[0x1060 : 0x1060 + len(arm)] = bytes(arm)
    model = _paired_verify(m, 16)
    pr = model.proofs[0x1020]
    assert pr.status == "certified" and pr.lemma.startswith("paired-index")
    assert set(pr.targets) == {0x1030, 0x1038, 0x1040, 0x1048, 0x1050}


@pytest.mark.parametrize(
    "vec,kernal", [(0x0314, True), (0xFFFE, False), (0x0318, False)], ids=["cinv", "hw", "nmi"]
)
def test_handler_driven_entry_replays_bit_exact(vec, kernal):
    """``play == 0``: the installed handler IS the frame entry and its RTI the
    boundary -- the convention's frame is pushed, never lifted (both executors agree)."""
    mem, init = G.irq_image(vec, kernal)
    model = _verify(mem, init, 0, 8, img=G.IRQ_IMAGE)
    assert model.play == G.IRQ_HANDLER
    assert model.play_frame == (vm.KERNAL_FRAME if kernal else vm.IRQ_FRAME)
    assert [v for _c, _r, v in S.Walker(model).run(8)] == list(range(1, 9))
    assert not [blk for blk in model.blocks.values() if blk.term[0] == "jmpd"]
    rti = next(blk for blk in model.blocks.values() if blk.term == ("rts", 3))
    assert rti.pcs[-1] in model.pcs


def test_play_zero_without_an_installed_vector_refuses():
    """No vector, no per-frame entry: the diagnostic names the vectors, not $0000."""
    mem = bytearray(0x10000)
    mem[0x0F00] = 0x60  # init: RTS, installing nothing
    with pytest.raises(S.DecompileError, match="no interrupt vector"):
        S.decompile(mem, 0x0F00, 0, 4)


def test_play_zero_init_that_never_returns_names_the_driver_cadence(monkeypatch):
    """The known deferred class: init idles until its own handler fires."""
    monkeypatch.setattr(S, "_GUARD", 1000)
    mem = bytearray(0x10000)
    mem[0x0F00:0x0F03] = bytes((0x4C, 0x00, 0x0F))  # init: JMP * (idle loop)
    with pytest.raises(RuntimeError, match="init never returned"):
        S.decompile(mem, 0x0F00, 0, 4)


def test_play_zero_with_a_zeroed_vector_refuses():
    mem, init = G.irq_image(handler=0x0000)
    with pytest.raises(S.DecompileError, match=r"vector \$0314 installed as \$0000"):
        S.decompile(mem, init, 0, 4, img=G.IRQ_IMAGE)


def test_load_image_over_the_kernal_epilogue_refuses():
    """The tune's own bytes live under the epilogue: refuse rather than fake them."""
    mem, init = G.irq_image()
    with pytest.raises(S.DecompileError, match=r"epilogue at \$EA31"):
        S.decompile(mem, init, 0, 4, img=(0xEA00, 0xEB00))


def _tunes():
    return [pytest.param(path, sub, secs, id=path.stem) for path, sub, secs in corpus_params(HVSC)]


@pytest.mark.oracle
def test_two_depth_routine_real_tune():
    """The tune that found the SP-flow edge-model hole. Outside the composer
    round-robin of ``_corpus.CORPUS``, so keyed by relpath: Gate C's population
    is a sample, and a sample cannot be evidence about a tune it omits."""
    resolve_tune = pytest.importorskip("pysidtracker.testing").resolve_tune
    path = resolve_tune("MUSICIANS/A/Abbott_Chris/Bangkok.sid", cache_dir=HVSC)
    if path is None:
        pytest.skip("tune unavailable")
    mem, _load, init, play = load_psid(Path(path).read_bytes())
    mem[0xD418] = 0x0F
    _verify(mem, init, play, 300, 1)


@pytest.mark.parametrize("sid,subtune,secs", _tunes())
def test_real_tune_full_length_cycle_exact(sid, subtune, secs):
    """Model-level acceptance: bit-exact full-length log from the walker.
    Artifact round-trip and Gate FP live on the frame program (test_frameprog)."""
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    frames = int(secs * 50)
    model = _verify(mem, init, play, frames, subtune)
    assert model.dispatch_sets is not None


def _arm_labels(stmts, out):
    """Every switch-arm label the frame program serializes, as ints."""
    for s in stmts:
        if s[0] == "swg":
            out.update(int(lbl[1:], 16) for lbl, _b in s[1])
            for _lbl, body in s[1]:
                _arm_labels(body, out)
        elif s[0] == "swc":
            out.update(int(lbl[1:], 16) for lbl in s[1])
            out.update(int(lbl[1:], 16) for lbl, _b in s[2])
            for _lbl, body in s[2]:
                _arm_labels(body, out)
        elif s[0] == "opsw":
            out.update(int(lbl[1:], 16) for lbl, _b in s[2])
            for _lbl, body in s[2]:
                _arm_labels(body, out)
        elif s[0] == "if":
            _arm_labels(s[3], out)
            _arm_labels(s[4], out)
        elif s[0] == "loop":
            _arm_labels(s[1], out)
        elif s[0] == "for":
            _arm_labels(s[4], out)
        elif s[0] == "callb":
            _arm_labels(s[3], out)
    return out


def test_guard_live_dispatch_serializes_its_observed_set_and_nothing_else():
    """A computed-dispatch site static analysis cannot certify serializes its
    observed set as switch arms; any other target faults in the evaluator
    (test_frameval::test_switch_arm_outside_the_observed_set_faults)."""
    entry = next((t for t in corpus_params(HVSC) if t[0].stem == "Bionic_Commando"), None)
    if entry is None:
        pytest.skip("corpus tune absent")
    sid, sub, secs = entry
    mem, _load, init, play = load_psid(sid.read_bytes())
    mem[0xD418] = 0x0F
    model, _ev = S.decompile(mem, init, play, secs * 50, sub)
    live = {s: p for s, p in model.proofs.items() if p.status == "observed"}
    assert live, "expected at least one guard-live site"
    arms = set()
    for _e, _p, _r, stmts in frameprog.program(model).procs:
        _arm_labels(stmts, arms)
    for site, pr in live.items():
        assert set(pr.targets) <= arms, "$%04X serializes fewer arms than it observed" % site
