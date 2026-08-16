"""E11: the printed tuneprog of the two exemplars (marked ``hvsc``; short horizons).

Asserts the shape the plan's appendix A and anatomy 3.7.7 call for -- two rates,
the SID image write-out, the struct views, the oscillator ``for`` -- and that S5/S6
left the certified S4 program byte-identical.
"""

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("pysidtracker")

from pysidtracker.testing import resolve_tune  # noqa: E402

from deity_informant import cli  # noqa: E402
from deity_informant.tuneprog import pipeline, printer, recover, structure  # noqa: E402
from deity_informant.tuneprog.machine import find_entries  # noqa: E402
from deity_informant.tuneprog.trace import Tracer  # noqa: E402
from deity_informant.tuneprog.verify import verify  # noqa: E402

pytestmark = pytest.mark.hvsc

_CACHE = Path(os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache")) / "hvsc"
PAL_CLOCK = 985248
AUTOMATAS = "MUSICIANS/G/Goto80/Automatas.sid"
COMMANDO = "MUSICIANS/H/Hubbard_Rob/Commando.sid"


def _tune(relpath):
    path = resolve_tune(relpath, cache_dir=_CACHE)
    if path is None:
        pytest.skip("%s unavailable (no HVSC tree, no cache, offline)" % relpath)
    return Path(path).read_bytes()


def _printed(relpath, seconds, song=None):
    """Trace, build, verify, then structure/recover/print: ``(text, names, prog, v)``."""
    data = _tune(relpath)
    img, schedule = find_entries(data)
    entry = schedule[0]
    calls = int(seconds * PAL_CLOCK / entry.cycles_per_tick)
    tracer = Tracer(img, entry, song=song)
    tracer.run_init()
    tracer.run_calls(calls)
    trace = tracer.trace()
    prog, _r, _p = pipeline.build(trace, Path(relpath).name)
    before = prog.to_json()
    v = verify(prog, trace, calls=calls, prefix=200)
    view = structure.view(prog, printer.needed(prog)[0])
    st = structure.structure(view)
    names = recover.recover(view, st)
    text = printer.render(view, st, names)
    assert prog.to_json() == before  # S5/S6 annotate; the certified program is untouched
    return text, names, prog, v, calls


def _fields(names, group="voice"):
    return {names.view[r][1] for r in names.groups.get(group, {}).get("members", ())}


def test_automatas_prints_the_shape_of_the_anatomy_player():
    text, names, _prog, v, calls = _printed(AUTOMATAS, seconds=30)
    assert v.div is None and v.call == calls

    # two rates: the wrapper's call counter selects main or sub
    assert "call_counter" in text and "& 7" in text
    assert "main(" in text and "sub(" in text
    assert set(names.procs.values()) >= {"tick", "main", "sub", "row_apply", "init"}

    # the SID image write-out: >= 8 per-voice fields, all three voices
    image = {n for r, n in names.region.items() if names.role.get(r) == "sid_image"}
    assert image >= {"pw_lo", "pw_hi", "freq_lo", "freq_hi", "sr", "ad", "ctrl", "ctrl_eor"}
    assert len(_fields(names) & image) >= 8
    for v_i in range(3):
        assert "sid[%d].ctrl = " % v_i in text
    assert "sid.res_route = " in text and "sid.mode_vol = " in text

    # struct-of-code: the voice view is stride 49 and holds the cascade counters
    assert names.groups["voice"]["stride"] == 49 and names.groups["voice"]["n"] == 3
    assert "timer" in _fields(names) and any(f.startswith("ptr") for f in _fields(names))

    # the oscillator loop over X in {$62,$31,0} prints as a for over the voice index
    assert "for v in 2, 1, 0:" in text
    assert "voice[v].freq_lo = " in text and "FREQ_LO[" in text

    # the frequency table, the filter switch on the patched opcode, the raster wait
    assert "freq_table" in [names.role.get(r) for r in names.region]
    assert "switch " in text and "case " in text
    assert "while input($D012)" in text


def test_the_cli_subcommand_decompiles_commando_at_a_short_horizon(tmp_path):
    sid = tmp_path / "Commando.sid"
    sid.write_bytes(_tune(COMMANDO))
    out = tmp_path / "out"
    assert cli.main(["tuneprog", str(sid), "--out", str(out), "--seconds", "5"]) == 0
    doc = (out / "tuneprog.md").read_text()
    assert doc.startswith("# tuneprog: Commando.sid")
    assert "## program" in doc and "tick(" in doc
    cert = json.loads((out / "certificate.json").read_text())
    assert cert["divergence"] is None and cert["stage"] == "S6"


def test_commando_prints_the_shape_of_the_design_illustration():
    text, names, _prog, v, calls = _printed(COMMANDO, seconds=20, song=0)
    assert v.div is None and v.call == calls

    # the speed divider, exactly the design's S5 illustration
    assert "timer" in text and any(l.strip().endswith("-= 1") for l in text.splitlines())
    assert " < 0:" in text

    # the voice loop over X = 2..0 and the stride-1 per-voice fields
    assert "for v in 2, 1, 0:" in text
    assert names.groups["voice"]["stride"] == 1 and names.groups["voice"]["n"] == 3
    assert len(_fields(names)) >= 4
    assert "voice[v]." in text

    # the note table and the pointer-borne pattern stream
    assert "FREQ" in text and "ptr" in text
    assert "sid[" in text or "io[" in text
