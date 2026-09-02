"""B7 -- the recognition pass: T1's accumulator plane joined to the lowered rows.

T1 states each accumulator's cell, sites, delta, bound, policy and phase, and
:mod:`.lower` carries each assignment's own site. The join turns a run of
``sets`` rows into one section 5 ``Acc`` and takes the rows out of the stream.
"""

from __future__ import annotations

from .algebra import (
    constant_under,
    extends,
    follow,
    peel,
    prefix,
    read_of,
    rewrite,
    target_of,
    unsplit,
    _key,
)
from .build import SITES, _cellnames


class Acc:
    """One T1 accumulator against the lowered rows: the record, or the refusal."""

    def __init__(self, join, rec):
        self.j, self.a = join, rec
        self.id, self.width = rec["id"], int(rec["width"])
        self.sites = set(rec.get("sites") or ())
        self.regions = list(rec.get("regions") or [rec["cell"]["region"]])
        self.lo = join.cellname(int(rec["cell"]["addr"].lstrip("$"), 16))
        self.hi = self._hi()
        self.why, self.record, self.stream = None, None, None
        self.rows, self.drop, self.flag, self.start = [], [], None, 0

    def _hi(self):
        """The high half's own cell, where T1 states a second region for a word."""
        other = [r for r in self.regions if r != self.a["cell"]["region"]]
        if self.width != 16 or len(other) != 1 or other[0] not in self.j.byid:
            return None
        return self.j.cellname(self.j.byid[other[0]].base)

    def refuse(self, why):
        self.why = why

    def find(self):
        """The lowered rows whose stores are this accumulator's own."""
        want = {target_of(self.lo): "lo"}
        if self.hi:
            want[target_of(self.hi)] = "hi"
        got = {}
        for name, st in self.j.streams.items():
            for i, r in enumerate(st["rows"]):
                for k, s in enumerate(r["sets"]):
                    if s[0] in want:
                        got.setdefault(name, []).append((i, k, r[SITES][k]))
        home = [n for n, v in got.items() if {x[2] for x in v} & self.sites]
        if len(home) > 1:
            return self.refuse("its own sites stand in %d streams" % len(home))
        if home:
            self.stream, self.rows = home[0], got[home[0]]
        return self

    def build(self):
        """The section 5 record this accumulator's rows state, or a refusal."""
        if self.width not in (8, 16):
            return self.refuse("width %d is not a section 5 width" % self.width)
        if self.width == 16 and self.hi is None:
            return self.refuse("T1 names no second region for the word")
        if self.lo is None:
            return self.refuse("no section 5 cell holds it")
        if self.find() is None:
            return None
        if not self.rows:
            return self.j.upgrade(self)
        if "rank" not in self.j.streams[self.stream]:
            return self.refuse("its rows stand outside the machine's rank order")
        rows = self.j.streams[self.stream]["rows"]
        deltarows = sorted({i for i, _k, s in self.rows if s in self.sites})
        if not deltarows:
            return self.refuse("no store of its own is a T1 site")
        own = prefix(rows[i]["when"] for i in deltarows)
        idx = sorted(
            {
                i
                for i, _k, _s in self.rows
                if i <= deltarows[-1] and (i >= deltarows[0] or extends(own, rows[i]["when"]))
            }
        )
        self.start, when = idx[0], prefix(rows[i]["when"] for i in idx)
        prod, self.drop = self.j.produce(self, self.stream, idx[0], when)
        if not prod:
            return self.refuse("T0 names no write of its own cells")
        self.drop += [(self.stream, i, k) for i, k, _s in self.rows if i in idx]
        stop = max(i for n, i, _k in self.drop if n == self.stream)
        got = self.fields(rows, idx, deltarows, when, stop)
        if got is None:
            return None
        got["produce"] = prod
        self.record = got
        return got

    def fields(self, rows, idx, deltarows, when, stop):
        """The record's own fields: delta and its guard, policy, phase, flag."""
        out = {
            "site": sorted(self.sites)[0],
            "rank": 0,
            "cell": self.lo,
            "target": self.a["target"]["register"].split("_")[0],
            "width": self.width,
            "bound": {k: self.a["bound"][k] for k in ("interval", "from", "witness")},
            "rate": 1,
            "scope": self.a.get("scope") or "voice",
            "when": when,
        }
        d = self.delta(rows, deltarows)
        if d is None:
            return None
        out.update(d)
        gone = {s[0][1:] for i in range(idx[0] + 1, stop + 1) for s in rows[i]["sets"]}
        guard = [
            t
            for t in prefix(rows[i]["when"] for i in deltarows)[len(when) :]
            if not _cellnames([t]) & gone
        ]
        if guard:
            out["delta_when"] = guard
        pol = self.policy(rows, idx, deltarows, when)
        if pol is None:
            return None
        out.update(pol)
        ph = self.phase()
        if ph is not None:
            out["phase"] = ph
        return out if self.carry(rows, deltarows, out) is not None else None

    def delta(self, rows, deltarows):
        """§5's ``delta``: T1's form over the object's cells, or the store peeled."""
        d = self.a.get("delta") or {}
        if d.get("kind") == "repeat":
            step, n = self.j.cellread(d["step"]["cell"]), self.j.cellread(d["n"])
            if step is None or n is None:
                return self.refuse("no cell holds the step or the count")
            return {"delta": {"repeat": [step, n]}}
        if d.get("kind") == "field":
            cell = self.j.cellread(d["cell"])
            if cell is None:
                return self.refuse("no cell holds the delta")
            return {"delta": {"field": [cell, int(d["mask"])]}}
        if self.width != 8:
            return self.refuse("a %s delta on a word is no section 5 form" % d.get("kind"))
        expr = next(v for t, v in rows[deltarows[0]]["sets"] if t == target_of(self.lo))
        got = peel(expr, read_of(self.lo), self.j.copies)
        if got is None:
            return self.refuse("the store is no accumulation on its own cell")
        return {"delta": got}

    def policy(self, rows, idx, deltarows, when):
        """§5's ``policy``: T1's word, and the value the run's own store reloads."""
        pol = self.a.get("policy")
        if pol in ("wrap", "reflect", "reflect-complement"):
            return {"policy": pol}
        if pol != "reload":
            return self.refuse("policy %r is no section 5 policy" % pol)
        seen = [i for i in idx if i not in deltarows]
        if len(seen) != 1 or rows[seen[0]]["when"] != when:
            return self.refuse("the reload is not one store under the record's own guard")
        val = self.word(rows[seen[0]])
        return {"policy": {"reload": val}} if val is not None else self.refuse("no reload value")

    def word(self, row):
        """The value one row stores, as a word where the accumulator has two halves."""
        lo = next((v for t, v in row["sets"] if t == target_of(self.lo)), None)
        if self.hi is None:
            return lo
        hi = next((v for t, v in row["sets"] if t == target_of(self.hi)), None)
        if lo is None or hi is None:
            return None
        return unsplit(lo, hi) or {"u16": [lo, hi]}

    def phase(self):
        """§5's ``phase``: the bit of a live cell T1 states the direction from."""
        ph = self.a.get("phase") or {}
        if ph.get("kind") != "bit" or ph.get("cell") is None:
            return None
        cell = self.j.cellread(ph["cell"])
        return None if cell is None else {"bit": [cell, int(ph["bit"])]}

    def carry(self, rows, deltarows, out):
        """§5's ``flag``: the carry a repeated addition leaves the next producer."""
        if "repeat" not in (out.get("delta") or {}):
            return out
        held = set.intersection(
            *[{s[0][1:] for s in rows[i]["sets"] if s[0][:1] == "@"} for i in deltarows]
        ) - {self.lo, self.hi}
        got = sorted(held & self.j.readafter(self.stream, max(deltarows)))
        if not got:
            return out
        if len(got) > 1:
            return self.refuse("%d cells of its own loop outlive it" % len(got))
        seeds = [
            v
            for i in range(self.start, min(deltarows))
            for t, v in rows[i]["sets"]
            if t == "@" + got[0]
        ]
        guards = self.j.inline(out["when"] + out.get("delta_when", []))
        seed = constant_under(self.j.inline(seeds[-1]), guards) if seeds else None
        if seed is None:
            return self.refuse("the carry it enters the loop with is no constant")
        self.flag = (got[0], got[0].lstrip("t"), max(deltarows))
        out["flag"] = {"name": self.flag[1], "seed": seed}
        return out


class Join:
    """T1's accumulator plane against the lowered streams of one lift."""

    def __init__(self, art, view, cells, ph):
        self.art, self.cells, self.ph = art, cells, ph
        self.byid, self.streams, self.accs = view.by_id(), ph.streams, ph.accs
        self.t0 = art["t0"].get("writes") or []
        self.report, self.wide, self.merged, self.names = [], [], [], {}
        self.defs = self._defs()
        self.copies = {k: v for k, v in self.defs.items() if _key(v) is not None}

    def _defs(self):
        """``{cell: value}`` for every cell one lowered ``sets`` alone writes."""
        seen = {}
        for st in self.streams.values():
            for r in st["rows"]:
                for t, v in r["sets"]:
                    if t[:1] == "@":
                        seen[t[1:]] = None if t[1:] in seen else v
        return {k: v for k, v in seen.items() if isinstance(v, dict)}

    def inline(self, e, depth=4):
        """One expression with the cells one ``sets`` alone writes substituted."""
        if depth <= 0:
            return e
        if isinstance(e, dict):
            k = _key(e)
            if k is not None and k[0] == "cell" and k[1] in self.defs:
                return self.inline(self.defs[k[1]], depth - 1)
            return {a: self.inline(v, depth) for a, v in e.items()}
        return [self.inline(x, depth) for x in e] if isinstance(e, list) else e

    def cellname(self, addr):
        return self.cells.name(addr, True)

    def cellread(self, spec):
        """One T1 cell as the object reads it: its name, and a word where it is one."""
        if not spec:
            return None
        addr = int(spec["addr"].lstrip("$"), 16)
        lo = self.cellname(addr)
        if lo is None:
            return None
        if int(spec.get("width") or 1) != 2:
            return read_of(lo)
        hi = self.cellname(addr + 1)
        return None if hi is None else {"u16": [read_of(lo), read_of(hi)]}

    def ranked(self):
        """The machine's streams in the rank order the object gives them."""
        got = [(st["rank"], n) for n, st in self.streams.items() if "rank" in st]
        return [n for _r, n in sorted(got)]

    def walk(self, stream, row, when, rank=None):
        """The rows the run's own guard still covers, from one row on, in rank order."""
        order = self.ranked()
        if stream in order:
            order = order[order.index(stream) :]
        elif rank is not None:
            order = [n for n in order if self.streams[n]["rank"] > rank]
        for k, name in enumerate(order):
            for i, r in enumerate(self.streams[name]["rows"]):
                if not k and stream == name and i < row:
                    continue
                if not extends(r["when"], when):
                    return
                yield name, i, r

    def readafter(self, stream, row):
        """Every cell some reader past one row of one stream still has."""
        out = set()
        order = self.ranked()
        tail = order[order.index(stream) :] if stream in order else [stream]
        for k, name in enumerate(tail):
            for i, r in enumerate(self.streams[name]["rows"]):
                if not k and i <= row:
                    continue
                out |= _cellnames(r.get("when", [])) | _cellnames([s[1] for s in r["sets"]])
        for a in self.accs.values():
            out |= _cellnames(list(a.values()))
        return out

    def sites_of(self, acc):
        """``{site: T0 write}`` for the writes whose cells are this accumulator's."""
        return {
            w["site"]["pc"]: w
            for w in self.t0
            if {c["region"] for c in (w.get("cells") or ())} & set(acc.regions)
        }

    def produce(self, acc, stream, row, when, rank=None):
        """``produce`` from T0, and the ``sets`` the record's own write replaces.

        The write is the one T0 names, and the halves of a word it names once:
        a register store whose value is a read of the accumulator's own cell, or
        the very value the accumulator's own store took.
        """
        want, out, seen = self.sites_of(acc), [], list(self.walk(stream, row, when, rank))
        for _n, _i, r in seen:
            for k, s in enumerate(r["sets"]):
                w = want.get(r[SITES][k])
                if w is None or s[0][:1] in "@#!":
                    continue
                for part in self.parts(acc, w):
                    if part not in out:
                        out.append(part)
        if not out:
            return [], []
        regs = {p[0] for p in out}
        reads = [read_of(acc.lo)] + ([read_of(acc.hi)] if acc.hi else [])
        halves = {target_of(acc.lo)} | ({target_of(acc.hi)} if acc.hi else set())
        drop = []
        for name, i, r in seen:
            own = [v for t, v in r["sets"] if t in halves]
            for k, s in enumerate(r["sets"]):
                same = follow(s[1], self.copies) in reads or s[1] in own
                if s[0] in regs and (r[SITES][k] in want or same):
                    drop.append((name, i, k))
        return out, drop

    @staticmethod
    def parts(acc, w):
        """One T0 write as ``produce`` entries: the register, and the half it takes."""
        reg = w["register"]
        if acc.width == 8:
            return [[reg, "byte"]]
        if int(w["site"].get("width") or 1) == 2:
            pair = [[reg + "_lo", "lo"], [reg + "_hi", "hi"]]
            return pair[::-1] if w["site"].get("hifirst") else pair
        regs = {c["region"] for c in (w.get("cells") or ())}
        return [[reg, "hi" if acc.a["cell"]["region"] not in regs else "lo"]]

    def upgrade(self, acc):
        """A stand-in assignment restated: T1's delta, bound and policy on the record.

        ``ins.pw`` is the only instrument-scoped cell the schema can be assigned,
        so :func:`.build.acc_of` states the store as a reload; T1 makes it the
        section 5 record the reload stands for.
        """
        name = next((k for k, v in self.accs.items() if v.get("site") in acc.sites), None)
        if name is None:
            return acc.refuse("no lowered row stores it")
        rec = self.accs[name]
        got = peel(rec["policy"]["reload"], read_of(rec["cell"]), self.copies)
        if got is None:
            return acc.refuse("the store is no accumulation on its own cell")
        prod, acc.drop = self.produce(acc, None, 0, rec["when"], rec["rank"])
        if not prod:
            return acc.refuse("T0 names no write of its own cells")
        rec.update(
            {
                "policy": acc.a["policy"],
                "delta": got,
                "produce": prod,
                "width": acc.width,
                "bound": {k: acc.a["bound"][k] for k in ("interval", "from", "witness")},
            }
        )
        acc.record, acc.stream = rec, None
        self.names[acc.id] = name
        return rec

    def apply(self, accs):
        """The rows the records replace, taken out; the ranks renumbered over both."""
        drop = {x for a in accs for x in a.drop}
        for name, st in self.streams.items():
            for i, r in enumerate(st["rows"]):
                keep = [k for k in range(len(r["sets"])) if (name, i, k) not in drop]
                r["sets"] = [r["sets"][k] for k in keep]
                r[SITES] = [r[SITES][k] for k in keep]
        for a in accs:
            if a.flag:
                self.carryflag(a)
            if a.hi and a.hi != a.lo:
                self.merge(a)
        self.split(accs)

    def carryflag(self, acc):
        """The loop's own carry as §5's flag: the cell its readers read as one."""
        cell, name, row = acc.flag
        sub = {("cell", cell): {"flag": name}}
        for _n, _i, r in self.walk(acc.stream, row + 1, []):
            r["when"] = rewrite(r.get("when", []), sub)
            r["sets"] = rewrite(r["sets"], sub)
        for a in self.accs.values():
            for k in ("when", "policy", "delta", "delta_when", "step_when"):
                if k in a:
                    a[k] = rewrite(a[k], sub)

    def merge(self, acc):
        """Two named halves as one 16-bit cell: §5's ``.hi``/``.lo`` on one name."""
        lo, hi = acc.lo, acc.hi
        sub = {_key(read_of(lo)): {"cell": lo + ".lo"}, _key(read_of(hi)): {"cell": lo + ".hi"}}
        put = {target_of(lo): target_of(lo) + ".lo", target_of(hi): target_of(lo) + ".hi"}
        for st in self.streams.values():
            for r in st["rows"]:
                r["when"] = rewrite(r.get("when", []), sub)
                r["sets"] = [[put.get(t, t), rewrite(v, sub)] for t, v in r["sets"]]
        for a in self.accs.values():
            for k in list(a):
                if k != "cell":
                    a[k] = rewrite(a[k], sub)
        self.wide.append(lo.lstrip("#"))
        self.merged.append((lo, hi))

    def split(self, accs):
        """The stream each record sits inside, cut at it; every rank renumbered."""
        keys = {n: float(self.streams[n]["rank"]) for n in self.ranked()}
        keys.update({"#" + k: float(a["rank"]) for k, a in self.accs.items()})
        inside = {}
        for a in accs:
            if a.stream is not None:
                inside.setdefault(a.stream, []).append(a)
        for name, group in inside.items():
            st, base = self.streams.pop(name), keys.pop(name)
            group.sort(key=lambda a: a.start)
            cuts = [0] + [a.start + 1 for a in group] + [len(st["rows"])]
            for j in range(len(cuts) - 1):
                rows = st["rows"][cuts[j] : cuts[j + 1]]
                if rows:
                    self.streams["%s_%d" % (name, j)] = {**st, "rows": rows}
                    keys["%s_%d" % (name, j)] = base + j / (len(cuts) + 1.0)
            for j, a in enumerate(group):
                keys["#" + self.name_of(a)] = base + (j + 0.5) / (len(cuts) + 1.0)
        for i, key in enumerate(sorted(keys, key=lambda k: keys[k])):
            if key.startswith("#"):
                self.accs[key[1:]]["rank"] = i
            else:
                self.streams[key]["rank"] = i

    def name_of(self, acc):
        """The key ``accs`` declares one record under; a new record takes a new one."""
        got = self.names.get(acc.id)
        if got is None:
            got = self.names[acc.id] = "acc%d" % len(self.accs)
            self.accs[got] = acc.record
        return got

    def run(self):
        """Every T1 accumulator joined: the records, and the refusals by name."""
        out = []
        for rec in self.art["t1"].get("accs") or ():
            acc = Acc(self, rec)
            acc.build()
            if acc.record is not None:
                self.name_of(acc)
                out.append(acc)
            self.report.append(
                {
                    "id": acc.id,
                    "cell": rec["cell"]["name"],
                    "sites": sorted(acc.sites),
                    "form": "acc" if acc.record is not None else "sets",
                    "why": acc.why,
                }
            )
        self.apply(out)
        return self.report
