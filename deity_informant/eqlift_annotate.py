"""Table-role labelling by dataflow provenance over pass-1 statement lists.

Each SID register store's value is backtraced through the asg/scratch-store
dataflow to the constant-base data tables its bytes flow from; freq tables get an
equal-temperament check that confirms the pitch table and counts its octaves."""

from __future__ import annotations

import re
from collections import namedtuple

import numpy as np

from . import datadecl
from . import frameproc
from . import sidprog

SID_LO, SID_HI = 0xD400, 0xD418
_SEMITONE = 2.0 ** (1.0 / 12.0)
_VOICE_ROLES = (
    "freq_lo",
    "freq_hi",
    "pw_lo",
    "pw_hi",
    "control",
    "attack_decay",
    "sustain_release",
)
_GLOBAL_ROLES = {0xD415: "cutoff_lo", 0xD416: "cutoff_hi", 0xD417: "res_filt", 0xD418: "mode_vol"}
_FREQ_ROLES = frozenset(("freq_lo", "freq_hi"))

TableRoles = namedtuple("TableRoles", "roles pitch")


def _role_of(base):
    """SID register role named by the constant store base, else None."""
    if base in _GLOBAL_ROLES:
        return _GLOBAL_ROLES[base]
    off = base - SID_LO
    return _VOICE_ROLES[off] if 0 <= off < len(_VOICE_ROLES) else None


def _const_base(addr):
    """Sum of the constant summands of a pass-1 address expression (table base)."""
    k = addr[0]
    if k == "const":
        return addr[1]
    if k == "op" and addr[1] in ("INT_ADD", "INT_SUB"):
        return sum(_const_base(a) for a in addr[2] if isinstance(a, tuple))
    return 0


def _is_table(base):
    return base >= 0x100 and not SID_LO <= base <= SID_HI + 4


def _backtrace(expr, env, cells, tables, seen):
    """Collect data-table bases feeding ``expr`` via reaching asg/scratch defs."""
    if not isinstance(expr, tuple) or expr in seen:
        return
    seen.add(expr)
    k = expr[0]
    if k == "loc":
        d = env.get(expr[1])
        if d is not None:
            _backtrace(d, env, cells, tables, seen)
    elif k == "mem":
        base = _const_base(expr[1])
        if base in cells:
            _backtrace(cells[base], env, cells, tables, seen)
        elif _is_table(base):
            tables.add(base)
        _backtrace(expr[1], env, cells, tables, seen)
    elif k == "op":
        for kid in expr[2]:
            _backtrace(kid, env, cells, tables, seen)


def _decls(model):
    decls = getattr(model, "data_decls", None)
    if decls is None:
        decls, _al = datadecl.declarations(model)
    return decls


def _avail(decls, base):
    """Bytes from ``base`` to the end of the table declaration containing it."""
    for d in decls:
        if d["kind"] == "table" and d["base"] <= base < d["base"] + d["size"]:
            return d["base"] + d["size"] - base
    return 0


def _u8(mem0, base, n):
    return np.frombuffer(bytes(mem0[base : base + n]), dtype=np.uint8).astype(np.float64)


def _pitch_words(mem0, lo, hi, decls):
    """16-bit note words from a freq_lo/freq_hi table pair, or None.

    Handles both interleaved 16-bit tables (hi == lo + 1) and Follin-style
    separate lo/hi byte blocks (paired by index)."""
    nlo = _avail(decls, lo)
    if not nlo:
        return None
    if hi == lo + 1:
        n = nlo // 2
        buf = np.frombuffer(bytes(mem0[lo : lo + 2 * n]), dtype="<u2").astype(np.float64)
        return buf if n >= 24 else None
    nhi = _avail(decls, hi)
    if hi <= lo or not nhi:
        return None
    n = min(nlo, nhi)
    return _u8(mem0, lo, n) + 256.0 * _u8(mem0, hi, n) if n >= 24 else None


def et_check(words):
    """Equal-temperament verdict for a note-frequency word array.

    pitch_table holds when the median semitone ratio is within 0.01 of 2**(1/12)
    and the median octave (n->n+12) ratio is within 0.05 of 2."""
    if words is None or len(words) < 24:
        return {"pitch_table": False, "octaves": 0, "reference": 0}
    ref = int(words[0])
    nz = words[:-1] > 0
    step = np.median(words[1:][nz] / words[:-1][nz]) if nz.any() else 0.0
    oz = words[:-12] > 0
    octr = np.median(words[12:][oz] / words[:-12][oz]) if oz.any() else 0.0
    good = abs(step - _SEMITONE) <= 0.01 and abs(octr - 2.0) <= 0.05
    return {"pitch_table": bool(good), "octaves": len(words) // 12 if good else 0, "reference": ref}


def _pitch_tables(roles, model):
    """{lo_base: ET verdict} for every freq_lo/freq_hi table pair that pairs up."""
    decls = _decls(model)
    los = [b for b, rs in roles.items() if "freq_lo" in rs]
    his = [b for b, rs in roles.items() if "freq_hi" in rs]
    pitch = {}
    for lo in los:
        cands = [et_check(_pitch_words(model.mem0, lo, hi, decls)) for hi in his]
        good = [info for info in cands if info["pitch_table"]]
        if good:
            pitch[lo] = max(good, key=lambda info: info["octaves"])
    return pitch


def model_procs(model):
    """Pass-1 statement lists for every procedure of a committed model."""
    aliases = getattr(model, "symbols", None)
    if aliases is None:
        _decls, aliases = datadecl.declarations(model)
    trees, labels, view = sidprog._model_trees(model)
    procs = []
    for _entry, root in trees:
        conv = frameproc._Conv(frameproc._Names(aliases))
        builder = frameproc._Builder(labels, set(model.dispatch_sets), view, conv)
        procs.append(builder.proc(root))
    return procs


def table_roles(stmts, model):
    """Map data-table base -> SID roles its bytes feed, plus pitch-table checks.

    Flow-sensitive over one procedure's pass-1 statement list: each SID-register
    store's value is backtraced through the current reaching asg/scratch-store
    definitions to the constant data-table bases it reads."""
    roles, env, cells = {}, {}, {}

    def visit(sl):
        for s in sl:
            k = s[0]
            if k == "asg":
                env[s[1]] = s[2]
            elif k == "st":
                role = _role_of(_const_base(s[1]))
                if role:
                    tables = set()
                    _backtrace(s[2], env, cells, tables, set())
                    for t in tables:
                        roles.setdefault(t, set()).add(role)
                elif s[1][0] == "const":
                    cells[s[1][1]] = s[2]
            for b in frameproc._stmt_bodies(s):
                visit(b)

    visit(stmts)
    pitch = _pitch_tables(roles, model)
    return TableRoles({b: sorted(rs) for b, rs in roles.items()}, pitch)


def aggregate(procs, model):
    """Union the ``table_roles`` of every pass-1 procedure into one TableRoles.

    ``procs`` is an iterable of pass-1 statement lists (one per procedure)."""
    roles, pitch = {}, {}
    for stmts in procs:
        tr = table_roles(stmts, model)
        for base, rs in tr.roles.items():
            roles.setdefault(base, set()).update(rs)
        for base, info in tr.pitch.items():
            if info["pitch_table"] or base not in pitch:
                pitch[base] = info
    return TableRoles({b: sorted(rs) for b, rs in roles.items()}, pitch)


_HEADER = re.compile(r"^(\s*(?:table|stream)\s+)(\S+)(\[\d+\].*:)$")


def _resolver(model, aliases):
    """Map a data-header name (canonical or aliased) back to its base address."""
    rev = {name: cell for cell, name in (aliases or {}).items()}

    def resolve(name):
        addr = sidprog._name_addr(name)
        return addr if addr is not None else rev.get(name)

    return resolve


def annotate_lines(lines, table_roles_result, model, aliases):
    """Append ``role ...`` / pitch notes to each labelled data-table header line."""
    resolve = _resolver(model, aliases)
    tr = table_roles_result
    out = []
    for ln in lines:
        m = _HEADER.match(ln)
        base = resolve(m.group(2)) if m else None
        if base is not None and base in tr.roles:
            ln = "%s  role %s" % (ln, ", ".join(tr.roles[base]))
            info = tr.pitch.get(base)
            if info and info["pitch_table"]:
                ln = "%s ; pitch table: %d octaves, equal-tempered" % (ln, info["octaves"])
        out.append(ln)
    return out
