"""The flattened form of a trackerprog: one fact per line, no JSON.

Generic over the object -- it walks what is there and names nothing of any
tune or family.  Expressions print in the vocabulary of
prototype-trackerprog.md section 5 (``repeat``, ``interval``, ``field``,
``fold``, ``bit``, ``carry``), guards as comparisons, and every table as rows
with a header.  :func:`numbers` measures the print the way architecture
section 11 requires of a presentation.
"""

from __future__ import annotations

import lzma

OPS = {"and": "&", "add": "+", "sub": "-", "or": "|", "xor": "^"}


def hexv(v, width=2):
    return "$%0*X" % (width, v)


def expr(e, notes=None):
    """One section 5 expression, flattened."""
    if e is None:
        return "--"
    if isinstance(e, bool):
        return str(e).lower()
    if isinstance(e, int):
        return str(e) if -16 < e < 16 else hexv(e, 4 if e > 0xFF else 2)
    if isinstance(e, str):
        return "<%s>" % e  # an override the instrument's arm binds
    if isinstance(e, list):
        return " ".join(expr(x, notes) for x in e)
    k, a = next(iter(e.items()))
    if k == "const":
        return expr(a, notes)
    if k == "cell":
        return a
    if k == "own":
        return "own." + a
    if k == "flag":
        return "flag " + a
    if k == "payload":
        return a
    if k == "ins":
        return "ins." + a
    if k == "insrec":
        return "ins[%s].%s" % (a[0], a[1])
    if k == "tuned":
        return "pitch[%s]" % expr(a, notes)
    if k == "sid_base":
        return "sid_base(%s)" % (a if isinstance(a, (int, str)) else expr(a, notes))
    if k == "u16":
        return "u16(%s, %s)" % (expr(a[0], notes), expr(a[1], notes))
    if k in OPS:
        return "%s %s %s" % (_sub(a[0], notes), OPS[k], _sub(a[1], notes))
    if k == "field":
        return "%s & %s" % (_sub(a[0], notes), hexv(a[1]))
    if k == "bit":
        return "bit(%s, %d)" % (expr(a[0], notes), a[1])
    if k == "fold":
        return "fold(%s, %d)" % (expr(a[0], notes), a[1])
    if k == "repeat":
        return "repeat(%s, %s)" % (expr(a[0], notes), expr(a[1], notes))
    if k == "stream":
        return "%s[%s]" % (a[0], expr(a[1], notes))
    if k == "tabcell":
        return "%s[%s].%s" % (a[0], expr(a[1], notes), a[2])
    if k == "global":
        return "#" + a
    if k == "trap":
        return "trap: " + a
    if k == "notefreq":
        return "pitch"
    if k == "interval":
        return "interval" if a is None else "interval(%s)" % expr(a, notes)
    if k == "transpose":
        return "transpose(%s)" % expr(a, notes)
    if k == "shr":
        return "%s >> %s" % (_sub(a[0], notes), expr(a[1], notes))
    if k == "reload":
        return "reload " + expr(a, notes)
    if k == "mul":
        return "%s * %s" % (expr(a[0], notes), expr(a[1], notes))
    return "%s(%s)" % (k, expr(a, notes))


def _sub(e, notes):
    """A binary operand, parenthesised where it is itself a binary node."""
    t = expr(e, notes)
    inner = isinstance(e, dict) and next(iter(e)) in tuple(OPS) + ("field",)
    return "(%s)" % t if inner else t


def guards(gs, notes=None):
    return " and ".join("%s %s %s" % (expr(a, notes), op, expr(b, notes)) for a, op, b in gs)


def _rowstep(step, notes):
    """One step of the row program (section 3.6), as what it does."""
    if "sets" in step:
        return "sets %s" % _sets(step["sets"], notes)
    if "stream" in step:
        return "stream %s" % step["stream"]
    if "commands" in step:
        return "the row's own commands"
    if "ins" in step:
        return "the instrument the row names"
    return "the sound the row keys"


def _when(step, notes):
    return "" if not step.get("when") else " when %s" % guards(step["when"], notes)


def _flush(rs):
    """How the image's own flush runs: down, up, or the order the tune writes.

    An entry may carry the guard the image writes it under, so a flush is a
    sequence of guarded writes and the plain register is the entry with none.
    """
    if any(not isinstance(r, int) for r in rs):
        return "under its own guards, %d of them" % sum(1 for r in rs if not isinstance(r, int))
    if rs == sorted(rs, reverse=True):
        return "descending"
    return "ascending" if rs == sorted(rs) else "in the image's own order"


def _regs(ws):
    return " ".join("%s=%s" % (hexv(r), hexv(v)) for r, v in ws)


def render(obj):  # noqa: C901 - one branch per object section, each linear
    """The whole object as text."""
    m, g, out = obj["meta"], obj["globals"], []
    notes = None
    add = out.append

    add("# trackerprog: %s song %d" % (m["tune"], m["song"] + 1))
    add("")
    add("## meta")
    add("")
    add("voices     %d, order %s" % (m["voices"], " ".join(str(v) for v in m["voice_order"])))
    add("commit     %s" % ", ".join(m["commit_order"]))
    add("tick       %d cycles; %s" % (m["cycles_per_tick"], _tempo(m["tempo"], notes)))
    add("sequencer  %s" % _consumes(m["row_consumes_tick"], notes))
    if m.get("row_command"):
        add(
            "commands   %s"
            % (
                "a voice holds the last one the score gave it, and re-runs it every row"
                if m["row_command"] == "held"
                else "spent by the row that gives them"
            )
        )
    if "shadow" in m:
        add(
            "shadow     %d registers, flushed %s at the head of every tick"
            % (len(m["shadow"]["registers"]), _flush(m["shadow"]["registers"]))
        )
    for k, label in (("prefetch", "fetched early"), ("pitch_links", "a new pitch resets")):
        if m.get(k):
            add(
                "%-10s %s"
                % (
                    label,
                    " ".join(x if isinstance(x, str) else "%s into %s" % tuple(x) for x in m[k]),
                )
            )
    for k, label in (("stage_sounds", "sounds cell"), ("wide", "wide cells ")):
        if m.get(k):
            v = m[k]
            add("%s %s" % (label, v if isinstance(v, str) else " ".join(str(x) for x in v)))
    for i, step in enumerate(m.get("row", ())):
        add("row %-6d %s%s" % (i, _rowstep(step, notes), _when(step, notes)))
    add(
        "tick       %s"
        % " ; ".join(x if isinstance(x, str) else "stream %s" % x["stream"] for x in m["tick"])
    )
    if m.get("prologue"):
        add("prologue   " + _cmd(m["prologue"], notes))
    add("player     %s" % m["player"])
    if "mode_vol" in g:
        add("mode_vol   %s" % hexv(g["mode_vol"]))
    for c in g.get("commit", ()):
        add(
            "global     %s := %s%s"
            % (
                hexv(c[0]),
                expr(c[1], notes),
                "" if len(c) < 3 else " when %s" % guards(c[2], notes),
            )
        )
    for name, d in g.get("flags", {}).items():
        add(
            "flag %-5s = %s where no producer leaves it%s"
            % (name, expr(d["default"], notes), "" if "proof" not in d else " (%s)" % d["proof"])
        )
    if g.get("init_writes"):
        add("init       %s" % _regs(g["init_writes"]))
    if g.get("stop_writes"):
        add("stop       %s" % _regs(g["stop_writes"]))

    p = obj["pitch"]
    add("")
    add(
        "## pitch -- the tuning: notes %d..%d, and nothing else"
        % (p["base"], p["base"] + len(p["freq"]) - 1)
    )
    add("")
    for i in range(0, len(p["freq"]), 8):
        add(
            "    "
            + "  ".join(
                "%3d %s" % (p["base"] + i + j, hexv(f, 4))
                for j, f in enumerate(p["freq"][i : i + 8])
            )
        )

    add("")
    add("## streams")
    add("")
    for k, st in obj["streams"].items():
        head = "%s%s%s" % (
            k,
            "" if "term" not in st else " -- " + st["term"],
            "" if "rank" not in st else ", rank %d" % st["rank"],
        )
        if st.get("all"):
            head += ", every row every tick"
        if st.get("epoch") == "entry":
            head += ", counted at entry"
        if st.get("rate"):
            head += ", one row every %s + 1 ticks in %s" % (
                expr(st["rate"]["reload"], notes),
                st["rate"]["cell"],
            )
        if st.get("when"):
            head += ", when " + guards(st["when"], notes)
        add(head)
        for i, row in enumerate(st["rows"]):
            add("    row %d: %s" % (i, _row(row, notes)))
        if "beyond" in st:
            for line in _private(
                "beyond", st["beyond"], notes, "past the tuning, by how far past it the step went"
            ):
                add(line)

    add("")
    add("## accumulators -- section 5 records, in rank order")
    add("")
    for k in sorted(obj["accs"], key=lambda x: obj["accs"][x]["rank"]):
        add(_acc(k, obj["accs"][k], notes))

    add("")
    add("## instruments -- %d" % len(obj["instruments"]))
    add("")
    add("  id   ad   sr  wave     pw  accumulators armed at note on")
    for k, ins in obj["instruments"].items():
        ad, sr = ins["adsr"] if "adsr" in ins else (None, None)  # a family may have none
        add(
            "%4s  %s  %s   %s  %s  %s"
            % (
                k,
                "--" if ad is None else hexv(ad),
                "--" if sr is None else hexv(sr),
                hexv(ins.get("wave", 0)),
                "  --" if "pw" not in ins else hexv(ins["pw"][0] | ins["pw"][1] << 8, 4),
                " ".join(_arm(a) for a in ins["accs"]),
            )
        )
        for line in _ins(ins, notes):
            add(line)
    add("")
    add("## score")
    add("")
    for v, o in enumerate(obj["score"]["orders"]):
        end = o["end"] if isinstance(o["end"], str) else "jump %d" % o["end"]["jump"]
        add("order %d -- %d steps, %s" % (v, len(o["play"]), end))
        steps = [x if isinstance(x, int) else x["pattern"] for x in o["play"]]
        for i in range(0, len(steps), 24):
            add("    " + " ".join("%3d" % x for x in steps[i : i + 24]))
        moved = [
            (i, x["transpose"])
            for i, x in enumerate(o["play"])
            if isinstance(x, dict) and x.get("transpose")
        ]
        if moved:
            add("    transpose " + " ".join("%d:%+d" % t for t in moved))
    cmds = obj["score"].get("commands", {})
    if cmds:
        add("")
        add("commands -- %d, named by the rows that hold them" % len(cmds))
        for k, c in cmds.items():
            add("    %-6s %s" % (k, _cmd(c, notes)))
    for k, pat in obj["score"]["patterns"].items():
        add("")
        add("pattern %s -- %d events" % (k, len(pat["events"])))
        add("     dur  snd  tie  gate   ins  note  arm")
        for e in pat["events"]:
            add(
                "    %4d  %3s  %3s  %-5s %4s  %5s  %s"
                % (
                    e["dur"],
                    "*" if e["sounds"] else ".",
                    "tie" if e["tie"] else ".",
                    "." if e["gate"] is None else e["gate"],
                    "." if e["ins"] is None else e["ins"],
                    "." if e["note"] is None else e["note"],
                    "." if e["arm"] is None else _armref(e["arm"]),
                )
            )

    add("")
    add("## initial state")
    add("")
    for k, v in obj["state0"].items():
        for line in _state(k, v):
            add(line)
    return "\n".join(out) + "\n"


def _state(k, v):
    """One initial-state row per named cell, however the object groups them."""
    if v is None:
        return []
    if isinstance(v, str):
        return ["%-10s %s" % (k, v)]
    if not isinstance(v, dict):
        return ["%-10s %s" % (k, " ".join(hexv(x) for x in v))]
    if {"arms", "rows", "all"} & set(v):  # a command the voice starts holding
        return ["%-10s %s" % (k, _cmd(v, None))]
    out = []
    for a, b in v.items():
        if isinstance(b, int):
            out.append("%-10s %-10s %s" % (k, a, hexv(b)))
        elif isinstance(b, dict):
            out.append("%-10s %-10s %s" % (k, a, " ".join("%s %s" % kv for kv in b.items())))
        elif b and isinstance(b[0], dict):
            out.append(
                "%-10s %-10s %s"
                % (k, a, " ".join("row %s hold %s" % (x["row"], x["hold"]) for x in b))
            )
        else:
            out.append("%-10s %-10s %s" % (k, a, " ".join(hexv(x) for x in b)))
    return out


def _tempo(t, notes):
    """The row clock: a divider, a countdown, or a counter and what resets it."""
    if t.get("form") == "counter":
        s = "tempo counter %s, row at %s" % (t["cell"], expr(t.get("boundary", 0), notes))
        if "fetch" in t:
            s += ", fetched at %s" % expr(t["fetch"], notes)
        for r in t.get("reset", ()):
            s += "\n           reset %s where %s" % (
                _sets(r["sets"], notes),
                guards(r["when"], notes),
            )
        return s
    if t.get("form") != "countdown":
        return "tempo divider %d phase %d" % (t["rate"], t["phase"])
    s = "tempo countdown %s, reload %s, row at %d" % (t["cell"], t["reload"], t.get("boundary", 0))
    if "early" in t:
        s += ", fetch %s early" % expr(t["early"], notes)
    if "alternate" in t:
        s += ", alternating %s when %s" % (
            t["alternate"]["stream"],
            guards(t["alternate"]["when"], notes),
        )
    return s


def _consumes(r, notes):
    """Whether the row spends the voice's tick, and the guard where it is one."""
    if r is True:
        return "the row consumes the voice's tick"
    if not r:
        return "the row shares the voice's tick"
    return "the row consumes the voice's tick when %s" % guards(r, notes)


def _target(t):
    return t if isinstance(t, str) else "reg " + hexv(t)


def _sets(rows, notes):
    """Assignments, each with the guards that let it through where it has any."""
    return " ; ".join(
        "%s := %s%s"
        % (_target(r[0]), expr(r[1], notes), "" if len(r) < 3 else " when %s" % guards(r[2], notes))
        for r in rows
    )


def _row(row, notes):
    """One stream row: a jump, a trap, or sets with a hold, an op and a next."""
    if not isinstance(row, dict):
        return expr(row, notes)
    if "jump" in row:
        return "stop" if not row["jump"] else "jump %d" % row["jump"]
    if "trap" in row:
        return "trap: " + row["trap"]
    bits = []
    if row.get("when"):
        bits.append("when " + guards(row["when"], notes))
    if "sets" in row:
        bits.append(_sets(row["sets"], notes))
    for f in ("delta", "depth", "cmp", "zero", "value", "raw", "mask"):
        if f in row:
            bits.append("%s %s" % (f, expr(row[f], notes)))
    if row.get("hold", 1) != 1:
        bits.append("hold %s" % expr(row["hold"], notes))
    for a in row.get("run", ()):
        bits.append("run " + _arm(a))
    if "op" in row:
        bits.append("op " + _op(row["op"], notes))
    if "next" in row:
        bits.append("next %s" % expr(row["next"], notes))
    return " ; ".join(bits) or "--"


def _op(op, notes):
    """A step's own producer: a pitch of the tuning, an accumulator, or a command."""
    if "pitch" in op:
        n = op["pitch"]
        if not isinstance(n, int):
            return "pitch %s" % expr(n, notes)
        return "pitch %s" % ("note %+d" % n if op.get("relative") else "note %d" % n)
    return _armref(op) if "acc" in op or "cmd" in op else _cmd(op, notes)


def _armref(a):
    """What a row's arm column holds: an arm, a command, or the names of them."""
    if isinstance(a, list):
        return " ".join(_armref(x) for x in a)
    if isinstance(a, str):
        return a
    if "cmd" in a:
        return a["cmd"]
    return _arm(a) if "acc" in a else _cmd(a, None)


def _cmd(c, notes):
    """A row command, unpacked: what it arms, what it sets and what it re-points."""
    bits = []
    if "arms" in c:
        bits.append("arm " + " ".join(_arm(a) for a in c["arms"]))
    if c.get("links"):
        bits.append("reset " + " ".join(c["links"]))
    for row in c.get("rows", ()):
        when = "" if not row.get("when") else " when %s" % guards(row["when"], notes)
        if row.get("sets"):
            bits.append(_sets(row["sets"], notes) + when)
        if row.get("point"):
            bits.append(_points(row["point"], notes) + when)
    if c.get("all"):
        bits.append("every voice " + _sets(c["all"], notes))
    if c.get("tie"):
        bits.append("ties")
    return " ; ".join(bits) or "--"


def _points(pts, notes):
    """What a step re-points: the slot, the row, and whether the hold survives."""
    return " ".join(
        "%s := row %s (hold %s)%s"
        % (
            p[0],
            expr(p[1], notes),
            "kept" if len(p) > 2 and p[2] else "reset",
            "" if len(p) < 4 or not p[3] else " when " + guards(p[3], notes),
        )
        for p in pts
    )


def _ins(ins, notes):
    """An instrument's cells, its stream entries and the prelude that precedes it."""
    out = []
    for row in ins.get("on_note", ()):
        when = "" if not row.get("when") else " when %s" % guards(row["when"], notes)
        if row.get("sets"):
            out.append("      %-7s %s%s" % ("on note", _sets(row["sets"], notes), when))
        if row.get("point"):
            out.append("      %-7s %s%s" % ("streams", _points(row["point"], notes), when))
    p = ins.get("prelude")
    if isinstance(p, dict):
        out.append(
            "      %-7s %s%s"
            % ("prelude", p["stream"], "" if "early" not in p else ", %s early" % p["early"])
        )
    if "pitch" in ins:
        for line in _private(
            "pitch", ins["pitch"], notes, "this instrument's sound is no pitch; it is its own"
        ):
            out.append(line[2:])
    return out


def _arm(a):
    """One arm: the accumulator it names, the overrides it binds, its own guards."""
    over = " ".join("%s %s" % (k, expr(v)) for k, v in a.items() if k not in ("acc", "when"))
    if a.get("when"):
        over = (over + " " if over else "") + "when " + guards(a["when"])
    return a["acc"] + ("(%s)" % over if over else "")


def _private(label, rec, notes, head):
    """A modulator's own values and the private state that feeds them."""
    out = ["      %-7s %s" % (label, head)]
    for j, w in enumerate(rec.get("words", [])):
        out.append(
            "          %3d  %s" % (j, "trap: " + w["trap"] if "trap" in w else expr(w, notes))
        )
    for f in ("value", "octave"):
        if f in rec:
            out.append("          %-9s %s" % (f, expr(rec[f], notes)))
    out.append(
        "          state  "
        + (" ".join("%s=%s" % (a, hexv(b)) for a, b in rec["state"].items()) or "stateless")
    )
    for x in rec["on"]:
        how = "; ".join(
            ["%s := %s" % (a, expr(b, notes)) for a, b in x.get("set", {}).items()]
            + ["%s += %s" % (a, expr(b, notes)) for a, b in x.get("add", {}).items()]
        )
        acc = "" if "acc" not in x else " of %s" % x["acc"]
        out.append("          on %s(voice %d)%s: %s" % (x["event"], x["voice"], acc, how))
    return out


def _policy(pol, notes):
    """A policy record: what it clamps to or reloads from, and where its edge is."""
    if "clamp" in pol:
        return "clamp %s%s" % (
            expr(pol["clamp"], notes),
            "" if not pol.get("edge") else " (edge %d)" % pol["edge"],
        )
    return "reload %s%s" % (
        expr(pol["reload"], notes),
        "" if not pol.get("when") else " when " + guards(pol["when"], notes),
    )


def _bound(x):
    """One end of a bound: a number, or the expression the tune reads it from."""
    return hexv(x, 4) if isinstance(x, int) else expr(x)


def _acc(name, a, notes):
    """One accumulator record, flattened."""
    head = "[%d] %-13s %-5s w%-3d %-14s scope %s" % (
        a["rank"],
        name,
        a["target"],
        a["width"],
        a["cell"],
        a["scope"],
    )
    lines = [head]
    pol = a["policy"]
    lines.append("      policy  %s" % (_policy(pol, notes) if isinstance(pol, dict) else pol))
    if "delta" in a:
        d = "      delta   %s" % expr(a["delta"], notes)
        if a.get("delta_when"):
            d += "   when %s" % guards(a["delta_when"], notes)
        lines.append(d)
    if a.get("rate", 1) != 1:
        lines.append("      rate    every %s ticks" % expr(a["rate"], notes))
    if "phase" in a:
        lines.append("      phase   %s" % expr(a["phase"], notes))
    if "flag" in a:
        f = a["flag"]
        lines.append(
            "      flag    %s = %s at entry, %s where the delta is skipped"
            % (f["name"], f["seed"], f["unguarded"])
            if "seed" in f
            else "      flag    %s = %s where the delta is skipped" % (f["name"], f["unguarded"])
        )
    if a.get("when"):
        lines.append("      when    %s" % guards(a["when"], notes))
    if a.get("step_when"):
        lines.append("      steps   when %s" % guards(a["step_when"], notes))
    if a.get("emit"):
        lines.append(
            "      emits   the value the tick came in with"
            if a["emit"] == "entry"
            else "      emits   the value the tick left"
        )
    if "bound" in a:
        b = a["bound"]
        iv = "" if "interval" not in b else "[%s, %s] " % tuple(_bound(x) for x in b["interval"])
        lines.append("      bound   %s%s -- %s" % (iv, b["from"], b.get("witness", "")))
    if "beyond" in a:
        lines += _private(
            "beyond",
            a["beyond"],
            notes,
            "past the tuning, by " + a["beyond"].get("index", "how far past it it went"),
        )
    lines.append("      writes  %s" % " ".join("%s(%s)" % (t, p) for t, p in a["produce"]))
    for key, label in (("false", "else "), ("true", "steps")):
        if key in a.get("gate", {}):
            lines.append(
                "      gate %s  %s"
                % (label, " ; ".join("%s := %s" % (t, expr(v, notes)) for t, v in a["gate"][key]))
            )
    if a.get("trap"):
        lines.append("      trap    %s" % a.get("note", "the arm the horizon never takes"))
    if a.get("armed_by"):
        lines.append("      armed   by the %s" % a["armed_by"])
    return "\n".join(lines)


def numbers(text):
    """The presentation numbers architecture section 11 asks of a print."""
    lines = [x for x in text.split("\n") if x.strip()]
    head = [x for x in lines if x.startswith(("#", "note  ", "  id ", "     dur", "a row with"))]
    return {
        "lines": len(lines),
        "tokens": sum(len(x.split()) for x in lines),
        "statements": len(lines) - len(head),
        "blocks": sum(1 for x in lines if x.startswith("## ")),
        "header_rows": len(head),
        "data_rows": len(lines) - len(head),
        "xz": len(lzma.compress(text.encode(), preset=9 | lzma.PRESET_EXTREME)),
    }
