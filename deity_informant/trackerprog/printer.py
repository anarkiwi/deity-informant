"""The flattened form of a trackerprog: one fact per line, no JSON.

Generic over the object -- it walks what is there and names nothing of any
tune or family.  Expressions print in the vocabulary of
prototype-trackerprog.md section 5 (``repeat``, ``tablestep``, ``field``,
``fold``, ``bit``, ``carry``), guards as comparisons, and every table as rows
with a header.  :func:`numbers` measures the print the way architecture
section 11 requires of a presentation.
"""

from __future__ import annotations

import lzma

OPS = {"and": "&", "add": "+", "sub": "-", "or": "|"}


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
    if k == "gen":
        return a
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
    if k == "tablestep":
        return "step[%s] >> %s" % (expr(a[0], notes), expr(a[1], notes))
    if k == "repeat":
        return "repeat(%s, %s)" % (expr(a[0], notes), expr(a[1], notes))
    if k == "stream":
        return "%s[%s]" % (a[0], expr(a[1], notes))
    if k == "notefreq":
        return "freq[%s]" % expr(a, notes)
    if k == "pitchrow":
        return "%s[%s]" % (expr(a[0], notes), expr(a[1], notes))
    if k == "row":
        return "->%d" % (notes[a] if notes else a)
    if k == "note":
        return "note.%s" % (a if isinstance(a, str) else expr(a, notes))
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


def _regs(ws):
    return " ".join("%s=%s" % (hexv(r), hexv(v)) for r, v in ws)


def render(obj):  # noqa: C901 - one branch per object section, each linear
    """The whole object as text."""
    m, g, out = obj["meta"], obj["globals"], []
    notes = obj["pitch"]["notes"]
    add = out.append

    add("# trackerprog: %s song %d" % (m["tune"], m["song"] + 1))
    add("")
    add("## meta")
    add("")
    add("voices     %d, order %s" % (m["voices"], " ".join(str(v) for v in m["voice_order"])))
    add("commit     %s" % ", ".join(m["commit_order"]))
    add(
        "tick       %d cycles; tempo divider %d phase %d"
        % (m["cycles_per_tick"], m["tempo"]["rate"], m["tempo"]["phase"])
    )
    add(
        "sequencer  the row consumes the voice's tick"
        if m["row_consumes_tick"]
        else "sequencer  the row shares the voice's tick"
    )
    add("note row   %s" % m["note_row"])
    add("player     %s" % m["player"])
    add("mode_vol   %s" % hexv(g["mode_vol"]))
    for name, d in g.get("flags", {}).items():
        add(
            "flag %-5s = %s where no producer leaves it%s"
            % (name, expr(d["default"], notes), "" if "proof" not in d else " (%s)" % d["proof"])
        )
    add("init       %s" % _regs(g["init_writes"]))
    add("stop       %s" % _regs(g["stop_writes"]))

    add("")
    add("## pitch -- %d notes; a note number and its frequency, and nothing else" % len(notes))
    add("")
    for i in range(0, len(notes), 8):
        add(
            "    "
            + "  ".join(
                "%3d %s" % (n, hexv(obj["pitch"]["freq"][i + j], 4))
                for j, n in enumerate(notes[i : i + 8])
            )
        )

    if obj.get("generators"):
        add("")
        add("## generators -- private state, fed by published events")
        add("")
        for k, gen in obj["generators"].items():
            init = " ".join("%s=%s" % (a, hexv(b)) for a, b in gen["state"].items()) or "stateless"
            add("%s = %s   [%s]" % (k, expr(gen["value"], notes), init))
            for s in gen["on"]:
                how = ("%s := %s" % (a, expr(b, notes)) for a, b in s.get("set", {}).items())
                how = list(how) + [
                    "%s += %s" % (a, expr(b, notes)) for a, b in s.get("add", {}).items()
                ]
                acc = "" if "acc" not in s else " of %s" % s["acc"]
                add("    on %s(voice %d)%s: %s" % (s["event"], s["voice"], acc, "; ".join(how)))

    add("")
    add("## streams")
    add("")
    for k, st in obj["streams"].items():
        add("%s -- %s" % (k, st["term"]))
        for i, row in enumerate(st["rows"]):
            if not isinstance(row, dict):
                add("    row %d: %s" % (i, expr(row, notes)))
                continue
            add(
                "    row %d: %s"
                % (i, " ; ".join("%s := %s" % (t, expr(v, notes)) for t, v in row["sets"]))
            )

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
        arms = " ".join(_arm(a) for a in ins["accs"])
        add(
            "%4s  %s  %s   %s  %s  %s"
            % (
                k,
                hexv(ins["adsr"][0]),
                hexv(ins["adsr"][1]),
                hexv(ins["wave"]),
                hexv(ins["pw"][0] | ins["pw"][1] << 8, 4),
                arms,
            )
        )
        if "seed" in ins:
            add(
                "      seed  no note: number %d, %s"
                % (
                    ins["seed"]["number"],
                    ", ".join(
                        "%s %s" % (f, expr(v, notes))
                        for f, v in ins["seed"].items()
                        if f != "number"
                    ),
                )
            )

    add("")
    add("## score")
    add("")
    for v, o in enumerate(obj["score"]["orders"]):
        add("order %d -- %d steps, %s" % (v, len(o["play"]), o["end"]))
        for i in range(0, len(o["play"]), 24):
            add("    " + " ".join("%3d" % x for x in o["play"][i : i + 24]))
    for k, pat in obj["score"]["patterns"].items():
        add("")
        add("pattern %s -- %d events" % (k, len(pat["events"])))
        if "cursor" in pat:
            add("    cursor  " + " ".join(str(x) for x in pat["cursor"]))
        add("     dur  tie  gate   ins  note  arm")
        for e in pat["events"]:
            add(
                "    %4d  %3s  %-5s %4s  %5s  %s"
                % (
                    e["dur"],
                    "tie" if e["tie"] else ".",
                    e["gate"],
                    "." if e["ins"] is None else e["ins"],
                    (
                        "."
                        if e["note"] is None
                        else "seed" if e["note"] == "seed" else notes[e["note"]]
                    ),
                    "." if e["arm"] is None else _arm(e["arm"]),
                )
            )

    add("")
    add("## initial state")
    add("")
    for k, v in obj["state0"].items():
        if isinstance(v, dict):
            for a, b in v.items():
                add("%-10s %s %s" % (k, a, " ".join(hexv(x) for x in b)))
        else:
            add("%-10s %s" % (k, " ".join(hexv(x) for x in v)))
    return "\n".join(out) + "\n"


def _arm(a):
    over = " ".join("%s %s" % (k, expr(v)) for k, v in a.items() if k != "acc")
    return a["acc"] + ("(%s)" % over if over else "")


def _table(name, rows, notes):
    """An accumulator's own table over the tuning's rows, wrapped."""
    live = [(notes[i], e) for i, e in enumerate(rows) if e is not None]
    out = ["      table   %s, by note (a note absent here is never modulated this way)" % name]
    for i in range(0, len(live), 6):
        out.append(
            "              " + "  ".join("%d:%s" % (n, expr(e, notes)) for n, e in live[i : i + 6])
        )
    return out


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
    lines.append("      policy  %s" % (expr(pol, notes) if isinstance(pol, dict) else pol))
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
        iv = (
            ""
            if "interval" not in b
            else "[%s, %s] " % (hexv(b["interval"][0], 4), hexv(b["interval"][1], 4))
        )
        lines.append("      bound   %s%s -- %s" % (iv, b["from"], b.get("witness", "")))
    for field in ("interval", "octave"):
        if field in a:
            lines += _table(field, a[field], notes)
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
