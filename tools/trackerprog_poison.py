#!/usr/bin/env python3
"""Section 7's poison method, run: two forms of one object over whole horizons.

The thirty certified builds of the nine hand transliterations, a stated mutation
and a count of the ticks whose write lists differ.  Every horizon is read from
the committed certificate that records it, so no tick count here is typed.
"""

import argparse
import hashlib
import importlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deity_informant.trackerprog import poison  # noqa: E402
from deity_informant.tuneprog import tunes  # noqa: E402

CERTS = ROOT / "docs" / "certificates"
DEFAULT_CACHE = ROOT / ".oracle-cache-poison"


@dataclass(frozen=True)
class Build:
    """One certified build: the tool that states it, the tune, and its certificate."""

    name: str
    module: str
    tune: str
    song: int
    cert: str
    cert_song: int
    kind: str


BUILDS = (
    Build("commando-song1", "commando", "Commando.sid", 0, "commando-song1", 1, "song"),
    Build("commando-song2", "commando", "Commando.sid", 1, "commando-song2", 2, "song"),
    # the third subtune has no committed certificate; all three run one horizon
    Build("commando-song3", "commando", "Commando.sid", 2, "commando-song1", 1, "song"),
    Build(
        "gt2-je-suis-linus",
        "goattracker",
        "Je_suis_Linus_le_salaud.sid",
        0,
        "gt2-je-suis-linus",
        1,
        "plain",
    ),
    Build("gt2-do-it-again", "goattracker", "Do_It_Again.sid", 0, "gt2-do-it-again", 1, "plain"),
    Build("sw-emomyst", "sidwizard", "Emomyst.sid", 0, "sw-emomyst", 1, "plain"),
    Build(
        "sw-end-of-the-world",
        "sidwizard",
        "End_of_the_World.sid",
        0,
        "sw-end-of-the-world",
        1,
        "plain",
    ),
    Build("defmon-jazzpjazz", "defmon", "Jazzpjazz.sid", 0, "goto80-jazzpjazz", 1, "claim"),
    Build("defmon-automatas", "defmon", "Automatas.sid", 0, "automatas", 1, "claim"),
    Build(
        "jch-guldkorn", "jch", "Guldkornekspressen_Intro.sid", 0, "jch-guldkorn-intro", 1, "claim"
    ),
    Build("jch-knob", "jch", "I_Could_Eat_a_Knob_at_Night.sid", 0, "jch-knob-at-night", 1, "claim"),
    Build("follin-song0", "follin", "Ghouls_n_Ghosts.sid", 0, "ghouls-song01", 1, "song"),
    Build("follin-song6", "follin", "Ghouls_n_Ghosts.sid", 6, "ghouls-song07", 7, "song"),
    Build("follin-song20", "follin", "Ghouls_n_Ghosts.sid", 20, "ghouls-song21", 21, "song"),
    Build(
        "blackbird-quintessence", "blackbird", "Quintessence.sid", 0, "lft-quintessence", 1, "pair"
    ),
    Build("walker-chameleon", "walker", "Chameleon.sid", 1, "walker-chameleon", 1, "pair"),
) + tuple(
    Build("galway-song%d" % s, "galway", "Comic_Bakery.sid", s, "galway-comic-bakery", s, "galway")
    for s in range(1, 15)
)
BUILD = {b.name: b for b in BUILDS}

# the eleven builds section 7's P1-P8 rows were measured over, before Follin
ELEVEN = tuple(
    b.name for b in BUILDS if b.module in ("commando", "goattracker", "sidwizard", "defmon", "jch")
)
SETS = {"all": tuple(BUILD), "eleven": ELEVEN}
SETS.update((m, tuple(b.name for b in BUILDS if b.module == m)) for m in {b.module for b in BUILDS})

POISONS = {
    "clock-no-reset": ["drop meta.tempo.reset"],
    "clock-no-funk": ["drop meta.tempo.reset.0"],
    "flag-seed": ["set accs.*.flag.seed=0"],
    "acc-bound": ["drop accs.*.bound"],
    "commit-order": ['set meta.commit_order=["ctrl", "ad", "sr"]'],
    "no-shadow": ["drop meta.shadow"],
    "stream-rank": ["drop streams.*.rank"],
    # B8's one-family forms: each kept row of the schema, struck, so its worth is
    # a number and not a sentence (backlog D6).  A form no build carries after the
    # strike renders 0 and says "no site in", which is the registry doing its job
    "acc-beyond": ['set accs.*.beyond.words.1={"const": 0}'],
    "acc-trap": ["set accs.*.trap=false"],
    "amplitude-count": ["set accs.*.amplitude.count=1"],
    "clamp-edge": ["set accs.*.policy.edge=0"],
    "cmd-tie": ["drop score.commands.*.tie"],
    "emit-entry": ["drop accs.*.emit"],
    "flush-unguarded": ["set meta.shadow.registers.*.1=[]"],
    "insrec-voice": [
        'set streams.hard_restart.rows.*.sets.*.1.insrec.0="ins"',
        'set streams.hard_restart.rows.*.when.*.0.and.0.insrec.0="ins"',
    ],
    "no-bug": ['set instruments.*.on_note.0.sets.1.1.shr.0.tuned={"cell": "note"}'],
    "op-wrap": ["drop streams.*.rows.*.op.wrap"],
    "pitch-links": ["drop meta.pitch_links"],
    "pitch-target": ['set meta.pitch_target="freq"'],
    "reflect-complement": ["set accs.vib_phase.amplitude.interval.1=255"],
    "rest-arm": ["set meta.rest_arm=[]"],
    "row-command-spent": ['set meta.row_command="spent"'],
    "stage-hold": ["drop meta.stage.*.hold"],
    "stop-voice": ['set meta.stop="voice"'],
    "sweep-bounce": ["drop accs.pw_down.gate", "drop accs.pw_up.gate"],
    "commit-guard": ["drop globals.commit.*.2"],
}


def horizon(cert, song):
    """The build's whole horizon, from the ``subtunes`` record of its certificate."""
    subs = json.loads((CERTS / (cert + ".json")).read_text())["subtunes"]
    return next(s for s in subs if len(subs) == 1 or s["song"] == song)["ticks"]


def horizons(names):
    """``{name: ticks}`` for the named builds, in registry order."""
    return {n: horizon(BUILD[n].cert, BUILD[n].cert_song) for n in names}


def module(name):
    return importlib.import_module("trackerprog_" + name)


def _obj_plain(mod, path, b):
    return mod.build(path)


def _obj_song(mod, path, b):
    return mod.build(path, b.song)


def _obj_pair(mod, path, b):
    return mod.build(path)[0]


def _obj_galway(mod, path, b):
    return mod.build(path, b.song, horizon(b.cert, b.cert_song))[0]


def _obj_claim(mod, path, b):
    loop, ticks, cycles = mod.claim(str(CERTS / (b.cert + ".json")), b.song)[:3]
    return mod.build(path, b.song, cycles, None if loop else ticks)


BUILDERS = {
    "plain": _obj_plain,
    "song": _obj_song,
    "pair": _obj_pair,
    "galway": _obj_galway,
    "claim": _obj_claim,
}


def _toolhash(name):
    src = (ROOT / "tools" / ("trackerprog_%s.py" % name)).read_bytes()
    return hashlib.sha256(src).hexdigest()[:12]


def build_object(name, cache=None):
    """The object its tool's own reproduce line builds, cached on the tool's hash."""
    b = BUILD[name]
    d = poison.cache_dir(cache)
    path = d / ("obj-%s-%s.json" % (name, _toolhash(b.module))) if d else None
    if path is not None and path.is_file():
        return json.loads(path.read_text())
    tune = tunes.resolve(b.tune)
    if tune is None:
        raise SystemExit("%s: %s unavailable (no HVSC tree, no cache, offline)" % (name, b.tune))
    obj = BUILDERS[b.kind](module(b.module), str(tune), b)
    if path is not None:
        d.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))
    return obj


def sweep_build(args):
    """One build against every poison, plus the stored-render comparison."""
    name, specs, cache, stored = args
    obj = build_object(name, cache)
    ticks = horizon(BUILD[name].cert, BUILD[name].cert_song)
    out = {}
    for label, edits in specs:
        out[label] = poison.strike(obj, poison.Poison(label, edits), ticks, cache)
    if stored:
        out["stored"] = poison.against(obj, np.load(Path(stored) / (name + ".npy")), ticks, cache)
    return name, out


def emit(names, out, cache):
    """Write each build's base render, for another checkout to strike against."""
    Path(out).mkdir(parents=True, exist_ok=True)
    for name in names:
        b = BUILD[name]
        d = poison.render_digests(build_object(name, cache), horizon(b.cert, b.cert_song), cache)
        np.save(Path(out) / (name + ".npy"), d)
    return len(names)


def resolve(spec):
    """A build set: named sets and build names, comma-separated."""
    names = []
    for part in spec.split(","):
        part = part.strip()
        if part in SETS:
            names.extend(n for n in SETS[part] if n not in names)
        elif part in BUILD:
            names.append(part)
        else:
            raise SystemExit("no such build or set: %s" % part)
    return names


def _s(n):
    return "" if n == 1 else "s"


def report(name, rows, out=None):
    """Every build's line under one poison, then the poison's own totals."""
    out = out or sys.stdout
    print("== %s ==" % name, file=out)
    for build, row in rows.items():
        print(poison.line(build, row), file=out)
    t = poison.total(rows)
    print(
        "%-24s %7d of %-7d differing over %d build%s, %d site%s"
        % (
            "TOTAL",
            t["differing"],
            t["ticks"],
            t["builds"],
            _s(t["builds"]),
            t["sites"],
            _s(t["sites"]),
        ),
        file=out,
    )
    if t["untouched"] and t["sites"]:
        print("   no site in: %s" % " ".join(t["untouched"]), file=out)
    elif t["untouched"] and rows and any(r["mutation"] != "stored render" for r in rows.values()):
        print("   matched no site in any build", file=out)
    if t["refused"]:
        print("   refused by: %s" % " ".join(t["refused"]), file=out)
    return t


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--builds", default="all", help="build names or sets: %s" % ", ".join(sorted(SETS))
    )
    p.add_argument(
        "--poison",
        action="append",
        default=[],
        help="a named poison: %s" % ", ".join(sorted(POISONS)),
    )
    p.add_argument("--drop", action="append", default=[], help="drop PATH, as its own mutation")
    p.add_argument(
        "--set", action="append", default=[], dest="sets", help="set PATH=JSON, as its own mutation"
    )
    p.add_argument("--horizons", action="store_true", help="print the horizon table and stop")
    p.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="worker processes (0: one per build, capped at the CPUs)",
    )
    p.add_argument("--json", help="write the whole sweep here")
    p.add_argument(
        "--cache", default=str(DEFAULT_CACHE), help="render and object cache ('' for none)"
    )
    p.add_argument("--emit-digests", help="write each build's base render here and stop")
    p.add_argument("--against", help="count against base renders written by another checkout")
    a = p.parse_args(argv)

    names = resolve(a.builds)
    h = horizons(names)
    if a.horizons:
        for name, ticks in h.items():
            print("%-24s %7d" % (name, ticks))
        print("%-24s %7d over %d builds" % ("TOTAL", sum(h.values()), len(h)))
        return 0
    cache = a.cache or None
    if a.emit_digests:
        print("%d builds written to %s" % (emit(names, a.emit_digests, cache), a.emit_digests))
        return 0

    specs = [(n, POISONS[n]) for n in a.poison] + [("drop " + d, ["drop " + d]) for d in a.drop]
    specs += [("set " + s, ["set " + s]) for s in a.sets]
    if not specs and not a.against:
        specs = [("identity", [])]
    jobs = a.jobs or min(len(names), os.cpu_count() or 1)
    work = [(n, specs, cache, a.against) for n in names]
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            done = dict(pool.map(sweep_build, work))
    else:
        done = dict(map(sweep_build, work))

    out = {"horizons": h, "sweeps": {}}
    labels = [label for label, _ in specs] + (["stored"] if a.against else [])
    for label in labels:
        rows = {n: done[n][label] for n in names}
        out["sweeps"][label] = {"builds": rows, "total": report(label, rows)}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
