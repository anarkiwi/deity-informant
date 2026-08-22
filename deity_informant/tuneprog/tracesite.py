"""S1 -- what one executed site records, resolved once and compiled to Python.

A site is ``(pc, instruction bytes)`` -- the tracing VM's cache key -- so all of
it that cannot change between executions is resolved on the first one and held
in one list, which the step loop then only indexes.
"""

from __future__ import annotations

from ..lifter import OPS, MODE_LEN, STATUS_BITS
from ..vm import _emit_line, _rd_expr, _lhs

IDX_REG = {"absx": 1, "zpx": 1, "indx": 1, "absy": 2, "zpy": 2, "indy": 2}
ILEN = [MODE_LEN[OPS[b][1]] for b in range(256)]
IDX_SLOT = [IDX_REG.get(OPS[b][1], 0) for b in range(256)]
PEN_REG = {"ax": (1, False), "ay": (2, False), "iy": (2, True)}  # index slot, indirect
_CODE = {}  # generated source -> code object; two sites of one shape compile once
WROTE = 2  # TraceVM.known: 1 = loaded or power-on, 2 = written by a traced store
_FLAGS = sum(1 << i for i, _b in STATUS_BITS)

K_NEXT, K_BR, K_JMP, K_JMPIND, K_JSR, K_RTS, K_RTI, K_BRK, K_JAM = range(9)
KIND = {
    "next": K_NEXT,
    "br": K_BR,
    "jmp": K_JMP,
    "jmpind": K_JMPIND,
    "jsr": K_JSR,
    "rts": K_RTS,
    "rti": K_RTI,
    "brk": K_BRK,
    "jam": K_JAM,
}

(
    S_F,
    S_N,
    S_KIND,
    S_CYC,
    S_PEN,
    S_CTRL,
    S_RD,
    S_WR,
    S_E0,
    S_E1,
    S_AUX,
    S_RET,
    S_EK,
    S_PH,
    S_PH0,
    S_IDX,
    S_RS,
    S_WS,
    S_B0,
    S_NB,
    S_B1,
    S_B2,
    S_STABLE,
) = range(23)


def reg_masks(rec):
    """``(read-before-write, written)`` register-file bitmasks of one instruction."""
    rd = wr = 0
    for _mn, out, ins in rec["ops"]:
        for vn in ins:
            if vn[0] == "r":
                b = 1 << vn[1]
                if not wr & b:
                    rd |= b
        if out is not None and out[0] == "r":
            wr |= 1 << out[1]
    stk = rec["stk"]
    if stk is not None:
        wr |= 1 << 3
        if stk in ("rts", "rti"):
            rd |= 1 << 3
        if stk == "rti":  # step() pops the status byte into the flag registers
            wr |= _FLAGS
        elif stk == "brk":  # step() pushes the status byte and sets I
            rd |= _FLAGS & ~wr
            wr |= _FLAGS | (1 << 10)
    return rd, wr


def compile_site(vm, pc, rec, slot, idx, rs, ws):
    """The site's P-Code as one nullary closure over the VM and its own access sets.

    Register file, uniques, ``rd``/``wr``, the ``(pc, op index)`` pair of each
    access and the address set it fills are baked in, so a ``(zp),Y`` pointer
    fetch and the stream load it feeds are attributed separately at no lookup
    cost; the index domain is sampled before any op can move a register.
    """
    lines = []
    ns = {"r": vm.reg, "u": vm.uniq, "rd": vm.read, "wr": vm.write}
    if slot:
        ns["I"] = idx
        lines.append("I.add(r[%d])" % slot)
    for i, (mn, out, ins) in enumerate(rec["ops"]):
        if mn in ("LOAD", "STORE"):
            store = mn == "STORE"
            d = ws if store else rs
            s = d.get(i)
            if s is None:
                s = d[i] = set()
            ns["P%d" % i] = (pc, i)
            ns[("W%d" if store else "R%d") % i] = s
            if store:
                lines.append(
                    "wr(%s, %s, %d, P%d, W%d)"
                    % (_rd_expr(ins[0]), _rd_expr(ins[1]), ins[1][2], i, i)
                )
            else:
                lines.append(
                    "%s = rd(%s, %d, P%d, R%d)" % (_lhs(out), _rd_expr(ins[0]), out[2], i, i)
                )
        else:
            lines.append(_emit_line(mn, out, ins))
    src = "def _f():\n    " + ("\n    ".join(lines) or "pass") + "\n"
    code = _CODE.get(src)
    if code is None:
        code = _CODE[src] = compile(src, "<pcode>", "exec")
    exec(code, ns)  # noqa: S102 - generated straight-line P-Code
    return ns["_f"]


def stable(known, pc, n):
    """True while no byte of this instruction has ever been written (WROTE in ``known``)."""
    return not any(known[(pc + k) & 0xFFFF] == WROTE for k in range(n))


def build(vm, pc, key, rec):
    """The site record for ``rec`` at ``pc``: everything the step loop indexes."""
    kind = KIND[rec["ctrl"][0]]
    op = key[1]
    ek = (pc, op)
    idx = set() if IDX_SLOT[op] else None
    rs, ws = {}, {}
    f = compile_site(vm, pc, rec, IDX_SLOT[op], idx, rs, ws)
    rdm, wrm = reg_masks(rec)
    pen = rec["pen"]
    pen = None if pen is None or pen[0] not in PEN_REG else PEN_REG[pen[0]] + (pen[1],)
    nxt = (pc + rec["len"]) & 0xFFFF
    e0, aux = None, None
    if kind == K_NEXT:
        e0 = vm.edge_slot(ek, nxt, "fall")
    elif kind == K_JMP:
        e0 = vm.edge_slot(ek, rec["ctrl"][1], "jmp")
    elif kind == K_JSR:
        e0 = vm.edge_slot(ek, rec["ctrl"][1], "jsr")
        aux = vm.call_slot(ek, nxt)
    elif kind in (K_RTS, K_RTI):
        aux = vm.ret_slot(ek)
    elif kind in (K_JMPIND, K_BRK):
        e0 = {}
    return [
        f,
        0,
        kind,
        rec["cyc"],
        pen,
        rec["ctrl"],
        rdm,
        wrm,
        e0,
        None,
        aux,
        nxt,
        ek,
        vm.phase,
        0,
        idx,
        rs,
        ws,
        op,
        len(key) - 2,
        key[2] if len(key) > 2 else 0,
        key[3] if len(key) > 3 else 0,
        False,
    ]


def recompile(vm, pc, op, rec, t):
    """Rebind a resumed site's closure to this VM, keeping its recorded sets."""
    t[S_F] = compile_site(vm, pc, rec, IDX_SLOT[op], t[S_IDX], t[S_RS], t[S_WS])
    return t
