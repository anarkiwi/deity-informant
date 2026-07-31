"""dmoracle — DefMON's song, decomposed into the universal primitive.

A third editor mapped onto `tracker`'s `(transfer, trigger, route)`, sharing
`gtoracle`'s editor-independent builder: every sidTAB column becomes the generic
lane it drives, the delay a clock and the jump a loop. See docs/dm-oracle.md.
"""

from collections import namedtuple

from . import framelog
from . import gtoracle

Sites = namedtuple("Sites", "sig base bands filt acc pitch")

_BAND_STRIDE = 0x31
_DATA_BASE = 0x7DE  # the data tables sit a fixed distance above the signature
_PITCH_PREFIX = 100  # a player variant may differ in the unused tail of the table

# operand offset inside a voice band -> (register offset in the voice, role)
_OPERANDS = {
    1: (2, "pwlo"),
    3: (3, "pwhi"),
    11: (0, "flo"),
    13: (1, "fhi"),
    21: (6, "sr"),
    23: (5, "ad"),
    25: (4, "wave"),
    27: (4, "xor"),
}
# (offset, opcode, register offset): the stores a voice band must carry
_BAND_STORES = (
    (4, 0x8E, 2),
    (7, 0x8D, 3),
    (14, 0x8E, 0),
    (17, 0x8D, 1),
    (28, 0x8E, 6),
    (31, 0x8C, 5),
    (34, 0x8D, 4),
)
_FILT_STORES = ((0x89, 0x17), (0x90, 0x18), (0xB3, 0x16))
_FILT_OPERAND = {0x17: -1, 0x18: -3}  # immediate operand behind each filter store

# sidTAB column -> the generic lane it drives, and how the driver reads that byte
_COLUMNS = (
    ("WGh", ("sidtab", "wave"), lambda b: b | 0x01),
    ("WGl", ("sidtab", "xor"), None),
    ("AD", ("ins", "ad"), None),
    ("SR", ("ins", "sr"), None),
    ("PW", ("pw", "hi"), None),
    ("PW", ("pw", "lo"), lambda b: b & 0xF0),
    ("PS", ("pw", "step"), None),
    ("RE", ("filt", "res"), None),
    ("FV", ("filt", "vol"), lambda b: (b & 0xF0) | 0x0F),
    ("CP", ("filt", "step"), None),
    ("ACID", ("filt", "acid"), lambda b: b & 0xFF),
    ("ACID", ("filt", "acidhi"), lambda b: (b >> 8) & 0xFF),
    ("TR", ("note", "tr"), None),
    ("AF", ("note", "slide"), None),
)
# role -> (lane, the column the driver stages, how it reads it, the refusal name)
_STAGED = {
    "wave": (("sidtab", "wave"), "WGh", lambda b: b, ("ghost", "wave")),
    "xor": (("sidtab", "xor"), "WGl", lambda b: b, ("ghost", "xor")),
    "ad": (("ins", "ad"), "AD", lambda b: b, ("ghost", "adsr")),
    "sr": (("ins", "sr"), "SR", lambda b: b, ("ghost", "adsr")),
    "pwhi": (("pw", "hi"), "PW", lambda b: b, ("sweep", "carry")),
    "pwlo": (("pw", "lo"), "PW", lambda b: b & 0xF0, ("sweep", "pwlo")),
    "re": (("filt", "res"), "RE", lambda b: b, ("res", "routing")),
    "fv": (("filt", "vol"), "FV", lambda b: b & 0xF0, ("mode", "vol")),
}
_EMITS = {"fv": lambda v: v | 0x0F}  # the staged byte is not always the emitted one
_GATE_MASK = 0x01  # the one bit of WGl a ctrl lane's gate images express


def dm_available():
    """Is `pydefmon` importable? The oracle is an optional extra."""
    try:
        import pydefmon  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
    except ImportError:
        return False
    return True


# ---- 1. the driver: where a relocatable replay keeps its song data ----------------
def dm_sites(mem, sig):
    """`Sites` for a mounted DefMON replay, or None if the driver is not the one.

    The signature site fixes the rest; every site is verified against the opcode
    that must be there, so a driver variant is refused rather than mis-read."""
    from pydefmon import NOTE_PITCH_HI, NOTE_PITCH_LO  # pylint: disable=import-outside-toplevel

    bands = [sig + _BAND_STRIDE * v for v in range(3)]
    for v, base in enumerate(bands):
        for off, opcode, reg in _BAND_STORES:
            addr, want = base + off, 0xD400 + 7 * v + reg
            if mem[addr] != opcode or mem[addr + 1] | (mem[addr + 2] << 8) != want:
                return None
    filt = {}
    for off, reg in _FILT_STORES:
        addr = sig + off
        if mem[addr] != 0x8D or mem[addr + 1] | (mem[addr + 2] << 8) != 0xD400 + reg:
            return None
        filt[reg] = addr
    if mem[filt[0x16] - 4] != 0xAD:  # LDA acc / ASL A / STA $D416
        return None
    acc = mem[filt[0x16] - 3] | (mem[filt[0x16] - 2] << 8)
    blob = bytes(mem)
    pitch = (blob.find(NOTE_PITCH_LO[:_PITCH_PREFIX]), blob.find(NOTE_PITCH_HI[:_PITCH_PREFIX]))
    if min(pitch) < 0:
        return None
    return Sites(sig, sig + _DATA_BASE, tuple(bands), filt, acc, pitch)


def _operands(sites):
    """`{address: (voice, register, role)}` for every staged byte the bands emit."""
    out = {}
    for v, base in enumerate(sites.bands):
        for off, (reg, role) in _OPERANDS.items():
            out[base + off] = (v, 7 * v + reg, role)
    for reg, delta in _FILT_OPERAND.items():
        out[sites.filt[reg] + delta] = (None, reg, "re" if reg == 0x17 else "fv")
    return out


# ---- 2. the tables: one sidTAB column, one generic lane ---------------------------
def _lane(rows, name, conv):
    """One sidTAB column as a 256-byte lane, indexed by row."""
    return tuple(
        (
            0
            if getattr(r, name) is None
            else (conv(getattr(r, name)) if conv else getattr(r, name)) & 0xFF
        )
        for r in rows
    )


def dm_tables(song, sites, mem):
    """The song's byte tables under the generic names, plus the decoded rows.

    Nothing here is "a sidTAB row": every entry is a lane of one generic kind —
    waveform, AD, SR, pulse, filter, note — read at a row index."""
    rows = [song.sidtab_row(y) for y in range(256)]
    tables = {key: _lane(rows, col, conv) for col, key, conv in _COLUMNS}
    lo, hi = sites.pitch
    tables[("pitch", "lo")] = tuple(mem[lo : lo + 128])
    tables[("pitch", "hi")] = tuple(mem[hi : hi + 128])
    tables[("clock", "dl")] = tuple(song.sidtab_dl)
    tables[("clock", "jp")] = tuple(  # the row a cascade lands on when it steps to y
        y if song.jp_target(y) is None else song.jp_target(y) for y in range(256)
    )
    for key, arr in (("v1", song.arranger_v1), ("v2", song.arranger_v2), ("v3", song.arranger_v3)):
        tables[("arr", key)] = tuple(arr)
    patt = [song.pattern_events(n) for n in range(128)]
    for key in ("flag", "slot_a", "slot_b", "note"):
        tables[("patt", key)] = tuple(getattr(ev, key) for pat in patt for ev in pat)
    return tables, rows


# ---- 3. the probe: rows, notes and staged bytes read off the running replay -------
def _dm_probe_class():
    """The instrumented DefMON replay, built lazily off the optional import.

    Provenance comes off the address bus: a per-row pointer read names the sidTAB
    row, a note-table read names the note, a band operand write names the voice
    and register. Reads are returned unchanged, so nothing is disturbed."""
    from pydefmon import DefmonPlayer  # pylint: disable=import-outside-toplevel

    class _Probe(DefmonPlayer):
        """A DefMON replay naming the row, note and voice behind every register.

        An observer returning ``None`` leaves the byte alone, so every callback
        below ends in one: it observes and does not substitute."""

        # pylint: disable=useless-return
        sites = None
        ops = {}
        ev = ()

        def _init(self, subtune):
            self.ev = []
            super()._init(subtune)
            base, lo = self.sites.base, self.sites.pitch[0]
            self._obs.subscribe_to_read(range(base, base + 0x100), self._rd_row)
            self._obs.subscribe_to_read(range(lo, lo + 128), self._rd_note)
            self._obs.subscribe_to_write(sorted(self.ops), self._wr_op)
            self._obs.subscribe_to_write(range(0xD400, 0xD419), self._wr_sid)

        def _rd_row(self, addr):
            self.ev.append(("row", addr - self.sites.base, 0))
            return None

        def _rd_note(self, addr):
            self.ev.append(("note", addr - self.sites.pitch[0], 0))
            return None

        def _wr_op(self, addr, val):
            self.ev.append(("op", addr, val))
            return None

        def _wr_sid(self, addr, val):
            self.ev.append(("sid", addr - 0xD400, val))
            return None

    return _Probe


def _stage(state, tables, rows, who, val, cur):
    """Record what one operand write stages: a lane at a row, or an unnamed byte.

    The row is the one the cascade just fetched where its column carries the byte,
    else the row the voice still holds — the held reading of docs/tracker.md §5."""
    voice, reg, role = who
    if role in ("flo", "fhi"):
        _stage_note(state, tables, voice, reg, role, val, cur)
        return
    if role == "xor":
        state[("xor", voice)] = val
        return
    lane, col, read, miss = _STAGED[role]
    key = ("row", voice if voice is not None else "filt")
    for cand in (cur[0], state.get(key)):
        got = None if cand is None else getattr(rows[cand], col)
        if got is not None and read(got) == val:
            state[key] = cand
            state[reg] = (lane, cand, _EMITS.get(role, _same)(val))
            return
    state[reg] = (miss, None, val)


def _same(val):
    """The staged byte is the emitted byte."""
    return val


def _stage_note(state, tables, voice, reg, role, val, cur):
    """freq_lo/freq_hi: the note table at the note the player last indexed."""
    lane = ("pitch", "lo" if role == "flo" else "hi")
    key = ("note", voice)
    for cand in (cur[1], state.get(key)):
        if cand is not None and 0 <= cand < len(tables[lane]) and tables[lane][cand] == val:
            state[key] = cand
            state[reg] = (lane, cand, val)
            return
    state[reg] = (("slide", "detune"), None, val)


def _ctrl_cell(state, tables, voice, val):
    """The ctrl emit: the waveform lane read through the gate the XOR mask carries.

    DefMON emits ``WGh ^ WGl``. A mask touching only the gate bit is one of the
    lane's three gate images; any other mask is a second generator on the plane."""
    lane, row, _v = state.get(7 * voice + 4, (None, None, 0))
    if lane != ("sidtab", "wave") or row is None:
        return gtoracle.Cell("raw", ("ghost", "ctrl"), 0, val, 0)
    if state.get(("xor", voice), 0) & ~_GATE_MASK:
        return gtoracle.Cell("raw", ("xor", "mask"), 0, val, 0)
    table = gtoracle._ctrl_table(tables[lane])  # pylint: disable=protected-access
    n = len(tables[lane])
    for k in range(3):
        if table[row + k * n] == val:
            return gtoracle.Cell("ctrl", lane, row + k * n, val, 0)
    return gtoracle.Cell("raw", ("xor", "mask"), 0, val, 0)


def _cell(state, tables, rows, reg, val):
    """The `Cell` one SID write is: the lane and row its staged byte came from."""
    if reg <= 0x14 and reg % 7 == 4:
        return _ctrl_cell(state, tables, reg // 7, val)
    if reg == 0x16:
        return gtoracle.Cell("raw", ("cutoff", "slide"), 0, val, 0)
    lane, row, emitted = state.get(reg, (None, None, None))
    if lane is None:
        return gtoracle.Cell("raw", ("ghost", "held"), 0, val, 0)
    if row is None or emitted != val:
        return gtoracle.Cell("raw", lane, 0, val, 0)
    if lane == ("pw", "lo") and rows[row].PS is not None:
        return gtoracle.Cell("ramp", ("pw", "step"), row, val, tables[("pw", "step")][row])
    return gtoracle.Cell("select", lane, row, val, 0)


def _instr(state, voice):
    """The voice's instrument: the sidTAB row its AD lane last read."""
    lane, row, _v = state.get(7 * voice + 5, (None, None, 0))
    return -1 if lane != ("ins", "ad") or row is None else row


def _dm_structure(song, tables, fetched):
    """Counts of the arrangement the composer wrote, in the same generic names."""
    steps = [
        y
        for y in range(256)
        if tables[("arr", "v1")][y] or tables[("arr", "v2")][y] or tables[("arr", "v3")][y]
    ]
    used = sorted({r for fr in fetched for r in fr})
    return {
        "patterns": len(
            {b for k in ("v1", "v2", "v3") for b in tables[("arr", k)] if 0 < b < 0x80}
        ),
        "pattern_rows": sum(
            1 for n in range(128) for ev in song.pattern_events(n) if ev.flag or ev.note
        ),
        "orderlist_entries": sum(
            1 for k in ("v1", "v2", "v3") for y in steps if tables[("arr", k)][y]
        ),
        "instruments": len({y for y in range(256) if song.sidtab_row(y).values()}),
        "song_steps": len(steps),
        "rows_walked": len(used),
        "loops": sum(1 for y in range(256) if song.jp_target(y) is not None),
        "delays": len({tables[("clock", "dl")][y] for y in used}),
    }


def dm_native(raw, nframes, subtune=None):
    """``(Native, records, per-frame fetched rows)`` for a replay, or None.

    The schedule is the driver's, exactly as `gtoracle` takes GoatTracker's;
    every value is the song's, named by the lane and row the machine read."""
    from pydefmon import DefmonSong  # pylint: disable=import-outside-toplevel
    from pydefmon._sid_format import find_signature  # pylint: disable=import-outside-toplevel
    from pysidtracker import SidImage  # pylint: disable=import-outside-toplevel

    image = SidImage.from_bytes(raw)
    sig = find_signature(image.mem)
    sites = dm_sites(image.mem, sig) if sig >= 0 else None
    if sites is None:
        return None
    song = DefmonSong.from_sid_bytes(raw)
    tables, rows = dm_tables(song, sites, image.mem)
    probe = _dm_probe_class()
    probe.sites, probe.ops = sites, _operands(sites)
    player = probe(raw) if subtune is None else probe(raw, subtune=subtune)
    writes, notes, instrs, onsets, shape, frames, fetched = [], [], [], [], [], [], []
    state, prev = {}, (None, None, None)
    for _f in range(nframes):
        player.ev = []
        player.play_frame()
        cells, got, walked, cur = {}, [], [], (None, None)
        for ev in player.ev:
            if ev[0] == "row":
                walked.append(ev[1])
                cur = (ev[1], cur[1])
            elif ev[0] == "note":
                cur = (cur[0], ev[1])
            elif ev[0] == "op":
                _stage(state, tables, rows, player.ops[ev[1]], ev[2], cur)
            else:
                got.append((ev[1], ev[2]))
                cells[ev[1]] = _cell(state, tables, rows, ev[1], ev[2])
        frames.append(got)
        shape.append(tuple(r for r, _v in got))
        writes.append(cells)
        fetched.append(tuple(walked))
        now = tuple(state.get(("note", v)) for v in range(3))
        onsets.append(tuple(int(a is not None and a != b) for a, b in zip(now, prev)))
        notes.append(tuple(-1 if n is None else n for n in now))
        instrs.append(tuple(_instr(state, v) for v in range(3)))
        prev = now
    native = gtoracle.Native(
        "defmon", tables, writes, shape, notes, instrs, onsets, _dm_structure(song, tables, fetched)
    )
    return native, framelog.canonical(frames), fetched


def dm_decompile(path, nframes, subtune=None):
    """``(Native, records, fetched rows)`` for a cached DefMON ``.sid``, or None."""
    with open(path, "rb") as fh:
        return dm_native(fh.read(), nframes, subtune)
