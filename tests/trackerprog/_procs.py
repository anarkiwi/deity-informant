"""The hand-built procedures the binding's own helpers are read against.

A join two paths reach that no fold makes one, a jump table, a loop that halves a
word once a turn, and a block the program cannot leave.
"""

from _bound import C, GLOB, SWEEP, V, ram, store
from deity_informant.tuneprog.ir import (
    Bin,
    Block,
    Goto,
    If,
    Let,
    Proc,
    Return,
    Switch,
    Trap,
)


def diamond():
    """A join two paths reach that no fold makes one: the object states it as a cell."""
    return Proc(
        "tick",
        entry="a",
        blocks={
            "a": Block("a", [Let("y", C(1))], If(Bin("!=", V("y"), C(0)), "b", "c"), src=0x2000),
            "b": Block(
                "b",
                [Let("z", C(1)), Let("w", C(3)), store(GLOB, 11, C(1), src=0x2004, size=1)],
                Goto("e"),
                src=0x2004,
            ),
            "c": Block("c", [Let("w", C(4))], If(Bin("==", V("y"), C(1)), "d", "e"), src=0x2008),
            "d": Block(
                "d",
                [Let("z", C(2)), store(GLOB, 11, C(2), src=0x200C, size=1)],
                Goto("g"),
                src=0x200C,
            ),
            "g": Block("g", [store(GLOB, 11, C(4), src=0x2018, size=1)], Goto("e"), src=0x2018),
            "e": Block(
                "e",
                [store(GLOB, 11, Bin("+", V("z"), V("w")), src=0x2010, size=1)],
                Goto("f"),
                src=0x2010,
            ),
            "f": Block("f", [], Return(vals=[]), src=0x2014),
        },
    )


def dispatch():
    """A jump table whose edges decide a term, and one label two cases reach."""
    return Proc(
        "tick",
        entry="a",
        blocks={
            "a": Block(
                "a",
                [Let("y", C(1))],
                Switch(V("y"), ((0, "b"), (1, "c"), (2, "d"), (3, "d"))),
                src=0x3000,
            ),
            "b": Block("b", [store(GLOB, 11, C(1), src=0x3004, size=1)], Goto("e"), src=0x3004),
            "c": Block("c", [store(GLOB, 11, C(2), src=0x3008, size=1)], Goto("e"), src=0x3008),
            "d": Block("d", [store(GLOB, 11, C(3), src=0x300C, size=1)], Goto("e"), src=0x300C),
            "e": Block("e", [], Return(vals=[]), src=0x3010),
        },
    )


def halver(inhead=True):
    """A loop that halves the word a per-voice pair holds, once a turn."""
    half = store(SWEEP, 10, Bin(">>", ram(SWEEP, 10), C(1)), src=0x4004)
    head = Block("h", [Let("k2", Bin("-", V("k"), C(1)))], If(V("k2"), "s", "z"), src=0x4004)
    body = {"s": Block("s", [half], Goto("h"), src=0x4008)}
    if inhead:
        head = Block("h", [half, Let("k2", Bin("-", V("k"), C(1)))], If(V("k2"), "h", "z"))
        body = {}
    return Proc(
        "tick",
        entry="a",
        blocks={
            "a": Block("a", [Let("k", ram(GLOB, 11, size=1))], Goto("h"), src=0x4000),
            "h": head,
            "z": Block("z", [], Return(vals=[]), src=0x4010),
            **body,
        },
    )


def trapping():
    """A proc one of whose blocks the program cannot leave: no block of a phase."""
    return Proc(
        "tick",
        entry="a",
        blocks={
            "a": Block("a", [], If(Bin("!=", V("y"), C(0)), "b", "t"), src=0x5000),
            "b": Block("b", [], Return(vals=[]), src=0x5004),
            "t": Block("t", [], Trap("unverified"), src=0x5008),
        },
    )


class Holder:
    """What the segment readers ask of a binder: the reader, and the names it splits on."""

    def __init__(self, low, amb=None):
        self.low, self.amb = low, amb or {}
