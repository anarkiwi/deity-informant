"""What a resumed run may keep: the options a record was taken under, and the state.

``state.json`` carries the horizon and the build options each stage ran with, so
a run resumed under different ones rewinds to the stage they first decide rather
than certifying a mixture (:mod:`.pipeline` drives the stages themselves).
"""

from __future__ import annotations

import json


def horizon(args):
    """What decides where a subtune stops; a record made under another one is stale."""
    return [args.calls, args.seconds, args.max_calls, bool(args.until_period), args.chunk]


def _stops(st, args):
    """Drop the ``--songs all`` trace records another horizon wrote, and what they fed.

    A stale record means the subtune must be traced again, so the run rewinds to
    S1 and forgets that subtune's verification; a layout without records (an
    older run's ``traced`` list) keeps nothing.
    """
    old = st.get("traced")
    keep = {}
    if isinstance(old, dict):
        keep = {k: v for k, v in old.items() if v.get("horizon") == horizon(args)}
    st["traced"] = keep
    if len(keep) != len(old or ()):
        st["stage"] = "trace"
        st["subtunes"] = [x for x in st.get("subtunes", ()) if str(x["song"]) in keep]
        st.pop("divergence", None)  # the run that found it is the one being redone
    return st


def _stops_one(st, args, out):
    """The single-song analogue of :func:`_stops`: rewind a run taken at another horizon.

    A tracer resumed under a horizon nobody asked for certifies that many ticks
    (a shorter target never re-traces), so a mismatch goes back to S1 and forgets
    the verification the old target produced.
    """
    if st.get("horizon") not in (None, horizon(args)):
        st.update(stage="trace", subtunes=[])
        st.pop("divergence", None)
        for name in ("verify.pkl", "tracer.pkl"):
            (out / name).unlink(missing_ok=True)
    st["horizon"] = horizon(args)
    return st


def build_opts(args):
    """What decides the program the front end builds; a change invalidates it."""
    return [args.closure, bool(args.no_merge), args.songs, args.sid_model]


def state(out, args):
    p = out / "state.json"
    st = json.loads(p.read_text()) if args.resume and p.exists() else {"stage": "trace", "calls": 0}
    if st.get("build") not in (None, build_opts(args)) and st["stage"] != "trace":
        # the S4 program on disk is not the one these options ask for, and a
        # verifier's machine state belongs to the program that produced it
        st.update(stage="front", subtunes=[])
        st.pop("divergence", None)
        (out / "verify.pkl").unlink(missing_ok=True)
    return _stops(st, args) if args.songs == "all" else _stops_one(st, args, out)
