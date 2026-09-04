"""A statement list that is a tree: the nested statements of a level's own regions.

A ``loop`` or a ``region`` carries a statement list of its own, so the passes
that read or rewrite a stream's statements walk it rather than its top level.
"""

from __future__ import annotations


def body(s):
    """The statement list one statement carries, where it carries one."""
    return s.get("region") or (s.get("loop") or {}).get("body")


def stmts(rows):
    """Every statement of a region tree, the nested ones included, in order."""
    out = []
    for r in rows or ():
        out.append(r)
        out += stmts(body(r))
    return out


def kept(rows, live):
    """A region tree with every statement ``live`` refuses dropped, nesting kept."""
    out = []
    for r in rows or ():
        if "region" in r:
            got = kept(r["region"], live)
            if got:
                out.append({**r, "region": got})
        elif "loop" in r:
            got = kept(r["loop"]["body"], live)
            if got:
                out.append({**r, "loop": {**r["loop"], "body": got}})
        elif live(r):
            out.append(r)
    return out
