"""SIDL: a text-based lossless guarded-frame-template representation of a playroutine.

``build`` lifts a recorded playroutine into a :class:`Program` of straight-line
frame templates (checks + stores) plus only the data cells they read; ``dumps``/
``loads`` round-trip the canonical text; ``Program.run`` re-emits the write stream.
"""

from __future__ import annotations

import re

from . import expr as E
from .lifter import lift
from .recorder import record
from .vm import PcodeVM, run_sub

SID_OUTPUTS = frozenset(range(0xD400, 0xD419))

_REG_NAMES = {
    0: "A",
    1: "X",
    2: "Y",
    3: "SP",
    8: "C",
    9: "Z",
    10: "I",
    11: "D",
    12: "B",
    13: "V",
    14: "N",
}
_NAME_REGS = {v: k for k, v in _REG_NAMES.items()}
_CHAINS = {"INT_OR": "|", "INT_XOR": "^", "INT_AND": "&"}
_BINS = {"INT_LEFT": "<<", "INT_RIGHT": ">>"}
_CMPS = {"INT_EQUAL": "==", "INT_NOTEQUAL": "!=", "INT_LESS": "<", "INT_LESSEQUAL": "<="}
_KINDS = ("branch", "target", "place", "opcode")


class DispatchError(RuntimeError):
    """No template matched the current state (outside the recorded path
    vocabulary), or a needed cell/uni value is not in the program."""


class _Missing(KeyError):
    pass


def _reg_name(i):
    return _REG_NAMES.get(i, "r%d" % i)


def _reg_index(name):
    if name in _NAME_REGS:
        return _NAME_REGS[name]
    if name.startswith("r") and name[1:].isdigit():
        return int(name[1:])
    raise ValueError("unknown register %r" % name)


# ---- expression text (exact round-trip of the recorder's evolved forms) ------
def _hex(v, sz):
    return "$%0*X" % (2 * sz, v)


def _wsuf(sz):
    return "" if sz == 1 else ":%d" % sz


def fmt_expr(n, env=None):
    """Render an expression node (``env``: node -> ``let`` name substitutions);
    ``parse_expr`` is its exact inverse."""
    k = n[0]
    if k == "const":
        return _hex(n[1], n[2])
    if k == "reg":
        return _reg_name(n[1])
    if k == "uni":
        return "u%d%s" % (n[1], _wsuf(n[2]))
    if env is not None:
        name = env.get(n)
        if name is not None:
            return name
    if k == "mem":
        return "mem[%s]" % fmt_expr(n[1], env)
    if k == "cur":
        return "cur[%s]" % fmt_expr(n[1], env)
    mn, kids, sz = n[1], n[2], n[3]
    if mn == "INT_ZEXT":
        return "zext%d(%s)" % (sz, fmt_expr(kids[0], env))
    if mn == "INT_CARRY":
        return "carry(%s, %s)" % (fmt_expr(kids[0], env), fmt_expr(kids[1], env))
    if mn == "INT_ADD":
        half = 1 << (8 * sz - 1)
        parts = [fmt_expr(kids[0], env)]
        for c in kids[1:]:
            if c[0] == "const" and c[1] >= half:
                parts.append("- " + _hex((-c[1]) & E.mask(sz), sz))
            else:
                parts.append("+ " + fmt_expr(c, env))
        return "(%s)%s" % (" ".join(parts), _wsuf(sz))
    if mn == "INT_SUB":
        assert kids[1][0] != "const"  # simplify() folds const subtrahends into INT_ADD
        return "(%s - %s)%s" % (fmt_expr(kids[0], env), fmt_expr(kids[1], env), _wsuf(sz))
    if mn in _CHAINS:
        body = (" %s " % _CHAINS[mn]).join(fmt_expr(c, env) for c in kids)
        return "(%s)%s" % (body, _wsuf(sz))
    if mn in _CMPS:
        assert sz == 1
        return "(%s %s %s)" % (fmt_expr(kids[0], env), _CMPS[mn], fmt_expr(kids[1], env))
    body = "%s %s %s" % (fmt_expr(kids[0], env), _BINS[mn], fmt_expr(kids[1], env))
    return "(%s)%s" % (body, _wsuf(sz))


_TOKEN = re.compile(r"\$[0-9A-Fa-f]+|u\d+|[A-Za-z]\w*|<<|>>|<=|<-|==|!=|[-()\[\],:+|^&<=@]|\S")
_ADDSUB = frozenset("+-")
_CHAINOPS = {"|": "INT_OR", "^": "INT_XOR", "&": "INT_AND"}
_BINOPS = {"<<": "INT_LEFT", ">>": "INT_RIGHT"}
_CMPOPS = {"==": "INT_EQUAL", "!=": "INT_NOTEQUAL", "<": "INT_LESS", "<=": "INT_LESSEQUAL"}


class _Toks:
    def __init__(self, text, env=None):
        self.toks = _TOKEN.findall(text)
        self.i = 0
        self.env = env

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self):
        t = self.peek()
        if t is None:
            raise ValueError("unexpected end of expression")
        self.i += 1
        return t

    def expect(self, tok):
        t = self.next()
        if t != tok:
            raise ValueError("expected %r, got %r" % (tok, t))

    def done(self):
        return self.i >= len(self.toks)


def _const_tok(t):
    digits = t[1:]
    return ("const", int(digits, 16), max(1, len(digits) // 2))


def _suffix(ts):
    if ts.peek() == ":":
        ts.next()
        return int(ts.next())
    return 1


def _chain(ts):
    operands = [_atom(ts)]
    ops = []
    while ts.peek() != ")":
        ops.append(ts.next())
        operands.append(_atom(ts))
    ts.expect(")")
    sz = _suffix(ts)
    if not ops:
        raise ValueError("redundant parentheses")
    if set(ops) <= _ADDSUB:
        if ops == ["-"] and operands[1][0] != "const":
            return ("op", "INT_SUB", (operands[0], operands[1]), sz)
        kids = [operands[0]]
        for op, o in zip(ops, operands[1:]):
            if op == "-":
                if o[0] != "const":
                    raise ValueError("n-ary '-' of a non-constant")
                o = ("const", (-o[1]) & E.mask(sz), sz)
            kids.append(o)
        return ("op", "INT_ADD", tuple(kids), sz)
    if len(ops) == 1 and ops[0] in _CMPOPS:
        if sz != 1:
            raise ValueError("comparison width must be 1")
        return ("op", _CMPOPS[ops[0]], (operands[0], operands[1]), 1)
    if len(ops) == 1 and ops[0] in _BINOPS:
        return ("op", _BINOPS[ops[0]], (operands[0], operands[1]), sz)
    mns = {_CHAINOPS.get(op) for op in ops}
    if len(mns) == 1 and None not in mns:
        return ("op", mns.pop(), tuple(operands), sz)
    raise ValueError("mixed operators in %r" % ops)


def _atom(ts):
    t = ts.next()
    if t.startswith("$"):
        return _const_tok(t)
    if t == "(":
        return _chain(ts)
    if t in ("mem", "cur"):
        ts.expect("[")
        addr = _atom(ts)
        ts.expect("]")
        return (t, addr, 1)
    if t in ("zext1", "zext2"):
        ts.expect("(")
        a = _atom(ts)
        ts.expect(")")
        return ("op", "INT_ZEXT", (a,), int(t[4:]))
    if t == "carry":
        ts.expect("(")
        a = _atom(ts)
        ts.expect(",")
        b = _atom(ts)
        ts.expect(")")
        return ("op", "INT_CARRY", (a, b), 1)
    if re.fullmatch(r"u\d+", t):
        return ("uni", int(t[1:]), _suffix(ts))
    if ts.env is not None and t in ts.env:
        return ts.env[t]
    return ("reg", _reg_index(t))


def parse_expr(text, env=None):
    """Parse one expression (``env``: ``let`` name -> node); must consume ``text``."""
    ts = _Toks(text, env)
    n = _atom(ts)
    if not ts.done():
        raise ValueError("trailing tokens in %r" % text)
    return n


def _strip(n):
    """Recorder node -> SIDL node: drop ``cur`` version/fallback (evolved forms
    read the evolving image positionally; the fallback is a record-time device)."""
    k = n[0]
    if k in ("const", "reg", "uni"):
        return n
    if k in ("mem", "cur"):
        assert n[2] == 1, n
        return (k, _strip(n[1]), 1)
    return ("op", n[1], tuple(_strip(c) for c in n[2]), n[3])


# ---- program model -----------------------------------------------------------
class Template:
    """One frame path: ordered ``("ck", site, kind, expr, obs)`` /
    ``("st", addr, expr)`` events plus non-identity end-of-frame register exprs."""

    __slots__ = ("name", "events", "regs")

    def __init__(self, name, events, regs):
        self.name = name
        self.events = list(events)
        self.regs = dict(regs)


class _EntryBuf:
    def __init__(self, cells):
        self.cells = cells

    def __getitem__(self, a):
        v = self.cells.get(a)
        if v is None:
            raise _Missing(a)
        return v


class _ImageBuf:
    def __init__(self, entry, overlay):
        self.entry = entry
        self.overlay = overlay

    def __getitem__(self, a):
        v = self.overlay.get(a)
        return self.entry[a] if v is None else v


class Program:
    """A playroutine as guarded frame templates over a sparse cell image."""

    def __init__(self, play, outputs, regs0, cells, init_writes, templates, frames, uni=None):
        self.play = play
        self.outputs = frozenset(outputs)
        self.regs0 = list(regs0)
        self.cells = dict(cells)
        self.init_writes = list(init_writes)
        self.templates = list(templates)
        self.frames = frames
        self.uni = uni

    def _try(self, tpl, cells, regs, uni):
        entry = _EntryBuf(cells)
        overlay = {}
        img = _ImageBuf(entry, overlay)
        writes = []
        for ev in tpl.events:
            if ev[0] == "st":
                _, addr, ex = ev
                v = E.evaluate(ex, entry, regs, img, uni) & 0xFF
                overlay[addr] = v
                if addr in self.outputs:
                    writes.append((addr, v))
            else:
                if E.evaluate(ev[3], entry, regs, img, uni) != ev[4]:
                    return None
        nregs = list(regs)
        for i, ex in tpl.regs.items():
            nregs[i] = E.evaluate(ex, entry, regs, img, uni) & 0xFF
        return overlay, writes, nregs

    def run(self, frames=None):
        """Interpret the program: ``(init_writes, [per-frame write lists])``.

        Each frame, the first template whose checks all hold (evaluated in
        machine order against the current state) fires; its stores advance the
        state, and stores to ``outputs`` are emitted in order.
        """
        if frames is None:
            frames = self.frames
        cells = dict(self.cells)
        regs = list(self.regs0)
        frame_writes = []
        for f in range(frames):
            uni = self.uni[f] if self.uni and f < len(self.uni) else {}
            fired = None
            misses = []
            for tpl in self.templates:
                try:
                    fired = self._try(tpl, cells, regs, uni)
                except KeyError as exc:
                    misses.append("%s missing %s" % (tpl.name, exc))
                    continue
                if fired is not None:
                    break
            if fired is None:
                why = " (%s)" % "; ".join(misses) if misses else ""
                raise DispatchError("frame %d: no template matches the current state%s" % (f, why))
            overlay, writes, regs = fired
            cells.update(overlay)
            frame_writes.append(writes)
        return list(self.init_writes), frame_writes


# ---- build: record a playroutine and extract its template vocabulary ---------
class _TraceVM(PcodeVM):
    """Concrete VM logging LOAD addresses and ordered writes to ``outs``."""

    def __init__(self, mem, outs):
        super().__init__(mem)
        self.outs = outs
        self.reads = set()
        self.writes = []

    def _rd(self, addr, sz):
        for i in range(sz):
            self.reads.add((addr + i) & 0xFFFF)
        return super()._rd(addr, sz)

    def _wr(self, addr, val, sz):
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if a in self.outs:
                self.writes.append((a, (val >> (8 * i)) & 0xFF))
        super()._wr(addr, val, sz)


def _scan(n, addrs, flags):
    k = n[0]
    if k == "uni":
        flags.add("uni")
    elif k in ("mem", "cur"):
        if n[1][0] == "const":
            addrs.add(n[1][1])
        else:
            _scan(n[1], addrs, flags)
    elif k == "op":
        for c in n[2]:
            _scan(c, addrs, flags)


def _regs_used(n, out):
    k = n[0]
    if k == "reg":
        out.add(n[1])
    elif k in ("mem", "cur"):
        _regs_used(n[1], out)
    elif k == "op":
        for c in n[2]:
            _regs_used(c, out)


def _templates(rec, frames):
    raw = []
    for i in range(frames):
        events = []
        for e in rec.events[i]:
            if e[0] == "st":
                assert e[3] == 1, e
                events.append(("st", e[1], _strip(e[2])))
            else:
                ex = _strip(e[3])
                if ex[0] == "const":  # tautology: no dispatch information
                    assert ex[1] == e[4], e
                    continue
                events.append(("ck", e[1], e[2], ex, e[4]))
        regs = {j: _strip(x) for j, x in enumerate(rec.regs[i]) if _strip(x) != ("reg", j)}
        raw.append((events, regs))
    # a register template is live only if some expression consumes it at frame entry
    used = set()
    for events, _regs in raw:
        for ev in events:
            _regs_used(ev[3] if ev[0] == "ck" else ev[2], used)
    changed = True
    while changed:
        changed = False
        for _events, regs in raw:
            for j, ex in regs.items():
                if j in used:
                    before = len(used)
                    _regs_used(ex, used)
                    changed = changed or len(used) != before
    seen = {}
    tpls = []
    for events, regs in raw:
        live = {j: ex for j, ex in regs.items() if j in used}
        key = (tuple(events), tuple(sorted(live.items())))
        if key not in seen:
            seen[key] = len(tpls)
            tpls.append(Template("T%d" % len(tpls), events, live))
    return tpls


def build(mem, play, frames, init=None, outputs=SID_OUTPUTS):
    """Record ``frames`` play calls (after an optional concrete ``init`` call)
    and lift them into a :class:`Program`."""
    outputs = frozenset(outputs)
    tvm = _TraceVM(bytes(mem), outputs)
    cache = {}
    if init is not None:
        run_sub(tvm, init, cache, lift)
    init_writes = list(tvm.writes)
    post = bytes(tvm.mem)
    rec = record(tvm, run_sub, play, outputs, frames)
    tvm.reads = set()
    for _ in range(frames):
        run_sub(tvm, play, cache, lift)

    tpls = _templates(rec, frames)
    addrs = set(tvm.reads)
    flags = set()
    for tpl in tpls:
        for ev in tpl.events:
            _scan(ev[3] if ev[0] == "ck" else ev[2], addrs, flags)
        for ex in tpl.regs.values():
            _scan(ex, addrs, flags)
    addrs |= outputs
    cells = {a: post[a] for a in sorted(addrs)}
    uni = [dict(rec.uni[i]) for i in range(frames)] if "uni" in flags else None
    return Program(play, outputs, list(rec.entry[0][1]), cells, init_writes, tpls, frames, uni)


def reference_log(mem, play, frames, init=None, outputs=SID_OUTPUTS):
    """Concrete-VM oracle in ``Program.run``'s shape: ``(init_writes, frame_writes)``."""
    tvm = _TraceVM(bytes(mem), frozenset(outputs))
    cache = {}
    if init is not None:
        run_sub(tvm, init, cache, lift)
    init_writes = list(tvm.writes)
    frame_writes = []
    for _ in range(frames):
        tvm.writes = []
        run_sub(tvm, play, cache, lift)
        frame_writes.append(list(tvm.writes))
    return init_writes, frame_writes


# ---- canonical text ----------------------------------------------------------
def _ranges(addrs):
    runs = []
    for a in sorted(addrs):
        if runs and a == runs[-1][1] + 1:
            runs[-1][1] = a
        else:
            runs.append([a, a])
    return ",".join("$%04X" % lo if lo == hi else "$%04X..$%04X" % (lo, hi) for lo, hi in runs)


def _parse_ranges(text):
    out = set()
    for part in text.split(","):
        if ".." in part:
            lo, hi = part.split("..")
            out.update(range(int(lo.lstrip("$"), 16), int(hi.lstrip("$"), 16) + 1))
        else:
            out.add(int(part.lstrip("$"), 16))
    return out


def _event_line(ev, env=None):
    if ev[0] == "st":
        return "  st $%04X <- %s" % (ev[1], fmt_expr(ev[2], env))
    _, site, kind, ex, obs = ev
    return "  ck %s @$%04X %s = %s" % (kind, site, fmt_expr(ex, env), _hex(obs, E.width(ex)))


_LET_MIN = 16


def _bindings(exprs):
    """Deterministic CSE: repeated subexpressions worth a ``let``, innermost
    (shortest) first so definitions precede uses."""
    cnt = {}
    order = {}

    def walk(n):
        k = n[0]
        if k not in ("op", "mem", "cur"):
            return
        cnt[n] = cnt.get(n, 0) + 1
        if n not in order:
            order[n] = len(order)
        for c in n[2] if k == "op" else (n[1],):
            walk(c)

    for ex in exprs:
        walk(ex)
    cands = [n for n, c in cnt.items() if c >= 2 and len(fmt_expr(n)) >= _LET_MIN]
    cands.sort(key=lambda n: (len(fmt_expr(n)), order[n]))
    return cands


def _segments(tpl):
    """Split a template's events into runs each ending at a check (dispatch
    points), the unit of cross-template sharing."""
    segs = []
    cur = []
    for ev in tpl.events:
        cur.append(ev)
        if ev[0] == "ck":
            segs.append(tuple(cur))
            cur = []
    if cur:
        segs.append(tuple(cur))
    return segs


def dumps(prog):
    """Canonical SIDL text for ``prog`` (``loads`` is its exact inverse)."""
    out = ["sidl 0", "play $%04X" % prog.play, "frames %d" % prog.frames]
    out.append("outputs " + _ranges(prog.outputs))
    regline = []
    for i in range(16):
        if i in _REG_NAMES or prog.regs0[i]:
            regline.append("%s=$%02X" % (_reg_name(i), prog.regs0[i]))
    out.append("regs " + " ".join(regline))
    if prog.init_writes:
        out.append("init {")
        out.extend("  st $%04X <- %s" % (a, _hex(v, 1)) for a, v in prog.init_writes)
        out.append("}")
    out.append("mem {")
    row = []
    for a in sorted(prog.cells):
        if row and (a != row[0] + len(row) - 1 or len(row) - 1 >= 16):
            out.append("  $%04X: %s" % (row[0], " ".join(row[1:])))
            row = []
        if not row:
            row = [a]
        row.append("%02X" % prog.cells[a])
    if row:
        out.append("  $%04X: %s" % (row[0], " ".join(row[1:])))
    out.append("}")
    seg_names = {}
    seg_order = []
    tpl_segs = []
    for tpl in prog.templates:
        names = []
        for s in _segments(tpl):
            if s not in seg_names:
                seg_names[s] = "S%d" % len(seg_names)
                seg_order.append(s)
            names.append(seg_names[s])
        tpl_segs.append(names)

    def all_exprs():
        for s in seg_order:
            for ev in s:
                yield ev[3] if ev[0] == "ck" else ev[2]
        for tpl in prog.templates:
            for _i, ex in sorted(tpl.regs.items()):
                yield ex

    env = {}
    for n in _bindings(all_exprs()):
        name = "t%d" % len(env)
        out.append("let %s = %s" % (name, fmt_expr(n, env)))
        env[n] = name
    for s in seg_order:
        out.append("seg %s {" % seg_names[s])
        out.extend(_event_line(ev, env) for ev in s)
        out.append("}")
    for tpl, names in zip(prog.templates, tpl_segs):
        out.append("template %s = %s" % (tpl.name, " ".join(names)))
        out.extend(
            "  reg %s <- %s" % (_reg_name(i), fmt_expr(ex, env))
            for i, ex in sorted(tpl.regs.items())
        )
    if prog.uni is not None:
        out.append("uni {")
        for f, row_vals in enumerate(prog.uni):
            cols = " ".join("%d=%s" % (n, _hex(v, 1)) for n, v in sorted(row_vals.items()))
            out.append(("  %d: %s" % (f, cols)).rstrip())
        out.append("}")
    return "\n".join(out) + "\n"


def _parse_event(line, env=None):
    word, rest = line.split(None, 1)
    if word == "st":
        addr, ex = rest.split("<-", 1)
        return ("st", int(addr.strip().lstrip("$"), 16), parse_expr(ex, env))
    if word == "reg":
        name, ex = rest.split("<-", 1)
        return ("reg", _reg_index(name.strip()), parse_expr(ex, env))
    if word != "ck":
        raise ValueError("bad template line %r" % line)
    kind, rest = rest.split(None, 1)
    if kind not in _KINDS:
        raise ValueError("bad check kind %r" % kind)
    site, rest = rest.split(None, 1)
    ex, obs = rest.rsplit("=", 1)
    if ex.rstrip()[-1:] in ("=", "!", "<"):
        raise ValueError("malformed check %r" % line)
    site_addr = int(site.lstrip("@").lstrip("$"), 16)
    return ("ck", kind, site_addr, parse_expr(ex, env), int(obs.strip().lstrip("$"), 16))


def loads(text):
    """Parse canonical SIDL text back into a :class:`Program`."""
    lines = []
    for raw in text.splitlines():
        s = raw.split(";", 1)[0].strip()
        if s:
            lines.append(s)
    if not lines or lines.pop(0).split() != ["sidl", "0"]:
        raise ValueError("not a sidl 0 document")
    play = frames = None
    outputs = set()
    regs0 = [0] * 16
    init_writes = []
    cells = {}
    templates = []
    uni = None
    env = {}
    segs = {}

    def block(start):
        j = start
        body = []
        while lines[j] != "}":
            body.append(lines[j])
            j += 1
        return body, j + 1

    i = 0
    while i < len(lines):
        line = lines[i]
        word = line.split()[0]
        if word == "play":
            play = int(line.split()[1].lstrip("$"), 16)
            i += 1
        elif word == "frames":
            frames = int(line.split()[1])
            i += 1
        elif word == "outputs":
            outputs = _parse_ranges(line.split(None, 1)[1])
            i += 1
        elif word == "regs":
            for item in line.split()[1:]:
                name, v = item.split("=")
                regs0[_reg_index(name)] = int(v.lstrip("$"), 16)
            i += 1
        elif word == "init":
            body, i = block(i + 1)
            for b in body:
                ev = _parse_event(b)
                init_writes.append((ev[1], ev[2][1]))
        elif word == "mem":
            body, i = block(i + 1)
            for b in body:
                addr, bytestr = b.split(":", 1)
                a = int(addr.strip().lstrip("$"), 16)
                for k, tok in enumerate(bytestr.split()):
                    cells[a + k] = int(tok, 16)
        elif word == "let":
            lname, ex = line[4:].split("=", 1)
            env[lname.strip()] = parse_expr(ex, env)
            i += 1
        elif word == "seg":
            name = line.split()[1]
            body, i = block(i + 1)
            events = []
            for b in body:
                ev = _parse_event(b, env)
                if ev[0] == "st":
                    events.append(ev)
                else:
                    events.append(("ck", ev[2], ev[1], ev[3], ev[4]))
            segs[name] = events
        elif word == "template":
            name, refs = line.split("=", 1)
            name = name.split()[1]
            events = []
            for ref in refs.split():
                events.extend(segs[ref])
            templates.append(Template(name, events, {}))
            i += 1
        elif word == "reg":
            ev = _parse_event(line, env)
            templates[-1].regs[ev[1]] = ev[2]
            i += 1
        elif word == "uni":
            body, i = block(i + 1)
            uni = [{} for _ in body]
            for b in body:
                fstr, rest = b.split(":", 1)
                row = uni[int(fstr)]
                for item in rest.split():
                    n, v = item.split("=")
                    row[int(n)] = int(v.lstrip("$"), 16)
        else:
            raise ValueError("unknown section %r" % line)
    if play is None or frames is None:
        raise ValueError("missing play/frames header")
    return Program(play, outputs, regs0, cells, init_writes, templates, frames, uni)
