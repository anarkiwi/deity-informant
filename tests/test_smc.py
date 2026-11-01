"""SMC scenario framework: space derivation, feasibility, completeness and lift outcomes."""

import json
import sys
from pathlib import Path

import pytest

from jennings.memory import NATIVE as JENNINGS_NATIVE

from deity_informant import NATIVE
from deity_informant.lifter import OPS
from deity_informant.smc import feasible, generate, machine, space, synthm
from deity_informant.smc.expect import (
    GAP_REASONS,
    LIFT_REASONS,
    feasible_sids,
    gap_reason,
    lift_reason,
)
from deity_informant.smc.outcomes import classify

HERE = Path(__file__).resolve().parent
CORPUS = json.loads((HERE / "smc_corpus.json").read_text(encoding="utf-8"))
EXPECT = json.loads((HERE / "smc_expect.json").read_text(encoding="utf-8"))
BYSID = {c.sid: c for c in space.cells()}
EXACT = ("exact_nodeopt", "exact_deopt")

native = pytest.mark.skipif(not NATIVE, reason="needs the native extension")
cpu_native = pytest.mark.skipif(not JENNINGS_NATIVE, reason="needs jennings' native core")
has_machine = pytest.mark.skipif(not machine.core(), reason="needs vagrant-moses' native machine")


def _program(rec):
    return generate.Program(rec["name"], rec["source"], rec["entries"], rec["calls"], rec["init"])


def _covered():
    out = {}
    for rec in CORPUS:
        for sid in rec["cells"]:
            out.setdefault(sid, set()).add(rec["outcome"])
    return out


COVERED = _covered()
COVERED_EXACT = {sid for sid, outs in COVERED.items() if outs <= set(EXACT)}
CPU_CORPUS = [r for r in CORPUS if r["oracle"] == "cpu"]
MACHINE_CORPUS = {r["name"]: r["cells"] for r in CORPUS if r["oracle"] == "machine"}


# ---- (i) axes cover every template LOAD/STORE and device port ----------------
def test_every_opcode_store_classifies():
    for op in OPS:
        sc = space.store_channel(op)
        if sc is not None:
            assert sc[0] in space.CHANNELS, (hex(op), sc)


def test_every_opcode_load_classifies():
    for op in OPS:
        for rc in space.load_class(op):
            assert rc in space.READ_CLASSES, (hex(op), rc)


def test_writer_channels_and_device_ports_present():
    writers = {w for w, _, _ in space.writer_channels()}
    assert {"cpu", "cpu_port", "irq_push", "nmi_push", "brk_push", "reu_dma"} <= writers
    assert set(space.REU_AXES) == {"transfer", "c64_addr", "reu_addr", "autoload", "ff00_trigger"}
    assert space.REU_AXES["transfer"] == ("to_reu", "from_reu", "swap", "verify")


def test_space_product_is_deterministic():
    assert [c.sid for c in space.cells()] == [c.sid for c in space.cells()]


# ---- feasibility: every cell decided, each refusal proven --------------------
def test_every_cell_decided_with_proof():
    for c in space.cells():
        v = feasible.decide(c)
        assert v.feasible or v.rule
    rules = {rule for _, rule in feasible.refuted_cells(space.cells())}
    assert {"disjoint-address", "unbankable-role", "interrupt-is-async"} <= rules


# ---- (ii) every feasible cell witnessed or carries a lift/gap reason ----------
def test_expectation_table_matches_space():
    assert set(EXPECT) == feasible_sids()


def test_feasible_cells_witnessed_or_explained():
    bad = []
    for sid in feasible_sids():
        rec = EXPECT[sid]
        cell = BYSID[sid]
        reason = lift_reason(cell)
        if reason is not None:
            if rec["lift"] != reason:
                bad.append((sid, "lift", rec["lift"], reason))
        elif rec["witnessed"]:
            if rec["lift"] not in EXACT:
                bad.append((sid, "witnessed-not-exact", rec["lift"]))
        elif rec["lift"] != "placement-not-synthesized":
            bad.append((sid, "unwitnessed-flat", rec["lift"]))
        if not rec["witnessed"] and gap_reason(cell, False) not in GAP_REASONS:
            bad.append((sid, "gap", rec["gap"]))
    assert not bad, bad[:10]


def test_lift_reasons_are_known():
    for rec in EXPECT.values():
        assert rec["lift"] in EXACT or rec["lift"] in LIFT_REASONS + ("placement-not-synthesized",)


def test_witnessed_cells_have_a_corpus_program():
    for sid, rec in EXPECT.items():
        if rec["witnessed"]:
            assert sid in COVERED, sid


# ---- (iii) classifier closure: fuzzed programs produce only feasible cells ----
@cpu_native
def test_classifier_closure_on_fuzz():
    feas = feasible_sids()
    seen = 0
    for prog in generate.fuzz(seed=7, rounds=40):
        for cell in prog.cells:
            assert cell.channel in space.CHANNELS
            assert cell.sid in feas, cell.sid
            seen += 1
    assert seen


# ---- the synthesizer emits only feasible, oracle-confirmed cells -------------
@cpu_native
def test_synthesized_witnesses_are_feasible():
    feas = feasible_sids()
    progs = generate.synthesized()
    assert progs
    for prog in progs:
        assert prog.sids, prog.name
        assert prog.sids <= feas, (prog.name, sorted(prog.sids - feas))


@cpu_native
def test_targeted_witnesses_are_feasible():
    feas = feasible_sids()
    for prog in generate.targeted():
        assert prog.sids <= feas, prog.name


@cpu_native
def test_synthesizer_covers_its_domain():
    covered = set()
    for prog in generate.synthesized() + generate.targeted():
        covered |= prog.sids
    domain = {
        c.sid
        for c in space.cells()
        if feasible.decide(c).feasible
        and c.writer == "cpu"
        and c.mapping == "flat"
        and c.role in ("opcode", "operand", "ptr_indirect", "ret_addr")
        and c.timing in ("pre_lift", "earlier_call", "same_call_earlier", "same_insn")
    }
    assert len(covered & domain) >= 100


# ---- machine-level witnesses label and classify to lift gaps -----------------
@has_machine
def test_machine_programs_classify_to_lift_gap():
    progs = generate.machine()
    assert progs
    for prog in progs:
        assert prog.oracle == "machine" and prog.sids
        outcome, _ = classify(prog)
        assert outcome in LIFT_REASONS, (prog.name, outcome)


@has_machine
def test_machine_oracle_sees_banking_and_interrupts():
    covered = set()
    for prog in generate.machine():
        covered |= {(BYSID[s].mapping, BYSID[s].writer, BYSID[s].timing) for s in prog.sids}
    mappings = {m for m, _, _ in covered}
    assert {"ram_under_rom", "rom_vs_ram"} <= mappings
    assert any(w in ("irq_push", "nmi_push") for _, w, _ in covered)
    assert any(t == "async" for _, _, t in covered)


# ---- (iv) generation fixpoint: fresh fuzzing adds no new coverage -------------
@cpu_native
def test_generation_reaches_fixpoint():
    corpus_cov = {sid for rec in CORPUS for sid in rec["cells"]}
    fresh = set()
    for seed in (11, 22, 33):
        for prog in generate.fuzz(seed=seed, rounds=60):
            fresh |= prog.sids
    assert fresh <= corpus_cov, sorted(fresh - corpus_cov)


# ---- the CPU oracle is independent of the lifter under test -------------------
@cpu_native
def test_cpu_oracle_uses_jennings_not_pcode():
    import deity_informant.smc.oracle as oracle_mod

    src = oracle_mod.__file__
    text = Path(src).read_text(encoding="utf-8")
    assert "NativeMPU" in text and "PcodeVM" not in text and "wholeprog" not in text


# ---- oracle labelling is stable ----------------------------------------------
@cpu_native
@pytest.mark.parametrize("rec", CPU_CORPUS, ids=[r["name"] for r in CPU_CORPUS])
def test_corpus_labels_reproduce(rec):
    assert sorted(_program(rec).sids) == rec["cells"]


@has_machine
def test_machine_witnesses_reproduce():
    feas = feasible_sids()
    for wit in synthm.witnesses():
        run = wit.run()
        assert run.returned, wit.name
        assert not run.unclassified, (wit.name, run.unclassified[:3])
        assert {c.sid for c in run.cells} <= feas, wit.name
        assert sorted(c.sid for c in run.cells) == MACHINE_CORPUS["machine." + wit.name]


# ---- lift outcomes over the CPU corpus ---------------------------------------
@native
@pytest.mark.parametrize("rec", CPU_CORPUS, ids=[r["name"] for r in CPU_CORPUS])
def test_lift_outcome_matches_table(rec):
    outcome, _deopts = classify(_program(rec))
    assert outcome == rec["outcome"] and outcome in EXACT


# ---- strict-xfail: a machine or placement gap XPASSes once the lifter grows ----
def _gap_params():
    params = []
    for sid in sorted(feasible_sids()):
        rec = EXPECT[sid]
        if rec["lift"] in EXACT:
            params.append(sid)
        else:
            params.append(
                pytest.param(sid, marks=pytest.mark.xfail(strict=True, reason=rec["lift"]))
            )
    return params


@pytest.mark.parametrize("sid", _gap_params())
def test_cell_lifts_exactly(sid):
    assert sid in COVERED_EXACT


@cpu_native
def test_generate_and_coverage_helpers():
    kept, covered, feas = generate.generate(seed=3, fuzz_rounds=20)
    assert kept and covered <= feas
    cov2, feas2 = generate.coverage(kept)
    assert cov2 == covered and feas2 == feas


# ---- classify primitives (pure Python, no oracle needed) ---------------------
def test_view_banking():
    from deity_informant.smc.classify import view

    assert view(0x37, 0xE010) == "rom" and view(0x35, 0xE010) == "ram"
    assert view(0x37, 0xD010) == "io" and view(0x33, 0xD010) == "rom"
    assert view(0x37, 0x1000) == "ram"


def test_insn_reads_roles():
    from deity_informant.smc.classify import insn_reads

    mem = bytearray(0x10000)
    mem[0x1000:0x1003] = bytes([0x6C, 0x34, 0x12])  # JMP ($1234)
    roles = dict((a, r) for a, r in insn_reads(mem, 0x1000, 0, 0xFF))
    assert roles[0x1000] == "opcode" and roles[0x1234] == "ptr_indirect"
    mem[0x2000] = 0x60  # RTS
    rr = insn_reads(mem, 0x2000, 0, 0xFC)
    assert any(role == "ret_addr" for _, role in rr)


def test_expected_writes_forms():
    from deity_informant.smc.classify import expected_writes

    mem = bytearray(0x10000)
    mem[0x1000:0x1003] = bytes([0x9D, 0x00, 0x20])  # STA $2000,X
    assert expected_writes(mem, 0x1000, 5, 0, 0xFF) == {0x2005}
    mem[0x1000] = 0x48  # PHA
    assert 0x1FF in expected_writes(mem, 0x1000, 0, 0, 0xFF)


def test_classifier_timing_and_pre_lift():
    from deity_informant.smc.classify import Classifier

    base = bytearray(0x10000)
    cls = Classifier(base)
    cls.write(0x1001, "cpu", "abs", False, 0x1000)  # pre-lift write (call = -1)
    cls.start_calls()
    cls.fetch(0x1001, 0x55, "operand", 0x1000)
    assert any(c.timing == "pre_lift" for c in cls.cells)
    cls.write(0x1002, "cpu", "abs", False, 0x2000)
    cls.fetch(0x1002, 0x66, "opcode", 0x1000)
    assert any(c.timing == "same_call_earlier" for c in cls.cells)


def test_machine_mem_overlay_and_core_lookup():
    from deity_informant.smc.machine import _Mem, core

    ram = bytearray(0x10000)
    ram[0x2000] = 0x11
    mem = _Mem(0x1000, [0xA9, 0x42, 0xEA], ram)
    assert mem[0x1000] == 0xA9 and mem[0x1001] == 0x42 and mem[0x2000] == 0x11
    assert isinstance(core(), bool)


def test_back_to_back_interrupt_frames_stack():
    from deity_informant.smc.machine import _frames

    assert _frames(["cia_irq", "cia_irq_entry", "nmi_entry"], 0xF0) == [
        ("irq", 0xF3),
        ("nmi", 0xF0),
    ]
    assert _frames(["rti"], 0xF0) == []


def test_smcgen_refuses_without_machine(monkeypatch, tmp_path):
    """Regenerating without the machine oracle would silently drop its witnesses."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import smcgen  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(smcgen.machine, "core", lambda: False)
    with pytest.raises(SystemExit):
        smcgen.main(["--out-dir", str(tmp_path)])
    assert not list(tmp_path.iterdir())
