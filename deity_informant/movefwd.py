"""Redundant data-move pass: relabel SID-shadow-buffer stores onto the SID.

A driver that stages register writes in a RAM buffer and bulk-flushes it
(`sid[$D400+i] = B[i]`) hides provenance behind the copy; this relabels stores
to such a buffer onto the SID base, analysis-only. See docs/tracker.md §7a."""

from . import eqlift_annotate as ann
from . import frameproc


def _walk_stores(stmts, env, fn):
    """Flow-sensitive walk feeding ``fn(addr, val, env)`` at each store."""
    for s in stmts:
        if s[0] == "asg":
            env[s[1]] = s[2]
        elif s[0] == "st":
            fn(s[1], s[2], env)
        if s[0] == "if":
            _walk_stores(s[3], env, fn)
            _walk_stores(s[4], env, fn)
        else:
            for b in frameproc._stmt_bodies(s):
                _walk_stores(b, env, fn)


def _resolve(expr, env, seen=None):
    """Follow ``loc`` defs to the first non-``loc`` expression."""
    seen = seen or set()
    while isinstance(expr, tuple) and expr[0] == "loc" and expr[1] not in seen:
        seen.add(expr[1])
        if env.get(expr[1]) is None:
            break
        expr = env[expr[1]]
    return expr


def _store_bases(procs):
    """Every table base that is a store target in play (writable, not read-only)."""
    bases = set()

    def note(addr, _val, _env):
        b = ann._const_base(addr)
        if ann._is_table(b):
            bases.add(b)

    for stmts in procs:
        _walk_stores(stmts, {}, note)
    return bases


def _index(addr):
    """The varying (non-constant) part of an address expression, or None."""
    if not isinstance(addr, tuple) or addr[0] == "const":
        return None
    if addr[0] == "op" and addr[1] in ("INT_ADD", "INT_SUB"):
        nonc = [k for k in addr[2] if not (isinstance(k, tuple) and k[0] == "const")]
        if len(nonc) == 1:
            return nonc[0]
        return ("op", addr[1], tuple(nonc), addr[3]) if nonc else None
    return addr


def sid_shadows(procs):
    """{buffer_base: sid_base} for each buffer flushed by a parallel `sid[i]=B[i]`."""
    writable = _store_bases(procs)
    shadows = {}

    def scan(addr, val, env):
        sid = ann._const_base(addr)
        idx = _index(addr)
        if ann._role_of(sid) is None or idx is None:
            return
        root = _resolve(val, env)
        if isinstance(root, tuple) and root[0] == "mem":
            buf = ann._const_base(root[1])
            if buf in writable and ann._is_table(buf) and _index(root[1]) == idx:
                shadows.setdefault(buf, sid)

    for stmts in procs:
        _walk_stores(stmts, {}, scan)
    return shadows


def _rebase(ir, shadows):
    """Rewrite any const buffer base in ``ir`` to its flushed SID base."""
    if not isinstance(ir, tuple):
        return ir
    k = ir[0]
    if k == "const":
        return ("const", shadows.get(ir[1], ir[1]), ir[2])
    if k == "loc":
        return ir
    if k == "mem":
        return ("mem", _rebase(ir[1], shadows), ir[2])
    if k == "op":
        return ("op", ir[1], tuple(_rebase(x, shadows) for x in ir[2]), ir[3])
    return ir


def _map_tree(stmts, shadows):
    """Rebuild the statement tree, relabelling store addresses through shadows."""
    out = []
    for s in stmts:
        if s[0] == "st" and ann._const_base(s[1]) in shadows:
            s = ("st", _rebase(s[1], shadows), s[2])
        k = s[0]
        if k == "if":
            s = ("if", s[1], s[2], _map_tree(s[3], shadows), _map_tree(s[4], shadows))
        elif k == "loop":
            s = ("loop", _map_tree(s[1], shadows))
        elif k == "for":
            s = s[:4] + (_map_tree(s[4], shadows),) + s[5:]
        elif k == "callb":
            s = s[:3] + (_map_tree(s[3], shadows),) + s[4:]
        elif k in ("opsw", "swc"):
            s = (k, s[1], [(lbl, _map_tree(b, shadows)) for lbl, b in s[2]]) + s[3:]
        elif k == "swg":
            s = (k, [(lbl, _map_tree(b, shadows)) for lbl, b in s[1]]) + s[2:]
        out.append(s)
    return out


def list_procs(model):
    """The un-lifted pass-1 procedures (baseline for comparison)."""
    return list(ann.model_procs(model))


def lifted_procs(model):
    """Pass-1 procedures with SID-shadow stores relabelled onto the SID."""
    procs = list(ann.model_procs(model))
    shadows = sid_shadows(procs)
    if not shadows:
        return procs, shadows
    return [_map_tree(stmts, shadows) for stmts in procs], shadows
