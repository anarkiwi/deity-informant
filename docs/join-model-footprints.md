# The join model's footprint map (stage 3 landing 2, drafted)

What a join must forget is what the code it enters can write. The e-graph lift
havocs everything at a `call`, a `label` and a dynamic transfer because it has no
map of that; `Footprints` is the map, closed over the enumerated call/goto graph:
`of(pc)` is `(const cells, wild)` for the code entered at `pc` and everything it
enters in turn, and a procedure holding a transfer no edge map enumerates carries
`whole()` — the ⊤ every boundary has today.

Drafted during stage 3b's measurement and unlanded: landing 2 resurrects it,
gates it against the exemplars, and reads its weakenings against adoption §10's
"opaque-reset by default, weakened only by admitted argument". It is recorded
here because the class is the design, not the diff.

```python
_DYN = frozenset(("dcall", "dbr", "dgoto", "igoto"))  # transfers no edge map enumerates
_TOPFP = (frozenset(), True)  # the footprint that says nothing: a join forgets everything


class Footprints:
    """What entering at a pc may write, over the enumerated call/goto graph.

    ``of(pc)`` is ``(const cells, wild)`` for the code entered there and everything
    it enters in turn; a procedure holding dynamic control reaches every label and
    carries ``whole()``. An empty map is the ⊤ every call and label had before."""

    __slots__ = ("own", "calls", "dyn", "owner", "glob", "fp")

    def __init__(self, procs=()):
        self.own, self.calls, self.dyn, self.owner = {}, {}, {}, {}
        for entry, stmts in procs:
            self.owner.update(dict.fromkeys(_labels_of(stmts), entry))
            self.owner[entry] = entry
        for entry, stmts in procs:
            cells, tgts, dyn, wild = _scan(stmts, entry, self.owner)
            self.own[entry] = (frozenset(cells), wild)
            self.calls[entry], self.dyn[entry] = frozenset(tgts), dyn
        cells = frozenset().union(*(c for c, _w in self.own.values())) if self.own else frozenset()
        self.glob = (cells, not procs or any(w for _c, w in self.own.values()))
        self.fp = self._close()

    def _close(self):
        """The call graph's least fixpoint: a caller writes what its callees write."""
        cur = dict(self.own)
        for _round in range(len(self.own) + 1):
            nxt = {}
            for e, (cells, wild) in cur.items():
                for t in () if self.dyn[e] else self.calls[e]:
                    tc, tw = cur.get(self.owner.get(t), self.glob)
                    cells, wild = cells | tc, wild or tw
                nxt[e] = self.glob if self.dyn[e] else (cells, wild)
            if nxt == cur:
                break
            cur = nxt
        return cur

    def of(self, pc):
        """The footprint of entering at ``pc``; ⊤ where the map does not name it."""
        return self.fp.get(self.owner.get(pc), self.glob)

    def whole(self):
        """The footprint of control this map cannot follow: the whole program."""
        return self.glob


def _labels_of(stmts):
    out = []
    for s in stmts:
        if s[0] == "label":
            out.append(s[1])
        for b in E.frameproc._stmt_bodies(s):
            out.extend(_labels_of(b))
    return out


def _scan(stmts, entry, owner):
    """``(cells, pcs entered, dynamic, unplaceable)`` of one procedure's statements."""
    cells, tgts, flag = set(), set(), [False, False]

    def rec(sl):
        for s in sl:
            k = s[0]
            if k == "st":
                a = s[1]
                if a[0] == "const":
                    cells.add((a[1] & E._mask(a[2]), a[2], _ew(s[2])))
                else:
                    flag[1] = True
            elif k in ("call", "callb"):
                tgts.add(s[1])
            elif k == "goto" and owner.get(s[1], entry) != entry:
                tgts.add(s[1])  # control leaves for another list and writes what it writes
            elif k == "swc":
                tgts.update(int(lbl[1:], 16) for lbl in s[1])
            elif k in _DYN:
                flag[0] = True
            for b in E.frameproc._stmt_bodies(s):
                rec(b)

    rec(stmts)
    return cells, tgts, flag[0], flag[1]


```
