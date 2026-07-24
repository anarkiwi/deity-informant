# Readable transcription

`deity_informant.transcribe` renders a :class:`Kernel` as a readable, SID-semantic
data-flow view. It is the presentation layer on top of the state-machine kernel:
the kernel proves *what* the tune is (byte-exact), the transcription makes it
*legible*.

Module: `deity_informant/transcribe.py` (`transcribe`, `sid_name`).

```python
from deity_informant import record, run_sub, lift_kernel, transcribe
kern = lift_kernel(record(mem, run_sub, play, range(0xD400, 0xD419), frames))
print(transcribe(kern, "my_tune"))
```

## What it shows

- **SID WRITES** — each written register named by SID semantics (`V1.FREQ_LO`,
  `V2.CTRL`, `FILT_CUT_HI`, `MODE_VOL`, …); the distinct value expressions across
  the `K` variants are listed under it (extra lines are data-dependent paths).
- **STATE UPDATE** — each persistent cell and its distinct next-value expressions
  (the song engine's transition function).
- **LET** — shared subexpressions (deep pointer-table indirection reused across
  voices/registers) factored into `t#` bindings so the bodies stay short.
- **TABLES** — constant-data address ranges only.

Rendering is table-content-free by construction: a constant table cell renders as
`T[$addr]` and an indexed read as `T[<index expr>]` — never the byte value. Width
extension (`INT_ZEXT`) is transparent, 16-bit assembly renders `(hi:lo)`, and
arithmetic uses `+ - | & ^ << >>`.

## Example (synthetic `dec_timer`)

```
SID WRITES  (register <- value; extra lines are data-dependent variants):
  V2.FREQ_LO   $D407 <- T[(((S[$1441] + $1) & $3) + $1400)]
                        T[(S[$1441] + $1400)]
STATE UPDATE  (cell <- next value):
  S[$1440] <- $2
              (S[$1440] + $FF)
  S[$1441] <- ((S[$1441] + $1) & $3)
```

A down-counter (`$1440`) gates a 4-entry wavetable read indexed by a mod-4 phase
(`$1441`) — the two SID-write lines are the counter-expired and counting paths.

## Scale

Real commercial players transcribe faithfully and stay byte-exact, but reflect
their true complexity: a full 3-voice routine factors into a large `LET` table
(the three voices run identical code with per-voice constants that value-equality
CSE cannot merge). The `STATE UPDATE` section — the engine's register file — stays
compact and is the most directly analyzable part. Loop re-rolling across voices is
future work (see [kernel.md](kernel.md)).
