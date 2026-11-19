"""The LLVM InstCombine round trip (``cgen/llvmopt.py``): lowering, recovery and the printer's
reading of the forms it returns."""

import re

import numpy as np
import pytest

from deity_informant.cgen import llvmopt, mlir, printer
from wayward_eagle.c import text

ORIG = """"builtin.module"() ({
  "memref.global"() <{sym_name = "M", sym_visibility = "private", type = memref<65536xi8>}> : () -> () loc("<stdin>":2:3)
  "func.func"() <{function_type = (i16) -> (), sym_name = "trap", sym_visibility = "private"}> ({
  }) : () -> () loc("<stdin>":4:3)
  "func.func"() <{function_type = (i8, i8) -> (i8, i16), sym_name = "g", sym_visibility = "private"}> ({
  ^bb0(%arg0: i8 loc("<stdin>":5:1), %arg1: i8 loc("<stdin>":5:2)):
    %0 = "arith.constant"() <{value = 7 : i16}> : () -> i16 loc(unknown)
    "func.return"(%arg0, %0) : (i8, i16) -> () loc("0x1000"("1:1"))
  }) : () -> () loc("<stdin>":5:3)
  "func.func"() <{function_type = (i8, i8) -> i8, sym_name = "f"}> ({
  ^bb0(%a: i8, %b: i8):
    %g = "memref.get_global"() <{name = @M}> : () -> memref<65536xi8> loc("<stdin>":9:9)
    %1 = "arith.constant"() <{value = 4096 : index}> : () -> index loc(unknown)
    %2 = "memref.load"(%g, %1) : (memref<65536xi8>, index) -> i8 loc("0x1002"("1:2"))
    %3 = "arith.addi"(%2, %a) : (i8, i8) -> i8 loc("0x1004"("1:3"))
    %4:2 = "func.call"(%3, %b) <{callee = @g, no_inline}> {site = 5 : i32} : (i8, i8) -> (i8, i16) loc(callsite("0x1006"("1:4") at "0x2000"("2:1")))
    "memref.store"(%4#0, %g, %1) : (i8, memref<65536xi8>, index) -> () loc("0x1008"("1:5"))
    %5 = "arith.cmpi"(%3, %a) <{predicate = 9 : i64}> : (i8, i8) -> i1 loc("0x100a"("1:6"))
    "cf.cond_br"(%5, %3)[^bb1, ^bb2] <{operandSegmentSizes = array<i32: 1, 1, 0>}> : (i1, i8) -> () loc("0x100c"("1:7"))
  ^bb1(%6: i8):
    "func.return"(%6) : (i8) -> () loc("0x100e"("1:8"))
  ^bb2:
    "func.return"(%a) : (i8) -> () loc("0x1010"("1:9"))
  }) : () -> () loc("<stdin>":8:3)
}) : () -> () loc("<stdin>":1:1)
"""


@pytest.fixture(name="lowered", scope="module")
def fixture_lowered():
    """``(text, table)`` of :data:`ORIG` lowered."""
    return llvmopt.lower(ORIG)


def number(table, where):
    """The location number of location text ``where``."""
    return table.locs.index(where) + 1


def test_fused_location_chain_is_its_first_part():
    """A location LLVM fused from several reads as its first part's chain, the call sites an
    inlined operation came through included."""
    inlined = 'callsite("0x19a1"("15:54") at "0x142b"("5:30"))'
    other = 'callsite("0x199e"("15:51") at "0x142b"("5:30"))'
    chain = ((0x19A1, "15:54"), (0x142B, "5:30"))
    assert mlir._chain(f"fused[{inlined}, {other}]") == chain
    assert mlir._chain(f"fused[{inlined}]") == chain
    assert mlir._chain('fused["0x1000"("1:1"), "0x1002"("1:2")]') == ((0x1000, "1:1"),)
    assert mlir._chain("fused[]") == ()


def test_lowering_makes_memory_calls_and_numbers_locations(lowered):
    text, table = lowered
    assert "memref" not in text.replace("memref.global", "")
    assert re.search(r'%2 = "llvm.call"\(%__\d+\) <\{.*callee = @__a0.*\(i64\) -> i8', text)
    assert re.search(r'"llvm.call"\(%4#0, %__\d+\) <\{.*callee = @__a1.*\(i8, i64\) -> \(\)', text)
    assert text.count('"arith.index_cast"(%1) : (index) -> i64') == 2
    assert '"llvm.load"(%p) <{volatile_, ordering = 0 : i64}> : (!llvm.ptr) -> i8' in text
    assert '"llvm.store"(%x, %p) <{volatile_, ordering = 0 : i64}>' in text
    assert text.count("always_inline") == 2 and "alias_scopes" not in text
    assert 'sym_name = "trap", sym_visibility = "private"}> ({\n}) {no_unwind' in text
    assert "{site" not in text and list(dict.fromkeys(re.findall(r"loc\((.*?)\)", text))) == [
        f'"o":{n}:1' for n in range(1, len(table.locs) + 1)
    ]
    call = number(table, 'callsite("0x1006"("1:4") at "0x2000"("2:1"))')
    assert table.names[call - 1] == "func.call"
    assert table.props[call - 1] == "callee = @g, no_inline"
    assert table.attrs[call - 1] == "{site = 5 : i32}"
    assert table.names[number(table, '"0x1002"("1:2")') - 1] == "memref.load"
    assert table.gets == {"f": {"M": '"<stdin>":9:9'}}
    assert table.close == '}) : () -> () loc("<stdin>":1:1)'
    assert table.funcs["f"][1] == '}) : () -> () loc("<stdin>":8:3)'
    branch = number(table, '"0x100c"("1:7")')
    assert table.branches[branch] == (
        number(table, '"0x100e"("1:8")'),
        number(table, '"0x1010"("1:9")'),
    )


def llvm_func(table, name, body, args="%a: i8, %b: i8", ftype="i8 (i8, i8)"):
    """An imported ``llvm`` module holding function ``name`` of ``body`` (with ``{N:where}``
    standing for the location number of location text ``where``)."""
    body = re.sub(r"\{N:(.*?)\}", lambda m: f'loc("o":{number(table, m.group(1))}:1)', body)
    return f""""builtin.module"() ({{
  "llvm.func"() <{{function_type = !llvm.func<{ftype}>, sym_name = "{name}"}}> ({{
  ^bb0({args}):
{body}
  }}) : () -> ()
}}) : () -> ()
"""


def recovered(table, body, name="f", **kw):
    """The parsed recovered function of :func:`llvm_func`."""
    out = llvmopt.recover(llvm_func(table, name, body, **kw), table)
    top = mlir.parse(out)
    return next(o for o in top.regions[0][0].ops if o.prop("sym_name") == name), out


def ops(fn):
    """The operations of ``fn``, in order."""
    return [o for b in fn.regions[0] for o in b.ops]


def test_recovery_restores_dialects_locations_and_attributes(lowered):
    _, table = lowered
    body = """
    %c = "llvm.mlir.constant"() <{value = 4096 : i64}> : () -> i64
    %t = "llvm.mlir.constant"() <{value = true}> : () -> i1
    %m = "llvm.mlir.addressof"() <{global_name = @M}> : () -> !llvm.ptr
    %p = "llvm.getelementptr"(%m, %c) <{elem_type = i8, noWrapFlags = 3 : i32, rawConstantIndices = array<i32: -2147483648>}> : (!llvm.ptr, i64) -> !llvm.ptr {N:"0x1002"("1:2")}
    %0 = "llvm.load"(%p) <{ordering = 0 : i64}> : (!llvm.ptr) -> i8 {N:"0x1002"("1:2")}
    %1 = "llvm.or"(%0, %a) <{isDisjoint}> : (i8, i8) -> i8 {N:"0x1004"("1:3")}
    %2 = "llvm.call"(%1, %b) <{callee = @g}> : (i8, i8) -> !llvm.struct<(i8, i16)> {N:callsite("0x1006"("1:4") at "0x2000"("2:1"))}
    %3 = "llvm.extractvalue"(%2) <{position = array<i64: 0>}> : (!llvm.struct<(i8, i16)>) -> i8
    %f = "llvm.freeze"(%3) : (i8) -> i8
    "llvm.store"(%f, %p) <{ordering = 0 : i64}> : (i8, !llvm.ptr) -> () {N:"0x1008"("1:5")}
    %4 = "llvm.icmp"(%1, %a) <{predicate = 9 : i64}> : (i8, i8) -> i1 {N:"0x100a"("1:6")}
    %5 = "llvm.xor"(%4, %t) : (i1, i1) -> i1 loc("o":0:0)
    %6 = "llvm.zext"(%1) <{nonNeg}> : (i8) -> i64 loc(unknown)
    %7 = "llvm.trunc"(%6) : (i64) -> i16 loc(unknown)
    %8 = "llvm.sext"(%5) : (i1) -> i8 loc(unknown)
    "llvm.cond_br"(%4, %8)[^bb1, ^bb2] <{operandSegmentSizes = array<i32: 1, 1, 0>}> : (i1, i8) -> () {N:"0x100c"("1:7")}
  ^bb1(%9: i8):
    "llvm.return"(%9) : (i8) -> () {N:"0x100e"("1:8")}
  ^bb2:
    "llvm.return"(%a) : (i8) -> () {N:"0x1010"("1:9")}"""
    fn, out = recovered(table, body)
    by = {o.name: o for o in ops(fn)}
    assert by["memref.load"].loc == ((0x1002, "1:2"),)
    cast = next(o for o in ops(fn) if o.res == [by["memref.load"].args[1]])
    k = next(o for o in ops(fn) if o.res == [cast.args[0]])
    assert cast.name == "arith.index_castui" and (k.int_prop("value"), k.types[1]) == (
        4096,
        ["i16"],
    )
    assert by["memref.get_global"].where == '"<stdin>":9:9'
    assert by["arith.addi"].loc == ((0x1004, "1:3"),)
    call = by["func.call"]
    assert call.res == ["%2#0", "%2#1"] and call.loc == ((0x1006, "1:4"), (0x2000, "2:1"))
    assert "no_inline" in call.props and call.int_prop("site") == 5
    store = by["memref.store"]
    assert store.args[0] == "%2#0" and store.args[1] == by["memref.get_global"].res[0]
    assert by["arith.cmpi"].int_prop("predicate") == 9
    assert by["arith.xori"].loc == ((0x100A, "1:6"),)
    assert by["arith.extsi"].loc == ((0x100C, "1:7"),)
    casts = [o.types for o in ops(fn) if o.name == "arith.index_castui"]
    assert casts.count((["i16"], ["index"])) == 2 and (["i8"], ["index"]) in casts
    assert by["arith.constant"].types[1] in (["index"], ["i1"])
    assert '"func.func"() <{function_type = (i8, i8) -> i8, sym_name = "f"}>' in out
    assert "__ld" not in out and "memref.global" in out and "llvm." not in out
    assert out.rstrip().endswith('}) : () -> () loc("<stdin>":1:1)')


def test_multi_result_function_and_struct_values(lowered):
    _, table = lowered
    body = """
    %p = "llvm.mlir.poison"() : () -> !llvm.struct<(i8, i16)>
    %z = "llvm.mlir.zero"() : () -> !llvm.struct<(i8, i16)>
    %k = "llvm.mlir.constant"() <{value = 7 : i16}> : () -> i16
    %c = "llvm.icmp"(%arg0, %arg1) <{predicate = 6 : i64}> : (i8, i8) -> i1
    %0 = "llvm.insertvalue"(%p, %arg0) <{position = array<i64: 0>}> : (!llvm.struct<(i8, i16)>, i8) -> !llvm.struct<(i8, i16)>
    %1 = "llvm.insertvalue"(%0, %k) <{position = array<i64: 1>}> : (!llvm.struct<(i8, i16)>, i16) -> !llvm.struct<(i8, i16)>
    %2 = "llvm.select"(%c, %1, %z) : (i1, !llvm.struct<(i8, i16)>, !llvm.struct<(i8, i16)>) -> !llvm.struct<(i8, i16)>
    "llvm.switch"(%arg0, %2, %p)[^bb1, ^bb1, ^bb2] <{case_operand_segments = array<i32: 1, 0>, case_values = dense<[3, 4]> : vector<2xi8>, operandSegmentSizes = array<i32: 1, 1, 1>}> : (i8, !llvm.struct<(i8, i16)>, !llvm.struct<(i8, i16)>) -> () {N:"0x1000"("1:1")}
  ^bb1(%s: !llvm.struct<(i8, i16)>):
    "llvm.return"(%s) : (!llvm.struct<(i8, i16)>) -> () {N:"0x1000"("1:1")}
  ^bb2:
    "llvm.unreachable"() : () -> ()"""
    fn, _ = recovered(
        table,
        body,
        "g",
        args="%arg0: i8, %arg1: i8",
        ftype="struct<(i8, i16)> (i8, i8)",
    )
    blocks = fn.regions[0]
    assert [t for _, t in blocks[1].args] == ["i8", "i16"]
    sw = blocks[0].ops[-1]
    assert sw.name == "cf.switch" and [e[2] for e in sw.edges()] == [None, 3, 4]
    assert [len(e[1]) for e in sw.edges()] == [2, 2, 0]
    assert [o.name for o in blocks[0].ops].count("arith.select") == 2
    ret = blocks[1].ops[-1]
    assert ret.name == "func.return" and ret.args == [a for a, _ in blocks[1].args]
    assert blocks[2].ops[-1].name == "func.return" and len(blocks[2].ops[-1].args) == 2
    f = printer.Func(fn, None)
    a, b = np.meshgrid(np.arange(256), np.arange(256))
    want = np.where(a < b, a, 0)
    edge = sw.edges()[0][1]
    got = f.evaluate(sw.args[edge[0]], {"%arg0": a, "%arg1": b}, {})
    assert (got == want).all()


def test_unrecoverable_operations_raise(lowered):
    _, table = lowered
    with pytest.raises(ValueError, match="llvm.fadd"):
        recovered(
            table,
            '    %0 = "llvm.fadd"(%a, %b) : (i8, i8) -> i8\n    "llvm.return"(%0) : (i8) -> ()',
        )
    with pytest.raises(ValueError, match="umul.with.overflow"):
        recovered(
            table,
            '    %0 = "llvm.intr.umul.with.overflow"(%a, %b) : (i8, i8) -> !llvm.struct<(i8, i1)>\n'
            '    "llvm.return"(%a) : (i8) -> ()',
        )


def s8(x):
    """``x`` as signed bytes."""
    return (x ^ 0x80) - 0x80


#: Each intrinsic's operand count and its value on bytes ``a``, ``b`` (and ``b`` as a third).
INTRINSICS = {
    "smax": (2, lambda a, b: np.maximum(s8(a), s8(b))),
    "smin": (2, lambda a, b: np.minimum(s8(a), s8(b))),
    "umax": (2, np.maximum),
    "umin": (2, np.minimum),
    "abs": (1, lambda a, b: np.abs(s8(a))),
    "fshl": (3, lambda a, b: _fsh(a, b, True)),
    "fshr": (3, lambda a, b: _fsh(a, b, False)),
    "uadd.sat": (2, lambda a, b: np.minimum(a + b, 255)),
    "usub.sat": (2, lambda a, b: np.maximum(a - b, 0)),
    "sadd.sat": (2, lambda a, b: np.clip(s8(a) + s8(b), -128, 127)),
    "ssub.sat": (2, lambda a, b: np.clip(s8(a) - s8(b), -128, 127)),
    "ctpop": (1, lambda a, b: np.vectorize(lambda x: bin(x).count("1"))(a)),
    "ctlz": (1, lambda a, b: np.vectorize(lambda x: 8 - int(x).bit_length())(a)),
    "cttz": (
        1,
        lambda a, b: np.vectorize(lambda x: (int(x) & -int(x)).bit_length() - 1 if x else 8)(a),
    ),
    "bitreverse": (1, lambda a, b: np.vectorize(lambda x: int(f"{x:08b}"[::-1], 2))(a)),
    "ucmp": (2, lambda a, b: np.sign(a - b)),
    "scmp": (2, lambda a, b: np.sign(s8(a) - s8(b))),
}


def _fsh(a, b, left):
    """``fshl(a, b, b)`` or ``fshr(a, b, b)`` on bytes."""
    s = b % 8
    wide = (a << 8) | b
    return ((wide << s) >> 8) & 0xFF if left else (wide >> s) & 0xFF


def evaluated(table, body, a, b, ftype="i8 (i8, i8)"):
    """The value function ``f`` of ``body`` returns for each ``a``, ``b``."""
    fn, _ = recovered(table, body, ftype=ftype)
    f = printer.Func(fn, None)
    ret = fn.regions[0][-1].ops[-1]
    return f.evaluate(ret.args[0], {"%a": a, "%b": b}, {})


@pytest.mark.parametrize("name", sorted(INTRINSICS))
def test_intrinsic_expansions(lowered, name):
    _, table = lowered
    n, ref = INTRINSICS[name]
    args = ", ".join(["%a", "%b", "%b"][:n])
    types = ", ".join(["i8"] * n)
    body = f"""
    %0 = "llvm.intr.{name}"({args}) : ({types}) -> i8 {{N:"0x1000"("1:1")}}
    "llvm.return"(%0) : (i8) -> () {{N:"0x1000"("1:1")}}"""
    a, b = np.meshgrid(np.arange(256), np.arange(256))
    got = evaluated(table, body, a, b)
    assert (got == ref(a, b) & 0xFF).all()


@pytest.mark.parametrize(
    "name,ref",
    [
        ("uadd", lambda a, b: a + b > 255),
        ("usub", lambda a, b: a < b),
        ("sadd", lambda a, b: abs(s8(a) + s8(b) + 0.5) > 128),
        ("ssub", lambda a, b: abs(s8(a) - s8(b) + 0.5) > 128),
    ],
)
def test_overflow_intrinsics(lowered, name, ref):
    _, table = lowered
    st = "!llvm.struct<(i8, i1)>"
    body = f"""
    %0 = "llvm.intr.{name}.with.overflow"(%a, %b) : (i8, i8) -> {st} {{N:"0x1000"("1:1")}}
    %1 = "llvm.extractvalue"(%0) <{{position = array<i64: 1>}}> : ({st}) -> i1
    %2 = "llvm.extractvalue"(%0) <{{position = array<i64: 0>}}> : ({st}) -> i8
    %3 = "llvm.zext"(%1) : (i1) -> i8
    %4 = "llvm.shl"(%3, %3) : (i8, i8) -> i8
    %5 = "llvm.xor"(%2, %4) : (i8, i8) -> i8
    "llvm.return"(%5) : (i8) -> () {{N:"0x1000"("1:1")}}"""
    a, b = np.meshgrid(np.arange(256), np.arange(256))
    op = np.add if name[1:] == "add" else np.subtract
    want = (op(a, b) & 0xFF) ^ (ref(a, b).astype(np.int64) << 1)
    assert (evaluated(table, body, a, b) == want).all()


def test_bswap_and_signed_operations(lowered):
    _, table = lowered
    body = """
    %0 = "llvm.zext"(%a) : (i8) -> i16
    %1 = "llvm.zext"(%b) : (i8) -> i16
    %k = "llvm.mlir.constant"() <{value = 8 : i16}> : () -> i16
    %2 = "llvm.shl"(%1, %k) : (i16, i16) -> i16
    %3 = "llvm.or"(%2, %0) : (i16, i16) -> i16
    %4 = "llvm.intr.bswap"(%3) : (i16) -> i16
    %5 = "llvm.ashr"(%4, %k) : (i16, i16) -> i16
    %6 = "llvm.sext"(%a) : (i8) -> i16
    %7 = "llvm.xor"(%5, %6) : (i16, i16) -> i16
    %8 = "llvm.trunc"(%7) : (i16) -> i8
    "llvm.return"(%8) : (i8) -> () {N:"0x1000"("1:1")}"""
    a, b = np.meshgrid(np.arange(256), np.arange(256))
    want = ((s8(a) >> 0) ^ s8(a)) & 0xFF
    assert (evaluated(table, body, a, b) == want).all()


def test_branch_successors_restored(lowered):
    _, table = lowered
    body = """
    %0 = "llvm.icmp"(%a, %b) <{predicate = 6 : i64}> : (i8, i8) -> i1 {N:"0x100a"("1:6")}
    "llvm.cond_br"(%0, %a)[^bb2, ^bb1] <{operandSegmentSizes = array<i32: 1, 0, 1>}> : (i1, i8) -> () {N:"0x100c"("1:7")}
  ^bb1(%9: i8):
    "llvm.return"(%9) : (i8) -> () {N:"0x100e"("1:8")}
  ^bb2:
    "llvm.return"(%a) : (i8) -> () {N:"0x1010"("1:9")}"""
    fn, _ = recovered(table, body)
    br = fn.regions[0][0].ops[-1]
    assert br.name == "cf.cond_br" and br.succs == ["bb1", "bb2"]
    assert br.ints("operandSegmentSizes") == [1, 1, 0]
    f = printer.Func(fn, None)
    a, b = np.meshgrid(np.arange(256), np.arange(256))
    assert (f.evaluate(br.args[0], {"%a": a, "%b": b}, {}) == (a >= b)).all()


def test_printer_reads_signed_and_min_max_forms():
    text = """"builtin.module"() ({
  "func.func"() <{function_type = (i8, i8) -> i8, sym_name = "f"}> ({
  ^bb0(%a: i8, %b: i8):
    %k = "arith.constant"() <{value = 3 : i8}> : () -> i8
    %m = "arith.constant"() <{value = 100 : i8}> : () -> i8
    %0 = "arith.minui"(%a, %k) : (i8, i8) -> i8
    %1 = "arith.maxui"(%0, %k) : (i8, i8) -> i8
    %2 = "arith.remui"(%m, %k) : (i8, i8) -> i8
    %3 = "arith.shrsi"(%m, %k) : (i8, i8) -> i8
    %4 = "arith.extsi"(%1) : (i8) -> i16
    %5 = "arith.maxsi"(%a, %b) : (i8, i8) -> i8
    "func.return"(%5) : (i8) -> ()
  }) : () -> ()
}) : () -> ()
"""
    f = printer.Func(mlir.parse(text).regions[0][0].ops[0], None)
    assert f.iv("%0") == (0, 3) and f.iv("%1") == (3, 3) and f.iv("%2") == (0, 2)
    assert f.iv("%3") == (12, 12) and f.iv("%4") == (3, 3) and f.iv("%5") == (0, 255)
    for name in ("%2", "%3"):
        assert f.evaluate(name, {}, {}) == {"%2": 1, "%3": 12}[name]


@pytest.mark.parametrize(
    "pred,k,want",
    [(4, 0xFF, "(int8_t)v > -1"), (2, 0, "(int8_t)v < 0"), (5, 3, "(int8_t)v >= 3")],
)
def test_signed_compares_print_signed(pred, k, want):
    e = printer.CFunc.cmpi(pred, printer.var("v", 8), printer.lit(k, 8), 8)
    assert text(e.node) == want
    two = printer.CFunc.cmpi(3, printer.var("v", 8), printer.var("w", 8), 8)
    assert text(two.node) == "(int8_t)v <= (int8_t)w"


def test_signed_intervals_bound_branches():
    assert printer.solution(4, 0xFF, 8) == (0, 0x7F)
    assert printer.solution(2, 0, 8) == (0x80, 0xFF)
    assert printer.solution(2, 5, 8) is None and printer.solution(6, 5, 8) == (0, 4)


@pytest.fixture(name="prefix", scope="module")
def fixture_prefix():
    """The command prefix running the MLIR and LLVM tools; skips without them."""
    prefix = mlir.runner(build=True)
    if prefix is None:
        pytest.skip("needs mlir-opt or Docker")
    return prefix


SHIFTS = """"builtin.module"() ({
  "memref.global"() <{sym_name = "M", sym_visibility = "private", type = memref<65536xi8>}> : () -> () loc("<stdin>":2:3)
  "func.func"() <{function_type = (i8) -> (i8, i8), sym_name = "f", sym_visibility = "private"}> ({
  ^bb0(%a: i8):
    %g = "memref.get_global"() <{name = @M}> : () -> memref<65536xi8> loc("<stdin>":3:1)
    %one = "arith.constant"() <{value = 1 : i8}> : () -> i8 loc(unknown)
    %i = "arith.constant"() <{value = 53248 : index}> : () -> index loc(unknown)
    %0 = "arith.shrui"(%a, %one) : (i8, i8) -> i8 loc("0x1000"("1:1"))
    %1 = "arith.shrui"(%0, %one) : (i8, i8) -> i8 loc("0x1001"("1:2"))
    %2 = "arith.shrui"(%1, %one) : (i8, i8) -> i8 loc("0x1002"("1:3"))
    %3 = "arith.shrui"(%2, %one) : (i8, i8) -> i8 loc("0x1003"("1:4"))
    %4 = "memref.load"(%g, %i) : (memref<65536xi8>, index) -> i8 loc("0x1004"("1:5"))
    "memref.store"(%3, %g, %i) : (i8, memref<65536xi8>, index) -> () loc("0x1006"("1:6"))
    %5 = "memref.load"(%g, %i) : (memref<65536xi8>, index) -> i8 loc("0x1008"("1:7"))
    "func.return"(%4, %5) : (i8, i8) -> () loc("0x100a"("1:8"))
  }) : () -> () loc("<stdin>":3:3)
  "func.func"() <{function_type = (i8) -> i8, sym_name = "main"}> ({
  ^bb0(%a: i8):
    %0:2 = "func.call"(%a) <{callee = @f, no_inline}> : (i8) -> (i8, i8) loc("0x2000"("2:1"))
    "func.return"(%0#1) : (i8) -> () loc("0x2002"("2:2"))
  }) : () -> () loc("<stdin>":9:3)
}) : () -> () loc("<stdin>":1:1)
"""


def test_shift_chain_folds_end_to_end(prefix):
    out = llvmopt.combined(SHIFTS, prefix)
    top = mlir.parse(out)
    f, main = top.regions[0][0].ops[1:3]
    body = ops(f)
    shifts = [o for o in body if o.name == "arith.shrui"]
    assert len(shifts) == 1 and shifts[0].pc in range(0x1000, 0x1004)
    k = next(o for o in body if o.res == [shifts[0].args[1]])
    assert k.int_prop("value") == 4
    mem = [(o.name, o.pc) for o in body if o.name.startswith("memref.") and o.pc]
    assert mem == [("memref.load", 0x1004), ("memref.store", 0x1006), ("memref.load", 0x1008)]
    call = ops(main)[0]
    assert call.res == ["%0#0", "%0#1"] and "no_inline" in call.props
    assert body[-1].name == "func.return" and len(body[-1].args) == 2


RANGED = SHIFTS.replace(
    '"func.return"(%4, %5) : (i8, i8) -> () loc("0x100a"("1:8"))',
    """%ten = "arith.constant"() <{value = 10 : i8}> : () -> i8 loc(unknown)
    %6 = "arith.cmpi"(%4, %ten) <{predicate = 6 : i64}> : (i8, i8) -> i1 loc("0x1009"("1:9"))
    %7 = "arith.extui"(%6) : (i1) -> i8 loc("0x1009"("1:9"))
    "func.return"(%7, %5) : (i8, i8) -> () loc("0x100a"("1:8"))""",
)


def test_load_ranges_fold_end_to_end(prefix):
    """A load's interval is its accessor's ``range``: a compare it decides folds; with its
    address set the load is dead once nothing reads it, and the load after the store reads
    the stored value, while without one both stay (volatile)."""
    cell = {"f": [([(0xD000, 0xD001)], False)] * 3}
    low, _ = llvmopt.lower(RANGED, {"f": [(0, 9), None]}, cell)
    assert low.count("llvm.range = #llvm.constant_range<i8, 0, 10>") == 1
    assert llvmopt.lower(RANGED, {"f": [(0, 0xFF), None]})[0].count("constant_range") == 0
    out = llvmopt.combined(RANGED, prefix, {"f": [(0, 9), None]}, cell)
    body = ops(mlir.parse(out).regions[0][0].ops[1])
    assert not [o for o in body if o.name == "arith.cmpi"]
    ret = body[-1]
    one = next(o for o in body if o.res == [ret.args[0]])
    assert one.name == "arith.constant" and one.int_prop("value") == 1
    assert not [o for o in body if o.name == "memref.load"]
    [store] = [o for o in body if o.name == "memref.store"]
    assert ret.args[1] == store.args[0] and store.pc == 0x1006
    unfolded = ops(mlir.parse(llvmopt.combined(RANGED, prefix)).regions[0][0].ops[1])
    assert [o for o in unfolded if o.name == "arith.cmpi"]
    assert [o.pc for o in unfolded if o.name == "memref.load"] == [0x1004, 0x1008]


ALIASED = """"builtin.module"() ({
  "memref.global"() <{sym_name = "M", sym_visibility = "private", type = memref<65536xi8>}> : () -> () loc("<stdin>":2:3)
  "func.func"() <{function_type = (i16, i8) -> (), sym_name = "io_write", sym_visibility = "private"}> ({
  }) : () -> () loc("<stdin>":3:3)
  "func.func"() <{function_type = (i8, i8) -> i8, sym_name = "f"}> ({
  ^bb0(%a: i8, %b: i8):
    %g = "memref.get_global"() <{name = @M}> : () -> memref<65536xi8> loc("<stdin>":4:1)
    %c = "arith.constant"() <{value = 4096 : index}> : () -> index loc(unknown)
    %d = "arith.constant"() <{value = 54272 : i16}> : () -> i16 loc(unknown)
    %five = "arith.constant"() <{value = 5 : i16}> : () -> i16 loc(unknown)
    %0 = "arith.extui"(%a) : (i8) -> i16 loc("0x1000"("1:1"))
    %1 = "arith.shli"(%0, %five) : (i16, i16) -> i16 loc("0x1000"("1:1"))
    %2 = "arith.index_castui"(%1) : (i16) -> index loc("0x1000"("1:1"))
    "memref.store"(%a, %g, %c) : (i8, memref<65536xi8>, index) -> () loc("0x1002"("1:2"))
    "memref.store"(%b, %g, %c) : (i8, memref<65536xi8>, index) -> () loc("0x1004"("1:3"))
    "memref.store"(%a, %g, %2) : (i8, memref<65536xi8>, index) -> () loc("0x1006"("1:4"))
    "func.call"(%d, %a) <{callee = @io_write}> : (i16, i8) -> () loc("0x1008"("1:5"))
    %3 = "memref.load"(%g, %c) : (memref<65536xi8>, index) -> i8 loc("0x100a"("1:6"))
    %4 = "memref.load"(%g, %2) : (memref<65536xi8>, index) -> i8 loc("0x100c"("1:7"))
    %5 = "arith.addi"(%3, %4) : (i8, i8) -> i8 loc("0x100e"("1:8"))
    "func.return"(%5) : (i8) -> () loc("0x1010"("1:9"))
  }) : () -> () loc("<stdin>":5:3)
}) : () -> () loc("<stdin>":1:1)
"""


@pytest.mark.parametrize("hi", [0x1000, 0x2000])
def test_memory_facts_forward_and_remove_end_to_end(prefix, hi):
    """With the address sets: a store overwritten before any read is dead, a load reads the
    store before it across a store of a disjoint set and device I/O (an I/O call of
    volatile bytes only), the indexed store's
    address comes back as 16-bit arithmetic; a set that may reach the cell keeps its load."""
    cell, run = [(0x1000, 0x1001)], [(0, hi)]
    sets = {"f": [(cell, False)] * 2 + [(run, False), (cell, False), (run, False)]}
    body = ops(mlir.parse(llvmopt.combined(ALIASED, prefix, sets=sets, devices={"f": [True]})).regions[0][0].ops[2])
    stores = [o.pc for o in body if o.name == "memref.store"]
    assert stores == [0x1004, 0x1006]
    loads = [o.pc for o in body if o.name == "memref.load"]
    assert loads == ([] if hi == 0x1000 else [0x100A])
    idx = [o for o in body if o.name == "memref.store"][1].args[2]
    cast = next(o for o in body if o.res == [idx])
    add = next(o for o in body if o.res == [cast.args[0]])
    assert cast.name == "arith.index_castui" and add.name == "arith.shli"
    assert add.types[1] == ["i16"]


def test_generic_text_round_trips():
    """:func:`mlir.generic` prints what :func:`mlir.parse` read, discardable attributes after
    the properties, multi-result names and block labels as they were."""
    src = SHIFTS.replace(
        '"func.return"(%0#1) : (i8) -> ()', '"func.return"(%0#1) {tag = 1 : i8} : (i8) -> ()'
    )
    top = mlir.parse(src)
    text = mlir.generic(top)
    assert mlir.generic(mlir.parse(text)) == text
    assert '%0:2 = "func.call"(%a) <{callee = @f, no_inline}>' in text
    assert '"func.return"(%0#1) {tag = 1 : i8} : (i8) -> ()' in text
    assert "^bb0(%a: i8):" in text
    ret = mlir.parse(text).regions[0][0].ops[2].regions[0][0].ops[-1]
    assert ret.attrs == "tag = 1 : i8" and ret.prop("tag") == "1 : i8"
