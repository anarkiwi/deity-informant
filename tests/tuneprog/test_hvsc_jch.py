"""JCH NewPlayer V20 on two tunes (marked ``hvsc``; short horizons).

What anatomy 3.5 and ``docs/prototype-jch.md`` say the plain 3-voice V20 must
come out as: the width-3 state block, the 4-column table programs, the two-phase
tick, a voice *loop* with no family, and the RAM under the register file.
"""

import re

import pytest

from deity_informant.tuneprog import copymerge

from _hvsc import GULDKORN, KNOB, decompiled

pytestmark = pytest.mark.hvsc

KNOB_S = 15
GULD_S = 30
BOTH = ((KNOB, KNOB_S), (GULDKORN, GULD_S))


def test_both_v20_builds_certify_over_their_horizon():
    for rel, secs in BOTH:
        run = decompiled(rel, seconds=secs)
        assert run.v.div is None and run.v.call == run.calls
        sub = run.cert["subtunes"][0]
        assert sub["divergences"] == 0 and sub["envelope_traps"] == 0
        assert run.prog.meta["stack"] == "eliminated"


def test_the_state_block_is_one_width_three_record_per_track():
    """``abs,X`` with X the track: rows of three, so the record is stride 1, n 3."""
    for rel, secs in BOTH:
        run = decompiled(rel, seconds=secs)
        g = run.names.groups["voice"]
        assert (g["stride"], g["n"]) == (1, 3) and len(g["members"]) >= 30
        assert "voice[v]." in run.text


def test_the_voice_loop_is_a_loop_and_no_sibling_family():
    """JCH walks X = 2, 1, 0 with DEX/BMI, so there is nothing to fold."""
    for rel, secs in BOTH:
        run = decompiled(rel, seconds=secs)
        assert "for v in 2, 1, 0:" in run.text
        assert copymerge.report(run.prog) is None


def test_the_table_programs_type_as_their_own_records():
    """Pulse/filter are 4-byte records, instruments 8, the frequency table u16."""
    run = decompiled(GULDKORN, seconds=GULD_S)
    strides = sorted(g["stride"] for g in run.names.groups.values())
    assert strides.count(4) >= 2 and 8 in strides
    freq = [r for r, k in run.names.role.items() if k == "freq_table"]
    assert len(freq) == 1 and "12-TET" in run.text
    assert re.search(r"rec\d\[\w+/4\]\.\w+", run.text), run.text


def test_the_tick_counter_is_the_phase_and_the_note_lands_two_frames_late():
    """Prefetch at tick 2 into staged cells, commit at tick 0, hard restart before."""
    run = decompiled(GULDKORN, seconds=GULD_S)
    assert run.names.phase is not None
    assert "phase -= 1" in run.text and "if phase == 0:" in run.text
    assert "sid[v].ad = $F\n" in run.text and "sid[v].sr = 0\n" in run.text
    assert "= $FE" in run.text and "= $FF" in run.text


def test_the_registers_a_role_reaches_are_named_by_the_register():
    for rel, secs in BOTH:
        run = decompiled(rel, seconds=secs)
        assert "sid.res_route = " in run.text and "sid.mode_vol = " in run.text
    named = {
        run.names.region[r]
        for run in (decompiled(GULDKORN, seconds=GULD_S),)
        for r, k in run.names.role.items()
        if k == "sid_image"
    }
    assert {"cutoff_hi", "mode_vol"} <= named  # the $D417/$D418 shadows, by their register
    knob = decompiled(KNOB, seconds=KNOB_S).text
    assert "ghost.cutoff_hi = " in knob and "ghost.res_route = " in knob


def test_the_puterman_build_writes_the_ram_under_the_register_file():
    """Its wrapper banks I/O out, so the player's writes are memory, the flush is not."""
    run = decompiled(KNOB, seconds=KNOB_S)
    ghost = [r for r, d in run.names.image.items() if d == 0]
    assert len(ghost) == 1 and run.names.region[ghost[0]] == "ghost"
    rgn = next(r for r in run.prog.storage if r.id == ghost[0])
    assert (rgn.base, rgn.size, rgn.kind) == (0xD400, 25, "state")
    assert "ghost.reg[" in run.text and "sid[v].freq_lo = copy[v].freq_lo" in run.text
    assert "input(" not in run.text
    assert run.cert["subtunes"][0]["inputs_pinned"] == 2


def test_the_two_init_cleared_blocks_print_as_records_over_the_track_index():
    """``$1014``/``$1748`` are walked ``base + n*3 + v``: the transpose of a stride view."""
    for rel, secs, base in ((KNOB, KNOB_S, 0x1014), (GULDKORN, GULD_S, 0x1014)):
        run = decompiled(rel, seconds=secs)
        rid = next(r.id for r in run.prog.storage if r.base == base)
        g, k, fields, flip = run.names.split[rid]
        assert (k, flip, len(fields)) == (3, True, 4)
        assert run.names.groups[g]["n"] == 3 and "%s[v]." % g in run.text
        assert "b%04X[" % base not in run.text


def test_the_register_offset_table_names_the_register_by_its_voice():
    """``$1740`` holds 0, 7, 14, so an index read from it is the voice itself."""
    for rel, secs, name in ((KNOB, KNOB_S, "ghost"), (GULDKORN, GULD_S, "sid")):
        run = decompiled(rel, seconds=secs)
        assert len(run.names.voicemap) == 1
        assert "voice_map" in run.names.role.values()
        for reg in ("ad", "sr", "freq_lo", "pw_lo"):
            assert "%s[v].%s" % (name, reg) in run.text or "%s[x].%s" % (name, reg) in run.text
        assert "%s.reg[5 + " % name not in run.text
