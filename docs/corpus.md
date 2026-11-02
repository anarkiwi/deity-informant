# HVSC corpus run

`tools/wpcorpus.py ROOT --out DIR` runs every song of an HVSC tree once, for its full length,
and records everything the whole-program lifter's evaluation uses, so the corpus need not be
swept again. Options: `--sample N --seed S` (random tunes), `--jobs J`, `--max-cycles C` (per
init or call, default 2,000,000), `--default-length S` (default 180), `--timeout S` and
`--call-timeout S` (default 0.02 with vagrant-moses' native trace, else 0.5: tens of times the
per-call cost of every stage; wall clock
per song is `timeout + call_timeout * calls`), `--label-calls N` (default 1000; 0: the whole
song).

## Plan

Per file: the md5 of the whole file, the header (kind, version, load/init/play, songs, start
song, flags: clock, SID models, extra SID addresses) and per song its speed bit and length
from `ROOT/DOCUMENTS/Songlengths.md5` (`--default-length` when the md5 or song is missing,
flagged `length_default`). The play rate is the VBI rate of the clock (PAL unless the clock
flag says NTSC only), or with the speed bit set the CIA 1 timer A rate from the latch the
song's init writes on the reference (`rate_src` `cia-init`), else the KERNAL's latch
(`cia-kernal`). `calls = round(length * rate)`; for interrupt-driven songs it is the length in
video frames.

## Per song

PSID with a play routine:

1. Init on the reference (`wholeprog.reference_call`) over the power-on image; init facts
   over the same image; play on the reference for every call from the post-init state (timed,
   `reference_s`), stopping at the first call that does not return (`calls_run`).
2. Play facts (`trace.collect`) over those calls. The song needs the bank map when the init or
   play facts store to $00/$01, or an executed start fetches an opcode, operand or vector from
   a window that is not RAM under sidplay's $01 for that entry (`smclabel.iomap`):
   `bank_evidence`.
3. Flat: lift, `wplift.check` over all calls (`exact`, `mismatch`, `deopts`; `no-return`
   when both stop the same way), lifted `Program.call` and jennings `run_to_rts` timed over
   the same calls (`timing`), scenario cells from the vagrant-moses machine oracle with play
   called as sidplay does (`oracle` `calls`) over the first `--label-calls` calls.
4. Banked: one vagrant-moses machine run (init and every call, sidplay's $01 per phase) gives
   the facts, the call records the bank-aware lift is checked against (`c64.check`) and the
   cells over every call, natively where vagrant-moses has the trace (`c64.TRACE_NATIVE`);
   `configs` lists the bank configurations seen. Speed is the banked reference and the
   lifted program (jennings has no bank map).

RSID, or PSID without play: the machine runs init (with the KERNAL's CIA 1 timer) and then the
tune's own interrupts for the song's frames, scanned by vagrant-moses' native `smc.Scan` (else,
as the reference, a minimal decode hook dropped once the main context is seen busy;
`tools/traceequiv.py --irq` checks they agree); `irq` records the vector path after init
(`kernal` when the KERNAL is banked in, so IRQs go through $0314, else `hardware` via RAM
$FFFE), both vectors, interrupts taken, whether init returned, and the main context's
instructions until then and the frame it first stored outside page 1 or touched I/O
(`first_busy_frame`; `idle` when it never did). A second machine oracle run labels the first
`--label-calls` frames (`oracle` `frames`). These songs are skipped for lifting (`irq-driven`). RSIDs flagged as BASIC tunes are skipped (the machine
has the ROM-less ROM set).

Labelling goes through `label(oracle, ...)`; it uses vagrant-moses' native SMC recorder when
installed (`label_backend` `native`), else the Python classifier.
Every labelled row has `cells` (scenario sids), `cells_first` (the call or frame each was
first seen at), `label_calls`, and `reasons` (`smc.expect.lift_reason` per cell, `lift-exact`
when the lifter can reproduce it).

## Output

- `rows.jsonl`: one row per (path, song), appended as songs finish. Failures carry `error`
  (the exception, or `timeout`).
- `facts.jsonl.zst`: one zstd frame per lifted song with the post-init state as runs that
  differ from the loaded image (`[[addr, hex]]`), its registers and the facts, enough to
  re-lift or re-check without re-tracing.
- `summary.json`, `summary.md`: coverage by kind, bank evidence, `stack_private` and
  `kept_bytes`, gap reasons and top cells by songs, speed ratios and rates, lift and facts
  times (p10/median/p90), first-seen indices and the cells first seen in the last 10% of a
  capped labelling, per-stage cost (`cost_us`: microseconds per call or frame, nanoseconds per
  reference cycle), and the failure list.

A rerun skips the (path, song) keys already in `rows.jsonl` (a torn last line or facts frame
is cut off, and a lifted row whose facts were lost runs again).
