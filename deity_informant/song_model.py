"""Song-synthesis model recovery over eqlift pass-1 graphs.

Per-voice cadence counters (decrement/increment cells plus their reload sources)
and SID frequency-driver classification, reusing eqlift_annotate dataflow
provenance (env/cells backtrace, pitch-table check)."""

from collections import namedtuple

from . import eqlift_annotate as ann
from . import frameproc

_INC, _DEC = 0x01, 0xFF
_FREQ = frozenset(("freq_lo", "freq_hi"))


def _cell(base):
    """A RAM cell that may hold a counter/accumulator (excludes SID and 0/1)."""
    return base >= 0x02 and not ann.SID_LO <= base <= ann.SID_HI + 4


Counter = namedtuple("Counter", "base kind reload")
FreqDriver = namedtuple("FreqDriver", "role source pitch slide kind")
SongModel = namedtuple("SongModel", "counters freq")


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


def _self_add(expr, base, env):
    """True if ``expr`` adds/subtracts to a value that itself reads ``base``."""
    if not (isinstance(expr, tuple) and expr[0] == "op"):
        return False
    if expr[1] not in ("INT_ADD", "INT_SUB"):
        return False
    for kid in expr[2]:
        r = _resolve(kid, env)
        if isinstance(r, tuple) and r[0] == "mem" and ann._const_base(r[1]) == base:
            return True
    return False


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


def recover(stmts, model):
    """Recover cadence counters and freq drivers from one pass-1 statement list."""
    tr = ann.table_roles(stmts, model)
    spans = _pitch_ranges(tr, model)
    env, cells, steps, reloads, accum, stores = {}, {}, {}, {}, set(), []

    def visit(sl):
        for s in sl:
            if s[0] == "asg":
                env[s[1]] = s[2]
            elif s[0] == "st":
                _store(s)
            for b in frameproc._stmt_bodies(s):
                visit(b)

    def _store(s):
        base = ann._const_base(s[1])
        role = ann._role_of(base)
        if role in _FREQ:
            stores.append(_probe(role, s[2], env, cells, spans))
            return
        if not _cell(base):
            return
        if s[1][0] == "const":
            cells[base] = s[2]
        st = _step(s[2], env)
        selfadd = _self_add(s[2], base, env)
        if st and st[0] == base:
            steps.setdefault(base, st[1])
        if selfadd:
            accum.add(base)
        elif not (st and st[0] == base):
            src = _read_base(s[2], env)
            if src:
                reloads.setdefault(base, src)

    visit(stmts)
    freq = [_label(p, accum) for p in stores]
    counters = [Counter(b, k, reloads.get(b)) for b, k in sorted(steps.items())]
    return SongModel(counters, freq)


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


def analyze(model):
    """Union the recovered song model over every pass-1 procedure of ``model``."""
    counters, freq = [], []
    for stmts in ann.model_procs(model):
        sm = recover(stmts, model)
        counters.extend(sm.counters)
        freq.extend(sm.freq)
    return SongModel(counters, freq)
