"""Parametric witness synthesis: a writer gadget x a reader gadget x a timing harness.

Given ``(channel, role, timing)`` the synthesizer places the reader at a fixed address, emits a
store of that channel hitting it and wraps them for the timing. It returns a
``(source, entries, calls, init)`` tuple or ``None`` when the flat model cannot express the
combination (then the cell is a documented gap).
"""

from __future__ import annotations

_RD_ABS = 0x1400
_RD_ZP = 0x40
_PTR = 0xF0
_ZP_CHANNELS = ("zp", "zp_indexed_wrap", "zp_rmw", "zp_indexed_wrap_rmw")


def _store(channel, addr, lines):
    """Store register A to ``addr`` via a non-RMW ``channel`` (``False`` if unsupported)."""
    if channel == "zp":
        lines.append("        STA $%02X" % addr)
    elif channel == "zp_indexed_wrap":
        lines += ["        LDX #$01", "        STA $%02X,X" % ((addr - 1) & 0xFF)]
    elif channel == "abs":
        lines.append("        STA $%04X" % addr)
    elif channel == "abs_indexed":
        lines += ["        LDX #$01", "        STA $%04X,X" % (addr - 1)]
    else:
        return False
    return True


def _rmw(channel, addr, lines):
    if channel == "zp_rmw":
        lines.append("        INC $%02X" % addr)
    elif channel == "abs_rmw":
        lines.append("        INC $%04X" % addr)
    elif channel == "abs_indexed_rmw":
        lines += ["        LDX #$01", "        INC $%04X,X" % (addr - 1)]
    elif channel == "zp_indexed_wrap_rmw":
        lines += ["        LDX #$01", "        INC $%02X,X" % ((addr - 1) & 0xFF)]
    elif channel == "indirect_rmw":
        lines += [
            "        LDA #$%02X" % (addr & 0xFF),
            "        STA $%02X" % _PTR,
            "        LDA #$%02X" % (addr >> 8),
            "        STA $%02X" % (_PTR + 1),
            "        LDY #$00",
            "        DCP ($%02X),Y" % _PTR,
        ]
    else:
        return False
    return True


def _write(channel, addr, value, lines):
    """Emit a write of ``value`` to ``addr`` via ``channel`` (RMW ignores ``value``)."""
    if channel.endswith("_rmw"):
        return _rmw(channel, addr, lines)
    if channel == "indirect":
        lines += [
            "        LDA #$%02X" % (addr & 0xFF),
            "        STA $%02X" % _PTR,
            "        LDA #$%02X" % (addr >> 8),
            "        STA $%02X" % (_PTR + 1),
            "        LDY #$00",
            "        LDA #$%02X" % value,
            "        STA ($%02X),Y" % _PTR,
        ]
        return True
    if channel == "unstable_high":
        lines += ["        LDX #$%02X" % value, "        LDY #$00", "        SHX $%04X,Y" % addr]
        return True
    lines.append("        LDA #$%02X" % value)
    return _store(channel, addr, lines)


def _reader_at(rd, role):
    """Reader source lines at absolute ``rd``; the target address and the original byte."""
    org = "        .org $%04X" % rd
    body = ["rd:     LDA #$11", "        STA $C000", "        RTS"]
    tgt = rd + (1 if role == "operand" else 0)
    orig = 0x11 if role == "operand" else 0xA9
    return [org] + body, tgt, orig


def _value(role, orig):
    return 0x60 if role == "opcode" else (0x2A if orig != 0x2A else 0x2B)


def _code_witness(channel, role, timing):
    rd = _RD_ZP if channel in _ZP_CHANNELS else _RD_ABS
    reader, tgt, orig = _reader_at(rd, role)
    if channel in _ZP_CHANNELS and tgt > 0xFF:
        return None
    value = _value(role, orig)
    writer = []
    if not _write(channel, tgt, value, writer):
        return None
    if timing == "same_call_earlier":
        src = ["entry:"] + writer + ["        JMP rd"] + reader
        return "\n".join(src), ["entry"], [(0, 0), (0, 0)], ()
    if timing == "earlier_call":
        src = (
            ["entry:  CMP #$00", "        BEQ w", "        JMP rd", "w:"]
            + writer
            + ["        RTS"]
            + reader
        )
        return "\n".join(src), ["entry"], [(0, 0), (0, 1)], ()
    if timing == "pre_lift":
        src = ["entry:  JMP rd", "ini:"] + writer + ["        RTS"] + reader
        return "\n".join(src), ["entry", "ini"], [(0, 0)], [("ini", 0)]
    return None


def _same_insn(channel, role):
    """A looped RMW that modifies its own operand (writer pc == reader pc)."""
    if role != "operand":
        return None
    if channel == "abs_rmw":
        head, body = "entry:  LDX #$02", ["loop:   INC loop+1", "        DEX", "        BNE loop"]
    elif channel == "abs_indexed_rmw":
        head = "entry:  LDY #$02"
        body = ["        LDX #$00", "loop:   INC loop+1,X", "        DEY", "        BNE loop"]
    else:
        return None
    src = [head] + body + ["        STA $C000", "        RTS"]
    return "\n".join(src), ["entry"], [(0, 0)], ()


_JMPIND_PTR = 0x1430


def _ptr_reader(channel):
    """The reader, patched pointer address and patch value: ``(zp),Y`` or ``JMP (ind)``."""
    if channel in _ZP_CHANNELS or channel == "unstable_high":
        reader = [
            "rd:     LDY #$00",
            "        LDA ($%02X),Y" % _PTR,
            "        STA $C000",
            "        RTS",
            "        .org $1420",
            "targ:   .byte $42",
        ]
        return reader, _PTR, 0x20
    reader = [
        "rd:     JMP (iptr)",
        "        .org $14C0",
        "land:   LDA #$55",
        "        STA $C000",
        "        RTS",
        "        .org $14D0",
        "alt:    LDA #$56",
        "        STA $C000",
        "        RTS",
        "        .org $%04X" % _JMPIND_PTR,
        "iptr:   .byte lo(land), hi(land)",
    ]
    return reader, _JMPIND_PTR, 0xD0


def _ptr_witness(channel, timing):
    """A pointer a later indirect transfer reads, patched via ``channel``."""
    if channel == "unstable_high":
        return None
    reader, ptr, value = _ptr_reader(channel)
    writer = []
    if not _write(channel, ptr, value, writer):
        return None
    if timing == "same_call_earlier":
        src = ["entry:"] + writer + ["        JMP rd"] + reader
        return "\n".join(src), ["entry"], [(0, 0), (0, 0)], ()
    if timing == "earlier_call":
        src = (
            ["entry:  CMP #$00", "        BEQ w", "        JMP rd", "w:"]
            + writer
            + ["        RTS"]
            + reader
        )
        return "\n".join(src), ["entry"], [(0, 0), (0, 1)], ()
    if timing == "pre_lift":
        src = ["entry:  JMP rd", "ini:"] + writer + ["        RTS"] + reader
        return "\n".join(src), ["entry", "ini"], [(0, 0)], [("ini", 0)]
    return None


# The return address of entry's JSR sits at $01FC/$01FD under run_sub's deterministic SP.
_RET_LO, _RET_HI = 0x01FC, 0x01FD


def _ret_addr(channel, timing):
    """A subroutine whose pushed return address a store of ``channel`` clobbers."""
    if timing != "same_call_earlier":
        return None
    if channel == "abs":
        sub = [
            "sub:    LDA #lo(dest-1)",
            "        STA $%04X" % _RET_LO,
            "        LDA #hi(dest-1)",
            "        STA $%04X" % _RET_HI,
            "        RTS",
        ]
    elif channel == "abs_rmw":
        sub = [
            "sub:    LDA #lo(dest-2)",
            "        STA $%04X" % _RET_LO,
            "        LDA #hi(dest-1)",
            "        STA $%04X" % _RET_HI,
            "        INC $%04X" % _RET_LO,
            "        RTS",
        ]
    elif channel == "abs_indexed_rmw":
        sub = [
            "sub:    LDA #lo(dest-2)",
            "        STA $%04X" % _RET_LO,
            "        LDA #hi(dest-1)",
            "        STA $%04X" % _RET_HI,
            "        LDX #$01",
            "        INC $%04X,X" % (_RET_LO - 1),
            "        RTS",
        ]
    elif channel == "abs_indexed":
        sub = [
            "sub:    TSX",
            "        LDA #lo(dest-1)",
            "        STA $0101,X",
            "        LDA #hi(dest-1)",
            "        STA $0102,X",
            "        RTS",
        ]
    elif channel == "indirect":
        sub = [
            "sub:    LDA #$%02X" % (_RET_LO & 0xFF),
            "        STA $%02X" % _PTR,
            "        LDA #$01",
            "        STA $%02X" % (_PTR + 1),
            "        LDY #$00",
            "        LDA #lo(dest-1)",
            "        STA ($%02X),Y" % _PTR,
            "        INY",
            "        LDA #hi(dest-1)",
            "        STA ($%02X),Y" % _PTR,
            "        RTS",
        ]
    else:
        return None
    src = (
        ["entry:  JSR sub", "        STA $C001", "        RTS"]
        + sub
        + ["dest:   LDA #$77", "        STA $C002", "        RTS"]
    )
    return "\n".join(src), ["entry"], [(0, 0), (0, 0)], ()


def witness(channel, role, timing):
    """A ``(source, entries, calls, init)`` witnessing ``(channel, role, timing)``, or ``None``."""
    if timing == "same_insn":
        return _same_insn(channel, role)
    if role == "ret_addr":
        return _ret_addr(channel, timing)
    if role == "ptr_indirect":
        return _ptr_witness(channel, timing)
    if role in ("opcode", "operand"):
        return _code_witness(channel, role, timing)
    return None
