"""The canonical end-to-end example, gated (docs/register-model-lift-impl.md).

The plan's stages MUST keep this pipeline green: playroutine -> decompile ->
minimize -> Z3-proved wide folds -> role-typed state machine -> frame oracles.
Pending shapes are ``xfail(strict=True)``, named by the stage that flips them."""

import os
import re
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from deity_informant import roles
from examples import state_machine_lift as sml
from examples.state_machine_lift import (
    CHI,
    CLO,
    CUT,
    DLO,
    FOLDS,
    FRAMES,
    FRAME_S,
    LP,
    NLO,
    PLAY,
    SCRIPTS,
    SHADOW,
    TEST_BIT,
    VOICES,
    VOL,
    WAVEF,
    ZBUF,
    ZLO,
    ZPV,
    adsr_before_gate,
    change_stream,
    classify_roles,
    declared_roles,
    dead_local_defs,
    extract_proc,
    framelog,
    grids_from_writes,
    image_end,
    lane_updates,
    minimized_wav,
    note_starts,
    oscillator_reset_frames,
    pipeline,
    pretty,
    proc_entries,
    render,
    restart_shape,
    sid_pairs,
    sidplayfp_wav,
    sidtrace_stream,
    to_psid,
    voice_skeleton,
    wav_frames,
)

XFAIL = {"strict": True}


@pytest.fixture(scope="module", name="art")
def _art():
    return pipeline()


@pytest.fixture(scope="module", name="text")
def _text(art):
    """The artifact: the re-rolled program, its roles read off the unrolled one."""
    return render(art["rolled"], classify_roles(art["folded"]))


@pytest.fixture(scope="module", name="min_grids")
def _min_grids(art):
    return grids_from_writes(art["init_writes"], art["min_frames"])


def _line(text, needle):
    return next(ln for ln in text.splitlines() if needle in ln)


def test_folds_all_proved(art):
    kinds = {p.split("(")[0] for p in art["proofs"]}
    assert kinds == set(FOLDS)
    got = set(art["proofs"])
    for b in ZPV:  # every voice folds on its own cursor, note pair and slide
        assert "pair_set(ptr_%04X)" % b in got
        assert "pair_store(zp_%02X,zp_%02X)" % (b + CLO, b + CHI) in got
        assert "advance(ptr_%04X,+2,nocarry)" % b in got
        assert "wide_cmp(zp_%02X<zp_%02X)" % (b + NLO, b + CLO) in got
        assert {"+self", "-self", "=pair"} <= {
            p.split(",")[1][:-1] for p in got if p.startswith("wide16(zp_%02X," % (b + CLO))
        }
        assert "wide16(zp_%02X,-pair)" % (b + DLO) in got
    assert any(p.endswith(",wide)") for p in got), "no observed page cross to fold"


def test_shadow_forwards_off_the_sid_path(art, text):
    """The RAM SID shadow is looked through: no sink reads it back.

    Stage 3c's memory price does this in the emitter, so the example's own
    ``forward_shadow`` fold is retired: the sinks arrive spelled from the cells."""
    shadow = re.compile(r"m_0[0-9A-F]{3}")
    assert not [n for n in shadow.findall(text) if SHADOW <= int(n[2:], 16) < SHADOW + 7 * VOICES]
    for v in ("voice", "v%d" % VOICES):  # the unified slice, then the voice that kept its copy
        assert "sid.%s.ctrl = %s_ctl" % (v, v) in text
        assert "sid.%s.attack_decay = %s_ad" % (v, v) in text
    eq = art["eqlift_text"]
    assert [ln for ln in eq.splitlines() if "m_034" in ln and "sid." in ln] == []
    assert [ln for ln in eq.splitlines() if "m_034" in ln.split("=")[0]], "no shadow store at all"


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


def test_roles_are_the_plan_s_own_and_the_field_line_is_the_dialect_s(art, text):
    """The example's roles are ``roles.ROLES`` and it spells them as the grammar does."""
    got = classify_roles(art["folded"])
    assert set(got.values()) <= set(roles.ROLES)
    by_voice = {pretty(n): r for n, r in declared_roles(art["folded"], got).items()}
    for v in range(1, VOICES + 1):
        assert by_voice["v%d_pos" % v] == "cursor"
        assert by_voice["v%d_dur" % v] == "counter"
        assert by_voice["v%d_phase" % v] == "accumulator"
        assert by_voice["v%d_pitch" % v] == "accumulator"
        assert by_voice["v%d_ctl" % v] == "flags"
        fields = ("note", "vib", "ad", "sr", "wave", "slide")
        assert {by_voice["v%d_%s" % (v, f)] for f in fields} == {"parameter"}
        assert "v%d_pos: cursor u16" % v in text and "v%d_dur: counter u8" % v in text
    for v in ("voice", "v%d" % VOICES):  # the state block declares each voice; the body
        assert "%s_pos:u16 += 2" % v in text and "sid.%s.freq:u16" % v in text


def test_independent_engine_grid(art):
    oracle_mod = pytest.importorskip("pysidtracker.oracle")
    psid = to_psid(art["mem"], image_end(art["labels"]))
    oracle = [g[:25] for g in oracle_mod.register_grid(psid, FRAMES)]
    assert oracle == art["orig_grids"]


def test_indexed_store_lives_in_a_branch_arm(text):
    """One arm writes a real span row; the pw pair crosses the join into the chip."""
    arms = [ln for ln in text.splitlines() if re.search(r"m_[0-9A-F]{4}\[log_idx\] = ", ln)]
    assert len(arms) == 1
    assert _line(text, "if (tick == $00)")
    assert "sid.v1.pw:u16" in text


def test_join_forwards_the_crossing_cell(art, text):
    """3b landing 2: the pw pair crosses the join as the value the head computed.

    The property lives in the emitter's text: the folded artifact resolves a forwarded
    lane back to the cell holding it (#148's pair rule, which is what writes all seven
    multi-byte registers wide), so both spellings fold to the same wide store."""
    eq = art["eqlift_text"]
    assert "ctr_0034" not in _line(eq, "sid.v1.pw_lo = "), "the sink re-reads the pair"
    assert "zp_35" not in _line(eq, "sid.v1.pw_hi = "), "the sink re-reads the pair"
    assert "sid.v1.pw:u16 = " in text


def test_wrapping_zero_page_row(art, text):
    """The zp-row idiom end to end, and no store forwards across the wrap."""
    assert ZBUF + ZLO > 0xFF, "the row's index arithmetic does not wrap"
    put = _line(text, "mem[zext2((row_put + $%02X))] = " % ZBUF)
    get = _line(text, "mem[zext2((row_get + $%02X))]" % ZBUF)
    assert put.endswith("row_src")
    assert re.fullmatch(r"\s*\w+ = mem\[zext2\(\(row_get \+ \$%02X\)\)\]" % ZBUF, get)
    assert "row_val = " in text, "the wrapped read reaches no state"
    ram = art["ram_end"]
    assert any(ram[ZBUF - 16 : ZBUF]) and any(ram[ZBUF : ZBUF + 16]), "one half never written"


def test_helper_procedure_and_its_boundary(art, text):
    """The JSR is a procedure of its own, and its returns resolve into the caller.

    3d landing 3: pass 2 still names the params and returns; the row read needs the
    lanes in one place, so the leaf callee is resolved at its sites and leaves."""
    sub = next(e for e in art["boundary"] if e != PLAY)
    assert art["boundary"][sub] == (("x",), ("a", "x"))
    assert art["boundary"][PLAY] == ((), ())
    assert art["resolved"] == (sub,)
    assert list(art["folded"]) == [PLAY] and "call sub_" not in text


def test_the_parser_reads_the_engine_s_own_artifact(art):
    """#193's four measured items: the signature, the width suffix, the promotion, trunc1.

    The subject is ``frameprog.dumps`` -- the text the engine emits, not the pre-rung
    dialect the prototype has read until now -- and every procedure of it parses."""
    text = art["artifact"]
    sigs = sml.proc_signatures(text)
    assert sigs == {PLAY: (("x",), ()), 0x1485: (("x",), ("a", "x"))}
    assert sml.proc_entries(text) == sorted(sigs)
    asts = {e: sml.extract_proc(text, e) for e in sigs}
    assert all(asts.values())
    assert asts[0x1485][-1] == ("ret", ("a", "x")), "the header's returns are not spelled"
    kinds = set()

    def walk(x):
        if isinstance(x, tuple) and x:
            kinds.add(x[0])
            if x[0] == "call":
                kinds.add(x[1])
        if isinstance(x, (tuple, list)):
            for kid in x:
                walk(kid)

    walk(list(asts.values()))
    assert {"pcall", "trunc1", "zext2"} <= kinds, sorted(kinds)


def _sites(asts):
    """``name -> {width}`` over every read site the parsed artifact spells."""
    out = {}

    def walk(x):
        if isinstance(x, tuple) and x and x[0] == "name":
            out.setdefault(x[1], set()).add(sml._wid(x))  # pylint: disable=protected-access
        if isinstance(x, (tuple, list)):
            for kid in x:
                walk(kid)

    walk(asts)
    return out


def test_a_width_belongs_to_the_site_and_not_to_the_name(art):
    """#193's blocker, measured: the artifact is width-typed and a name wears both.

    A name-keyed width registry cannot state this artifact, so the parser records the
    width the *site* spells. The store's width is its own value's, at every assignment."""
    text = art["artifact"]
    asts = [sml.extract_proc(text, e) for e in sml.proc_entries(text)]
    reads = _sites(asts)
    assert sorted(n for n, w in reads.items() if len(w) > 1) == [
        "zp_44",
        "zp_4D",
        "zp_64",
        "zp_6D",
        "zp_84",
        "zp_8D",
    ]
    both, mismatch = set(), []
    for line in (ln.strip() for ln in text.splitlines()):
        line = line[9:] if line.startswith("hi-first ") else line
        m = re.fullmatch(r"([\w.]+)(?::(\d))? = (.*)", line)
        if not m:
            continue
        want = int(m.group(2) or 1)
        if want != sml._wid(sml.parse_expr(m.group(3))):  # pylint: disable=protected-access
            mismatch.append(line)
        if want in reads.get(m.group(1), {want}) and len(reads.get(m.group(1), ())) > 1:
            both.add(m.group(1))
        reads.setdefault(m.group(1), set()).add(want)
    assert not mismatch, "a store's width is not the width of the value it stores"
    assert len(sorted(n for n, w in reads.items() if len(w) > 1)) == 15


def test_the_prototype_evaluates_a_promoted_call_off_the_header(art):
    """The promotion executes by its header: the params bind and only the returns land."""
    text = art["artifact"]
    sigs = sml.proc_signatures(text)
    body = sml.extract_proc(text, 0x1485)
    call = ("pcall", ("a", "x"), 0x1485, (("name", "x", 1),))
    flats = {PLAY: sml.Flat([call]), 0x1485: sml.Flat(body)}
    machine = sml.Machine(flats, sml.run_vm(art["mem"], 0)[1], sigs)
    env = {"x": 5, "y": 9}
    machine._run(PLAY, env)  # pylint: disable=protected-access
    ram = machine.ram
    assert (env["a"], env["x"]) == (ram[0x14A7 + 5], ram[0x14BD + 5])
    assert env["y"] == 9, "the promotion defined a register its header does not return"


def test_stack_spill_forwards(text):
    """3d landing 1: the pull is spelled from the pushed value and the store demotes.

    The reader set is artifact-wide, so the slot dies only once the script derefs are
    bounded -- 2b's observed extents are what bound them, consumed and never extended."""
    assert not re.search(r"m_01[0-9A-F]{2}", text), "the PHA/PLA spill survives as a cell"


def test_portamento_is_one_wide_compare(art, text):
    """The SBC/SBC chain and its guard both fold, each instance Z3-proved."""
    for v in ("voice", "v%d" % VOICES):
        assert "%s_diff:u16 = %s_note:u16 - %s_pitch:u16" % (v, v, v) in text
        assert "if (%s_note:u16 < %s_pitch:u16)" % (v, v) in text
        assert "%s_pitch:u16 += %s_slide:u8" % (v, v) in text
        assert "%s_pitch:u16 -= %s_slide:u8" % (v, v) in text
    freqs = {(g[1] << 8) | g[0] for g in art["orig_grids"]}
    assert len(freqs) > 40, "the slide is inaudible"


def test_carry_outlives_its_add(art, text):
    """The 24-bit accumulator folds; its carry-out is a value a later statement reads."""
    assert "wide24(zp_30,+self)" in set(art["proofs"])
    assert "; carry -> tick" in _line(text, "phase:u24 +=")
    assert "if (tick == $00)" in text
    assert len({g[3] for g in art["orig_grids"]}) > 4, "the pulse width never sweeps"


def test_filter_sweep_and_field_update_flags(art, text):
    """One voice routed through the filter; the flag cells wear the census's shape."""
    got = classify_roles(art["folded"])
    by_voice = {pretty(n): r for n, r in declared_roles(art["folded"], got).items()}
    assert by_voice["v3_res_route"] == "flags" and by_voice["v3_mode_vol"] == "flags"
    assert "filter.cutoff:u16" in text
    assert "v3_res_route = ((row_val & $70) | v3_res_route)" in text
    assert {g[24] for g in art["orig_grids"]} == {LP | VOL}
    for reg in (21, 22, 23):
        assert len({g[reg] for g in art["orig_grids"]}) > 4, "register %d never sweeps" % reg
    for writes in art["min_frames"]:  # the filter block is voice 3's tail, after its gate
        order = {r: k for k, (r, _v) in enumerate(writes)}
        assert order[23] > order[18] and order[24] > order[18]


def test_variable_arity_dispatch_operator(art, text):
    """The rawsid operator: (reg, val) pairs while reg < $80, then a terminator."""
    arities = {len(arg) // 2 for s in SCRIPTS for op, arg in s if op == "raw"}
    assert arities == {0, 1, 2}, "the operator's arity is not data-dependent"
    copies = 1 + VOICES - len(art["unify"]["unified"])  # the loop body plus the copies
    arms = re.findall(r"^\s*op \S+: \{$", text, re.M)  # the case headers, not the declarations
    assert len(arms) == 4 * copies, "a handler left the dispatch"
    assert text.count("sid.reg[a] = ") == copies, "the raw write is not a span store"
    assert len({g[17] for g in art["orig_grids"]}) > 1, "no raw command ran"


def test_voices_are_isomorphic_up_to_base_displacement(art):
    """Voices 1-2 are one shape at shifted bases; voice 3 is that shape plus a block."""
    play = art["folded"][PLAY]
    skel = [voice_skeleton(play, v) for v in range(VOICES)]
    core = [voice_skeleton(play, v, "core") for v in range(VOICES)]
    filt = [voice_skeleton(play, v, "filter") for v in range(VOICES)]
    assert skel[0] and skel[0] == skel[1]
    assert core[2] == core[0]
    assert filt[0] == filt[1] == []
    for reg in ("filter.cutoff", "filter.resonance", "filter.mode_vol"):
        assert any(reg in ln for ln in filt[2]), reg


def test_rerolling_unifies_the_isomorphic_voices(art, text):
    """3d landing 3: the total isomorphism is a loop; the near-miss keeps its copy.

    Voice 3 carries the filter block, so it refuses by shape and is not guarded into
    the loop -- a synthesized ``if voice == 3`` would fabricate structure."""
    assert "for voice in" in text, "the voices are still unrolled copies"
    got = art["unify"]
    assert got["unified"] == [1, 2] and got["voices"] == VOICES
    assert "block of" in got["refused"], "voice 3 unified against its own filter block"
    assert not re.search(r"if [^\n]*\bvoice\b", text), "a synthesized per-voice guard"


def test_multi_byte_sid_registers_are_written_wide(text):
    """Enumerated from the register map, so nothing multi-byte can be missed."""
    pairs = sid_pairs()
    assert len(pairs) == 2 * VOICES + 1
    for base in pairs:
        assert not re.search(r"\b%s_(?:lo|hi) = " % re.escape(base), text), base
    for base in ("sid.voice.freq", "sid.v3.freq", "sid.v1.pw", "filter.cutoff"):
        assert "%s:u16 = " % base in text, base


def test_declared_u16_state_is_updated_wide(art):
    """Every wide quantity is updated only at its own width, the cursors included."""
    assert [n for n, _a in lane_updates(art["folded"])] == []


def test_no_byte_lane_update_of_any_declared_u16(art):
    """FLIPPED at stage 4: the variable-stride advance folds like the constant one.

    ``_add_const`` reads the stride off an add chain reading the cell once, whatever
    the rest of the chain is, and ``prove_advance`` discharges the same obligation over
    a free byte -- so a table-valued step is the same rule as ``+2``."""
    assert lane_updates(art["folded"]) == []


def test_note_fetch_is_one_u16_row_read(art, text):
    """3d landing 3: the declared pair read at one index is one u16 row read.

    The lanes met once the leaf callee's returns resolved into the caller; the pair is
    the site's own declaration and the spelling is the image's own table label."""
    assert not re.search(r"v\d_note_(?:lo|hi) = ", text), "the note keeps split lanes"
    assert re.search(r"v\d_note = pitch\[", text), "the pitch table is still two rows"
    assert "voice_note = pitch[" in text, "the unified slice kept the split lanes"
    assert art["proofs"].count("row_read(m_14A7,m_14BD)") == VOICES


def test_the_loop_expands_to_the_program_it_rolled(art):
    """§6: the rendered loop is executed, and its meaning is the copies it replaced.

    Every leaf difference is bound, so expanding the loop reproduces the folded program
    exactly -- except at the observed-guard sites, which carry their Z3 proofs."""
    got, want = sml.expand(art["rolled"]), art["folded"]
    assert sorted(got) == sorted(want)
    diffs = []

    def walk(x, y, at):
        if type(x) is not type(y) or (isinstance(x, (list, tuple)) and len(x) != len(y)):
            diffs.append((at, x, y))
        elif isinstance(x, (list, tuple)):
            for i, (p, q) in enumerate(zip(x, y)):
                walk(p, q, at + (i,))
        elif x != y:
            diffs.append((at, x, y))

    for entry in sorted(got):
        walk(got[entry], want[entry], ())
    guards = [p for p in art["proofs"] if p.startswith("reroll_guard(")]
    assert len(diffs) == len(guards) and guards
    assert all((x, y) == (True, False) for _at, x, y in diffs), diffs


def test_the_voice_record_declares_every_binding_no_name_covers(art, text):
    """§4(e)'s voice parameter record: what the loop binds and no systematic name says."""
    loop = next(s for s in art["rolled"][PLAY] if s[0] == "forvoice")
    shown = sml.voice_display(loop[4], loop[1])
    rows = [n for n, decl in shown if decl]
    assert len(rows) == art["unify"]["params"] and rows == sorted(set(rows), key=rows.index)
    block = text.split("voices v1, v2 {")[1].split("\n}")[0]
    assert {ln.split(" = ")[0].strip() for ln in block.splitlines() if ln.strip()} == set(rows)
    for name, _decl in shown:  # every binding is spelled, and nothing spells two ways
        assert name in text


def test_no_unread_carry_definitions(art):
    """3b landing 1 closed #144's unread-``cflag`` finding; this keeps it closed."""
    dead = []
    for entry in proc_entries(art["eqlift_text"]):
        dead += dead_local_defs(extract_proc(art["eqlift_text"], entry))
    assert [d for d in dead if "carry" in repr(d[1]) or "<=" in repr(d[1])] == []


def test_the_filter_is_voice_three_s_alone(art):
    """The asymmetry is deliberate and is the only one: the cut cells are voice 3's."""
    ram = art["ram_end"]
    for v in range(VOICES - 1):
        assert ram[ZPV[v] + CUT] == 0, "voice %d owns filter state" % v
    assert ram[ZPV[VOICES - 1] + CUT] or ram[ZPV[VOICES - 1] + CUT + 1]


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
def test_wav_renders_and_the_two_spans_agree(art, tmp_path):
    """Both renders cover the same frames, so the A/B is over identical audio."""
    pytest.importorskip("pysidtracker.audio")

    def seconds(path):
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()

    mine = minimized_wav(art, tmp_path / "minimized.wav")
    assert seconds(mine) > 10
    try:
        theirs = sidplayfp_wav(art["mem"], art["labels"], tmp_path / "tune.wav")
    except Exception as exc:  # pylint: disable=broad-except
        pytest.skip("sidplayfp unavailable: %s" % exc)
    assert abs(seconds(mine) - seconds(theirs)) <= FRAME_S
    assert abs(seconds(mine) - wav_frames(art) * FRAME_S) <= FRAME_S


# The goal pinned: properties enumerated from the artifact, xfails naming their stage.
XFAIL = dict(strict=True)
OWNER = "stage 4 landing 4 (remaining part)"  # the live owner every pin here names (#180)
ARCH = frozenset(("a", "x", "y", "sp", "cflag", "nflag", "zflag", "vflag"))
LINE_PIN, COST_PIN = 324, 773  # lowered by stage 4: the variable-stride advance folds
# lowers these, never raises — only a feature landing re-pins them upward
EVIDENCE = {  # per role, the clause its declaration owes (sidprog.lark statedef)
    "cursor": r"\bin\s+\w+",
    "accumulator": r"\b(?:observed|mask|bound)\b",
    "vm": r"\b(?:ops|operators)\b",
}
REEMIT = ("reemit_6502", "emit_6502", "assemble_6502")
SEED_CODE = (
    "from examples.state_machine_lift import classify_roles, pipeline, render;"
    "a = pipeline(frames=%d);"
    "import sys; sys.stdout.write(render(a['rolled'], classify_roles(a['folded'], %d), %d))"
)
DECL = re.compile(r"^\s{2}(\w+):\s*(\w+)\s+(\S+)(.*)$")
OPSET = re.compile(r"^[^\n]*\b(?:operators|ops)\b[^\n]*\{$", re.M)
OPDECL = re.compile(r"\bop\s+(\w+)\b[^\n]*?\barity\s+(\d+)\b[^\n]*?\bwrites\b")


@pytest.fixture(scope="module", name="role_map")
def _role_map(art):
    return classify_roles(art["folded"])


@pytest.fixture(scope="module", name="role_text")
def _role_text(art, role_map):
    return render(art["rolled"], role_map)


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


def _reads_by_kind(procs):
    """Cell names read by data statements, and by control transfers."""
    data, ctrl = set(), set()
    for s in (x for stmts in procs.values() for x in _walk(stmts)):
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

    def _mark(self, i, kind):
        """A wide access is a slice, and every byte it spans took that access."""
        for a in [i] if isinstance(i, int) else range(*i.indices(len(self))):
            self.first.setdefault(a, kind)

    def __getitem__(self, i):
        self._mark(i, "r")
        return super().__getitem__(i)

    def __setitem__(self, i, value):
        self._mark(i, "w")
        super().__setitem__(i, value)


def _carried_addrs(art, ram0):
    """Addresses some frame reads before writing: what genuinely crosses the boundary."""
    machine = sml.Machine({e: sml.Flat(s) for e, s in art["folded"].items()}, ram0)
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
        [sys.executable, "-c", SEED_CODE % (frames, frames, frames)],
        cwd=str(Path(__file__).resolve().parent.parent),
        env=dict(os.environ, PYTHONHASHSEED=seed),
        check=True,
        stdout=subprocess.PIPE,
    )
    return got.stdout


@pytest.mark.xfail(
    reason="register-model-lift stage 4 landing 4 (remaining part): the prototype's fold and "
    "render layers re-based onto the artifact -- the engine emits role keywords now, but "
    "this text is sml.render's, which still names a/x/y off the pre-rung eqlift dialect",
    **XFAIL,
)
def test_no_architectural_register_survives_as_a_value(role_text):
    """Every value flows through declared state or a width-typed local.

    At pin time the lifted text still names ``a`` and ``y``."""
    names = set(re.findall(r"[A-Za-z_]\w*", re.sub(r"\$[0-9A-Fa-f]+|;.*", "", role_text)))
    assert not ARCH & names


def test_smc_dispatch_cells_are_not_data_state(art, role_map):
    """A cell whose only reads are the computed transfer is a JMP operand, not state."""
    data, ctrl = _reads_by_kind(art["folded"])
    assert not sorted(set(role_map) & (ctrl - data))


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


def test_state_block_holds_no_scratch(art, role_map, post_init_ram):
    """Every declared cell is read before its first write in some frame.

    A wide access is a slice, so the tracer marks every byte it spans: without that
    the ``u24`` phase read as one span looked written first and the ``u16`` diffs
    written as one span looked read first."""
    carried = _carried_addrs(art, post_init_ram)
    assert not sorted(n for n in role_map if _addrs(n) and not set(_addrs(n)) & carried)


def test_roles_carry_their_evidence(role_map, role_text):
    """A cursor names the block it walks, an accumulator its bound, a vm cell its ops."""
    decls = _state_decls(role_text)
    assert not sorted(
        pretty(n)
        for n, r in role_map.items()
        if r in EVIDENCE and not re.search(EVIDENCE[r], decls.get(pretty(n), ("", "", ""))[2])
    )


def test_init_lifts_to_declared_initial_values(art, role_map, role_text, post_init_ram):
    """Each declared cell's post-init value is its initializer; only SID writes stay.

    Read at the declared width, over the declared cells: a lane a ``u16`` spans is
    not a field of its own, so the pair's two bytes are one value."""
    assert all(0 <= r < 25 for r, _v in art["init_writes"]), "init writes off the SID boundary"
    decls, want = _state_decls(role_text), {}
    for n in role_map:
        name, addrs = pretty(n), _addrs(n)
        if addrs and name in decls:
            w = int(decls[name][1][1:]) // 8
            want[name] = int.from_bytes(bytes(post_init_ram[addrs[0] : addrs[0] + w]), "little")
    got = {}
    for name, decl in decls.items():
        m = re.search(r"=\s*\$([0-9A-Fa-f]+)", decl[2])
        if m:
            got[name] = int(m.group(1), 16)
    assert sorted(want) == sorted(decls), "a declared cell names no post-init value"
    assert got == want


def test_round_trip_witness_is_frame_identical(art):
    """Minimal 6502 re-emitted from the minimized program replays frame-for-frame.

    No evaluator in the trust chain: the artifact is assembled to 6502 and run on the
    machine, and its SID projection is differenced against the original routine's own."""
    emit = next(
        (f for m in (sml, sml.eqlift_mem) for n in REEMIT if callable(f := getattr(m, n, None))),
        None,
    )
    assert emit is not None, "no minimal-6502 re-emission capability"
    mem, _labels = emit(art["prog"])
    frames = sml.run_vm(mem, len(art["min_frames"]))[2]
    assert framelog.canonical(frames) == framelog.canonical(art["orig_frames"])


def test_every_pin_names_the_landing_that_flips_it():
    """#180's law, executable here as it already is on the shredder's family.

    The one left is landing 4's, so a pin that acquires a different owner moves the
    plan's ledger too and the two cannot drift apart."""
    pins = {
        n: m.kwargs["reason"]
        for n, f in sorted(globals().items())
        for m in getattr(f, "pytestmark", ())
        if m.name == "xfail" and "reason" in m.kwargs
    }
    assert len(pins) == 1, sorted(pins)
    assert not [n for n, r in pins.items() if OWNER not in r]


def test_render_is_hash_seed_independent():
    """adoption §10's closed defect, kept closed: no consumer sees the extraction order."""
    frames = FRAMES // 4
    assert _seeded_render("0", frames) == _seeded_render("12345", frames)


def test_size_ratchet(art, role_text):
    """Emitted size and extracted term cost: a stage lowers them or holds them."""
    assert len(role_text.splitlines()) <= LINE_PIN
    assert _term_cost(list(art["rolled"].values())) <= COST_PIN
