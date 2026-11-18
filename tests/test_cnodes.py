"""Tests for deity_informant.cgen.cnodes and units: provenance read back in text order and as
``#line`` directives, structural keys, renaming, and the ``.c`` split by reachability."""

from pycparser import c_ast

from deity_informant.cgen import cnodes as cn, units

RECS = [("pc",), ("check",), ("tag", "frame"), ("tag", "frame")]


def _line(rec):
    return rec + 1, RECS[rec][-1]


def _guarded():
    check = cn.marked(cn.binop(">", cn.ident("a"), cn.ident("L")), 1)
    cond = cn.marked(cn.binop("||", cn.ident("x"), check), 0)
    st = cn.when(cond, [cn.call("trap", cn.const(1))])
    return cn.define(cn.function("f", "void", [("a", "uint8_t")]), [st], (2, 3))


def test_records_in_text_order_and_directives_around_wrapped_ones():
    """A record held by a statement's leading expression starts its line; a wrapped one
    (a check) is entered mid-line and the enclosing one resumes after it."""
    text, order = cn.render([_guarded()], RECS)
    assert order == [2, 0, 1, 3] and "#line" not in text
    lines, again = cn.render([_guarded()], RECS, _line)
    assert again == order
    assert lines.startswith('#line 3 "frame"\nvoid f(uint8_t a)\n#line 3 "frame"\n{\n')
    assert '#line 1 "pc"\n  if (x ||\n' in lines
    assert '#line 2 "check"\n      a > L\n#line 1 "pc"\n' in lines
    assert lines.rstrip().endswith('#line 4 "frame"\n}')


def test_a_record_on_a_marked_node_wraps_it():
    """Marking a node already marked keeps both: the outer first, then the inner."""
    inner = cn.marked(cn.ident("v"), 1)
    outer = cn.marked(inner, 0)
    assert (outer.coord.rec, outer.coord.inner) == (0, 1) and inner.coord.inner is None
    assert cn.render([cn.decl("w", "uint8_t", outer)], RECS)[1] == [0, 1]


def test_verbatim_parts_print_their_record():
    """Verbatim C starts with its record's directive when lines are asked for."""
    text, order = cn.render([(2, "int x;")], RECS, _line)
    assert order == [2] and text == '#line 3 "frame"\nint x;\n'


def test_keys_ignore_provenance_and_rename():
    """Keys equal trees whatever their provenance; renaming maps names, each node once."""
    a, b = cn.marked(cn.call("f3", cn.const(1)), 0), cn.call("f3", cn.const(1))
    assert cn.key(a) == cn.key(b) != cn.key(cn.call("f4", cn.const(1)))
    assert cn.key(a, {"f3": "f4"}) == cn.key(cn.call("f4", cn.const(1)))
    head = cn.function("f3", "void", [])
    fd = cn.define(head, [cn.call("f4")])
    cn.rename([head, fd], {"f3": "f4", "f4": "f5"})
    assert cn.render([fd], RECS)[0] == "void f4(void) { f5(); }\n"


def test_types_and_literals():
    """Type spellings become type trees; literals are decimal below ten, else hex."""
    decl = cn.decl("p", "const uint8_t *const", cn.inits("A", "B"), ("static",), dim=True)
    assert cn.render([decl], RECS)[0] == "static const uint8_t *const p[] = {A, B};\n"
    assert [cn.spell(v) for v in (9, 10, 0x7F00)] == ["9", "0xA", "0x7F00"]
    assert cn.cast_type(cn.cast("int8_t", cn.ident("v"))) == "int8_t"
    assert cn.cast_type(cn.cast("uint8_t *", cn.ident("v"))) is None


def _fn(name, calls, storage=("static",)):
    head = cn.function(name, "void", [], storage)
    return units.part(cn.define(head, [cn.call(c) for c in calls]))


def test_split_by_reachability():
    """Each entry's own functions stay in its unit, ``static``; those both reach go to the
    shared unit, external, their prototypes and the state's externs to the header."""
    state = units.Part(cn.decl("S_1", "uint8_t", cn.ident("S_1_INIT")), units.STATE)
    parts = [
        units.Part((2, "#include <stdint.h>"), units.EVERY),
        state,
        _fn("g", []),
        _fn("i1", ["g"]),
        _fn("cgen_init", ["i1"], ()),
        _fn("cgen_play", ["g"], ()),
    ]
    got, head = units.split(parts, ["cgen_init", "cgen_play"], lambda e: str(e))
    text = {u: cn.render(xs, RECS)[0] for u, xs in got.items()}
    assert set(text) == {"cgen_init", "cgen_play", "None", "state"}
    assert "static void i1(void)" in text["cgen_init"] and "g()" in text["cgen_play"]
    assert text["None"].endswith("void g(void) {}\n") and "static" not in text["None"]
    assert text["state"] == "#include <stdint.h>\nuint8_t S_1 = S_1_INIT;\n"
    assert [cn.Printer(RECS).visit(d) for d in head] == ["extern uint8_t S_1", "void g(void)"]
    assert cn.render(units.single(parts), RECS)[0].count("#include") == 1
