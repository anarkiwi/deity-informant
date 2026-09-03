"""B7 -- a tick of several procedures, and the register file its writes land in.

The callee inlined where it stands, a run of calls rerolled into the pass it is,
and section 3.1's shadow: the image T0 names, its flush and its own order.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tuneprog"))

from deity_informant.trackerprog import callee, shadow  # noqa: E402
from deity_informant.tuneprog.ir import (  # noqa: E402
    Bin,
    Block,
    Call,
    Const,
    Goto,
    If,
    Let,
    Load,
    Proc,
    Rgn,
    Return,
    Store,
    Tuneprog,
    Var,
)

GHOST, CHIP = 0x1400, 0xD400


def C(v, w=1):
    return Const(v, w)


def V(n, w=1):
    return Var(n, w)


def call(proc, args, rets):
    return Call(proc, tuple(args), tuple(rets))


def callee_proc(name="p", src=0x2000):
    """One procedure of a voice: it reads its argument and leaves a value in A."""
    blocks = {
        "b": Block(
            "b",
            [
                Let(
                    "u",
                    Load("ram", Bin("+", C(GHOST, 2), Var("X", 1), 2), 1, GHOST, GHOST + 21, 1),
                )
            ],
            Return(vals=[Var("u", 1)]),
            src=src,
        )
    }
    return Proc(name, blocks=blocks, entry="b", params=[1], rets=[0])


def prog_of(procs, storage=()):
    meta = {
        "tick_proc": "tick",
        "init_proc": "init",
        "entry": {"kind": "sub"},
        "load": (0x1000, 0x2000),
    }
    return Tuneprog(meta, list(storage), [], dict(procs))


def test_a_tick_with_no_call_of_its_own_is_the_program_it_was():
    p = prog_of({"tick": Proc("tick", blocks={"a": Block("a", [], Return(vals=[]))}, entry="a")})
    got, loops = callee.inline(p, "tick")
    assert got is p and loops == 0


def test_one_call_is_the_callee_s_blocks_where_it_stands():
    tick = Proc(
        "tick",
        blocks={
            "a": Block("a", [call("p", [C(7)], ["r"])], Goto("z")),
            "z": Block("z", [Let("q", V("r"))], Return(vals=[Var("q", 1)])),
        },
        entry="a",
        params=[],
        rets=[0],
    )
    got, loops = callee.inline(prog_of({"tick": tick, "p": callee_proc()}), "tick")
    blocks = got.procs["tick"].blocks
    assert loops == 0 and not any(type(s) is Call for b in blocks.values() for s in b.stmts)
    assert any(b.label.endswith("$b") for b in blocks.values())  # the callee's own block


def test_a_run_of_calls_that_steps_one_constant_is_the_loop_it_is():
    """A pass over the voices the source unrolled is one loop the object states."""
    tick = Proc(
        "tick",
        blocks={
            "a": Block(
                "a",
                [call("p", [C(0)], ["r0"]), call("p", [C(7)], ["r1"]), call("p", [C(14)], ["r2"])],
                Goto("z"),
            ),
            "z": Block("z", [Let("q", V("r2"))], Return(vals=[Var("q", 1)])),
        },
        entry="a",
        params=[],
        rets=[0],
    )
    got, loops = callee.inline(prog_of({"tick": tick, "p": callee_proc()}), "tick")
    assert loops == 1
    blocks = got.procs["tick"].blocks
    # the run is one copy of the callee, closed by the step its own calls took
    assert sum(1 for b in blocks.values() if b.label.endswith("$b")) == 1
    latch = next(b for b in blocks.values() if type(b.term) is If and b.label.endswith("$b"))
    assert type(latch.term.c) is Bin and latch.term.c.b.v == 21  # 3 turns of 7


def test_a_run_whose_argument_does_not_step_is_no_loop():
    p = callee_proc()
    assert callee.shape(p, [call("p", [C(3)], ["a"]), call("p", [C(3)], ["b"])]) is None
    assert callee.shape(p, [call("p", [C(0)], ["a"])]) is None
    got = callee.shape(p, [call("p", [C(0)], ["a"]), call("p", [C(2)], ["b"])])
    assert got is not None and got[0] == (1, 0, 2)


def test_the_names_a_callee_reads_are_what_a_caller_must_state():
    assert callee.livein(callee_proc()) == (1,)


def test_a_call_graph_that_does_not_close_is_refused():
    loop = Proc(
        "p",
        blocks={"b": Block("b", [call("p", [], [])], Return(vals=[]))},
        entry="b",
        params=[],
        rets=[],
    )
    tick = Proc(
        "tick", blocks={"a": Block("a", [call("p", [], [])], Return(vals=[]))}, entry="a", rets=[]
    )
    it = callee.Inliner(prog_of({"tick": tick, "p": loop}), "tick")
    it.n = callee.PASSES  # the guard is the pass count, not the shape
    try:
        it.run()
        raise AssertionError("a recursive call graph must not close")
    except RecursionError:
        pass


# ---- the register file -------------------------------------------------------------
def flusher():
    """A tick whose one act copies a 25-byte image to the chip, descending."""
    load = Load("ram", Bin("+", C(GHOST, 2), Var("i", 1), 2), 1, GHOST, GHOST + 24, 1)
    blocks = {
        "f": Block(
            "f",
            [
                Let("v", load),
                Store("io", Bin("+", C(CHIP, 2), Var("i", 1), 2), V("v"), 1, CHIP, CHIP + 24, 2, 4),
                Let("j", Bin("-", V("i"), C(1))),
            ],
            If(Bin("==", Bin("&", V("j"), C(0x80)), C(0)), "g", "h"),
            src=4,
        ),
        "g": Block("g", [Let("i", V("j"))], Goto("f")),
        "h": Block("h", [], Return(vals=[])),
    }
    blocks["e"] = Block("e", [Let("i", C(24))], Goto("f"))
    return Proc("tick", blocks=blocks, entry="e", params=[], rets=[])


def t0_of():
    img = {"region": 1, "delta": (CHIP - GHOST) & 0xFFFF, "flush_pc": "$0004", "flush_proc": "tick"}
    return {
        "writes": [
            {"direct": True, "kind": "file", "register": None, "site": {"pc": "$0004"}},
            {"register": "ctrl", "image": img, "site": {"pc": "$0010"}},
        ]
    }


class _View:
    def __init__(self, storage):
        self.storage = storage

    def by_id(self):
        return {r.id: r for r in self.storage}


def ghost_region():
    return Rgn(1, "ghost", GHOST, 25, "state", init=bytes(range(25)))


def shadow_prog():
    init = Proc(
        "init", blocks={"a": Block("a", [], Return(vals=[]))}, entry="a", params=[], rets=[]
    )
    prog = prog_of({"tick": flusher(), "init": init}, storage=[ghost_region()])
    return prog, prog.image()


def test_the_shadow_is_t0_s_own_image_its_flush_and_the_order_it_sends():
    prog, m = shadow_prog()
    view = _View([ghost_region()])
    got = shadow.of(t0_of(), prog, view)
    assert got.rid == 1 and got.base == GHOST and got.size == 25
    assert got.registers[0] == "mode_vol" and got.registers[-1] == "v0.freq_lo"
    assert len(got.registers) == 25 and {"f", "g"} <= got.blocks
    assert shadow.seed(m, got) == list(range(25))


def test_a_tune_whose_writes_reach_the_chip_has_no_shadow():
    prog, _m = shadow_prog()
    view = _View([ghost_region()])
    assert (
        shadow.of({"writes": [{"register": "ctrl", "site": {"pc": "$0010"}}]}, prog, view) is None
    )
    bad = t0_of()
    bad["writes"][1]["image"] = dict(bad["writes"][1]["image"], delta=0)
    assert shadow.of(bad, prog, view) is None  # the delta names no region of the tune
