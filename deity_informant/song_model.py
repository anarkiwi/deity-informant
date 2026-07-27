"""Song-synthesis model recovery over eqlift pass-1 graphs.

Per-voice cadence counters, SID frequency-driver classification, and a
control/envelope automaton (note-lifecycle states with guarded gate/waveform/
AD-SR edges), reusing eqlift_annotate dataflow provenance."""

from collections import namedtuple

from . import eqlift_annotate as ann
from . import frameproc

_INC, _DEC = 0x01, 0xFF
_FREQ = frozenset(("freq_lo", "freq_hi"))
_CTRL = frozenset(("control", "attack_decay", "sustain_release"))
_SYM = {
    "INT_AND": "&",
    "INT_OR": "|",
    "INT_XOR": "^",
    "INT_ADD": "+",
    "INT_SUB": "-",
    "INT_EQUAL": "==",
    "INT_NOTEQUAL": "!=",
    "INT_LESS": "<",
    "INT_LESSEQUAL": "<=",
}


def _cell(base):
    """A RAM cell that may hold a counter/accumulator (excludes SID and 0/1)."""
    return base >= 0x02 and not ann.SID_LO <= base <= ann.SID_HI + 4


Counter = namedtuple("Counter", "base kind reload")
FreqDriver = namedtuple("FreqDriver", "role source pitch slide kind")
Transition = namedtuple("Transition", "role action to source guards")
Automaton = namedtuple("Automaton", "states transitions")
Sweep = namedtuple("Sweep", "plane acc updates rate bounds")
FilterModel = namedtuple("FilterModel", "cutoff static")
SongModel = namedtuple("SongModel", "counters freq control pw filter")

_PW = frozenset(("pw_lo", "pw_hi"))
_CUTOFF = frozenset(("cutoff_lo", "cutoff_hi"))
_CARRY = frozenset(("INT_CARRY", "INT_LESS", "INT_LESSEQUAL", "INT_EQUAL", "INT_NOTEQUAL"))
_FILTER_REGS = (0x15, 0x16, 0x17, 0x18)


def _render(ir):
    """Compact source-level string for a guard/value expression (best effort)."""
    if not isinstance(ir, tuple):
        return str(ir)
    if ir[0] == "const":
        return "$%02X" % ir[1]
    if ir[0] == "loc":
        return ir[1]
    if ir[0] == "mem":
        base = ann._const_base(ir[1])
        return "m_%04X" % base if base else "mem[.]"
    if ir[0] == "op" and ir[1] in _SYM:
        return "(" + (" %s " % _SYM[ir[1]]).join(_render(k) for k in ir[2]) + ")"
    return "?"


def _guard(cond, polarity):
    s = _render(cond)
    return s if polarity else "!" + s


def _has_and_mask(ir, keep):
    """True if ``ir`` ANDs with a const whose bit0 == keep (gate keep/clear)."""
    if not isinstance(ir, tuple):
        return False
    if ir[0] == "op" and ir[1] == "INT_AND":
        for k in ir[2]:
            if isinstance(k, tuple) and k[0] == "const" and (k[1] & 1) == keep:
                return True
    return any(_has_and_mask(k, keep) for k in ir[1:] if isinstance(k, tuple))


def _has_or_bit0(ir):
    if not isinstance(ir, tuple):
        return False
    if ir[0] == "op" and ir[1] == "INT_OR":
        for k in ir[2]:
            if isinstance(k, tuple) and k[0] == "const" and (k[1] & 1):
                return True
    return any(_has_or_bit0(k) for k in ir[1:] if isinstance(k, tuple))


def _control_action(val):
    """Classify a $D404 store: gate_off (mask clears bit0), gate_on, or waveform."""
    if _has_and_mask(val, 0):
        return "gate_off"
    if _has_or_bit0(val):
        return "gate_on"
    return "waveform"


def _resolve(expr, env, seen=None):
    """Follow ``loc`` defs through ``env`` to the first non-``loc`` expression."""
    seen = seen or set()
    while isinstance(expr, tuple) and expr[0] == "loc" and expr[1] not in seen:
        seen.add(expr[1])
        nxt = env.get(expr[1])
        if nxt is None:
            break
        expr = nxt
    return expr


def _step(expr, env):
    """(operand-base, kind) if ``expr`` is a +1/-1 counter update, else None."""
    if not (isinstance(expr, tuple) and expr[0] == "op"):
        return None
    if expr[1] not in ("INT_ADD", "INT_SUB"):
        return None
    imm = [k for k in expr[2] if isinstance(k, tuple) and k[0] == "const"]
    var = [k for k in expr[2] if not (isinstance(k, tuple) and k[0] == "const")]
    if len(imm) != 1 or not var:
        return None
    val = imm[0][1]
    dec = val == _DEC or (expr[1] == "INT_SUB" and val == _INC)
    inc = expr[1] == "INT_ADD" and val == _INC
    if not (dec or inc):
        return None
    return _read_base(var[0], env), "dec" if dec else "inc"


def _read_base(expr, env):
    """Const table/cell base that ``expr`` (resolved through ``env``) reads, or 0."""
    root = _resolve(expr, env)
    return ann._const_base(root[1]) if isinstance(root, tuple) and root[0] == "mem" else 0


def _inline(expr, env, seen=None):
    """Substitute every ``loc`` by its reaching env def (leaving mem/op/const)."""
    seen = seen or set()
    if not isinstance(expr, tuple):
        return expr
    if expr[0] == "loc":
        d = env.get(expr[1])
        if d is None or expr[1] in seen:
            return expr
        return _inline(d, env, seen | {expr[1]})
    if expr[0] == "op":
        return (expr[0], expr[1], tuple(_inline(k, env, seen) for k in expr[2]), expr[3])
    if expr[0] == "mem":
        return ("mem", _inline(expr[1], env, seen), expr[2])
    return expr


def _self_step(expr, base, env):
    """(direction, inlined step-ir) if ``expr`` is a self ADD/SUB on cell ``base``.

    Direction is ``sub`` when ``base`` is the minuend of an ``INT_SUB``; the step is
    the first non-carry operand that is not the self read, inlined through env."""
    root = _resolve(expr, env)
    if not (isinstance(root, tuple) and root[0] == "op" and root[1] in ("INT_ADD", "INT_SUB")):
        return None
    others = []
    self_seen = False
    for kid in root[2]:
        if _read_base(kid, env) == base:
            self_seen = True
        else:
            others.append(kid)
    if not self_seen:
        return None
    steps = [k for k in others if not (isinstance(k, tuple) and k[0] == "op" and k[1] in _CARRY)]
    pick = steps[0] if steps else others[0] if others else None
    return ("sub" if root[1] == "INT_SUB" else "add"), _inline(pick, env)


def _cell_refs(expr, env, out, seen=None):
    """Collect ``_cell`` bases read by ``expr``, following locals (never cells)."""
    seen = seen or set()
    if not isinstance(expr, tuple) or id(expr) in seen:
        return out
    seen.add(id(expr))
    if expr[0] == "loc":
        d = env.get(expr[1])
        if d is not None:
            _cell_refs(d, env, out, seen)
    elif expr[0] == "mem":
        base = ann._const_base(expr[1])
        if _cell(base):
            out.add(base)
        _cell_refs(expr[1], env, out, seen)
    elif expr[0] == "op":
        for kid in expr[2]:
            _cell_refs(kid, env, out, seen)
    return out


def _guard_comparands(gconds):
    """(counter-cell bases, other const/cell comparands) over guard conditions."""
    cells, consts = set(), set()

    def walk(ir):
        if not isinstance(ir, tuple):
            return
        if ir[0] == "op":
            for kid in ir[2]:
                if isinstance(kid, tuple) and kid[0] == "const":
                    consts.add(kid[1])
                b = ann._const_base(kid[1]) if isinstance(kid, tuple) and kid[0] == "mem" else 0
                if _cell(b):
                    cells.add(b)
            for kid in ir[2]:
                walk(kid)

    for cond, _pol in gconds:
        walk(cond)
    return cells, consts


def _pitch_ranges(tr, model):
    """[(base, end)] byte spans of every confirmed equal-tempered pitch table."""
    spans = []
    for d in ann._decls(model):
        info = tr.pitch.get(d["base"])
        if d["kind"] == "table" and info and info["pitch_table"]:
            spans.append((d["base"], d["base"] + d["size"]))
    return spans


def _in_pitch(tables, spans):
    return any(lo <= t < hi for t in tables for lo, hi in spans)


def _transition(role, val, guards, env, cells):
    """Classify a control/AD-SR store into an automaton edge with its guards."""
    if role == "control":
        action = _control_action(val)
        to = "off" if action == "gate_off" else "on"
    else:
        action, to = ("ad" if role == "attack_decay" else "sr"), "on"
    tables = set()
    ann._backtrace(val, env, cells, tables, set())
    return Transition(role, action, to, min(tables) if tables else 0, guards)


def _automaton(transitions):
    """A note-lifecycle automaton: states {off,on} plus the guarded edges."""
    states = set(t.to for t in transitions)
    if any(t.role == "control" for t in transitions):
        states.update(("off", "on"))
    return Automaton(tuple(sorted(states)), tuple(transitions))


def recover(stmts, model):
    """Recover counters, freq drivers, the control automaton and pw/filter sweeps."""
    tr = ann.table_roles(stmts, model)
    spans = _pitch_ranges(tr, model)
    env, cells, steps, reloads, accum, stores, trans = {}, {}, {}, {}, set(), [], []
    updates, planes = {}, []

    def visit(sl, guards, gconds):
        for s in sl:
            if s[0] == "asg":
                env[s[1]] = s[2]
            elif s[0] == "st":
                _store(s, guards, gconds)
            if s[0] == "if":
                then_pol = s[1] == "if"
                visit(s[3], guards + (_guard(s[2], then_pol),), gconds + ((s[2], then_pol),))
                visit(
                    s[4], guards + (_guard(s[2], not then_pol),), gconds + ((s[2], not then_pol),)
                )
            else:
                for b in frameproc._stmt_bodies(s):
                    visit(b, guards, gconds)

    def _store(s, guards, gconds):
        base = ann._const_base(s[1])
        role = ann._role_of(base)
        if role in _FREQ:
            stores.append(_probe(role, s[2], env, cells, spans))
            return
        if role in _CTRL:
            trans.append(_transition(role, s[2], guards, env, cells))
            return
        if role in _PW or role in _CUTOFF:
            planes.append((role, _cell_refs(s[2], env, set()), gconds))
            return
        if not _cell(base):
            return
        if s[1][0] == "const":
            cells[base] = s[2]
        st = _step(s[2], env)
        ss = _self_step(s[2], base, env)
        if st and st[0] == base:
            steps.setdefault(base, st[1])
        if ss:
            accum.add(base)
            updates.setdefault(base, []).append(ss + (gconds,))
        elif not (st and st[0] == base):
            src = _read_base(s[2], env)
            if src:
                reloads.setdefault(base, src)

    visit(stmts, (), ())
    freq = [_label(p, accum) for p in stores]
    counters = [Counter(b, k, reloads.get(b)) for b, k in sorted(steps.items())]
    decs = {b for b, k in steps.items() if k == "dec"}
    sweeps = [_sweep(role, refs, gc, accum, updates, decs) for role, refs, gc in planes]
    merged = _merge_sweeps(w for w in sweeps if w)
    pw = [w for w in merged if w.plane in _PW]
    cutoff = [w for w in merged if w.plane in _CUTOFF]
    return SongModel(counters, freq, _automaton(trans), pw, FilterModel(cutoff, {}))


def _sweep(role, refs, gconds, accum, updates, decs):
    """A plane store into a Sweep: accumulator (a self-updating cell it reads that
    is *not* a bare dec-counter), its update variants, gating counter and bounds.

    Rate/bounds come from the accumulator's own update guards plus the underflow
    guard on the plane store (turnaround comparands referencing the accumulator)."""
    accs = sorted((refs & accum) - decs) or sorted(refs & accum)
    if not accs:
        return None
    acc = accs[0]
    ups = tuple((d, st) for d, st, _g in updates.get(acc, []))
    turn = [(c, p) for c, p in gconds if _refs_cell(c, acc)]
    for _d, _st, gc in updates.get(acc, []):
        turn.extend((c, p) for c, p in gc if _refs_cell(c, acc))
    cells, consts = _guard_comparands(turn)
    ratecells, _rk = _guard_comparands(
        list(gconds) + [g for _d, _s, gc in updates.get(acc, []) for g in gc]
    )
    rate = sorted(((ratecells | cells) & decs) | (refs & decs))
    bounds = tuple(sorted((cells - decs - {acc}) | consts))
    return Sweep(role, acc, ups, rate[0] if rate else None, bounds)


def _refs_cell(ir, base):
    """True if ``ir`` reads memory cell ``base`` anywhere."""
    if not isinstance(ir, tuple):
        return False
    if ir[0] == "mem" and ann._const_base(ir[1]) == base:
        return True
    return any(_refs_cell(k, base) for k in ir[1:] if isinstance(k, tuple)) or (
        ir[0] == "op" and any(_refs_cell(k, base) for k in ir[2])
    )


def _merge_sweeps(sweeps):
    """Collapse per-store sweeps sharing (plane, acc); union updates and bounds."""
    out = {}
    for w in sweeps:
        k = (w.plane, w.acc)
        if k not in out:
            out[k] = w
            continue
        p = out[k]
        ups = p.updates + tuple(u for u in w.updates if u not in p.updates)
        bounds = tuple(sorted(set(p.bounds) | set(w.bounds)))
        out[k] = Sweep(w.plane, w.acc, ups, p.rate or w.rate, bounds)
    return list(out.values())


def _probe(role, val, env, cells, spans):
    """Snapshot a freq store's resolved root and pitch provenance at store time."""
    root = _resolve(val, env)
    mem = isinstance(root, tuple) and root[0] == "mem"
    op = isinstance(root, tuple) and root[0] == "op" and root[1] in ("INT_ADD", "INT_SUB")
    src = ann._const_base(root[1]) if mem else 0
    tables = set()
    ann._backtrace(val, env, cells, tables, set())
    return role, src, _in_pitch(tables, spans), mem, op


def _label(probe, accum):
    """Label a probed freq store: ``slide`` (modulation), ``note`` (pitch), else."""
    role, src, pitch, mem, op = probe
    slide = src in accum or op
    kind = "slide" if slide else "note" if mem and pitch else "other"
    return FreqDriver(role, src, pitch, slide, kind)


def _static_filter(model):
    """Filter registers written only in ``sid-init`` (static routing/mode/volume)."""
    prologue = getattr(model, "prologue", ()) or ()
    return {r: v for r, v in prologue if r & 0x1F in _FILTER_REGS}


def analyze(model):
    """Union the recovered song model over every pass-1 procedure of ``model``."""
    counters, freq, trans, pw, cutoff = [], [], [], [], []
    for stmts in ann.model_procs(model):
        sm = recover(stmts, model)
        counters.extend(sm.counters)
        freq.extend(sm.freq)
        trans.extend(sm.control.transitions)
        pw.extend(sm.pw)
        cutoff.extend(sm.filter.cutoff)
    return SongModel(
        counters, freq, _automaton(trans), pw, FilterModel(cutoff, _static_filter(model))
    )
