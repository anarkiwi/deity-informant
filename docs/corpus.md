# HVSC corpus run

`tools/wpcorpus.py ROOT --out DIR` runs every song of an HVSC tree once, for its full length,
and records everything the whole-program lifter's evaluation uses, so the corpus need not be
swept again. Options: `--sample N --seed S` (random tunes), `--jobs J`, `--max-cycles C` (per
init or call, default 2,000,000), `--default-length S` (default 180), `--timeout S` and
`--call-timeout S` (default 0.02 with vagrant-moses' native trace, else 0.5: tens of times the
per-call cost of every stage; wall clock
per song is `timeout + call_timeout * calls`), `--label-calls N` (default 1000; 0: the whole
song), `--backend jit|interp` (the lifted engine, default `jit`; rows record `backend` and the
native compile time `compile_ms` apart from `lift_ms` and the timed run).

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

1. Init on the reference (`wholeprog.reference_call`, `init_halt`, `init_cycles`) and init
   facts (`trace.collect`) over the power-on image.
2. One vagrant-moses machine run (init and every call, sidplay's $01 per phase; natively
   where vagrant-moses has the trace, `c64.TRACE_NATIVE`) gives the play facts, the call
   records the lift is checked against and the scenario cells (`oracle` `calls`) over every
   call it ran (`calls_run`, to the first that does not return); a `--label-calls` smaller
   than that labels the first that many calls in a run of its own (flat songs only). The song
   needs the bank map when the init or play facts store to $00/$01, or an executed start
   fetches an opcode, operand or vector from a window that is not RAM under sidplay's $01 for
   that entry (`smclabel.iomap`): `bank_evidence`. A machine init that does not return skips
   the song (`init <halt>`).
3. Lift from the machine's post-init state, flat or (`banked`) through the bank map with the
   facts' bank configurations (`configs`), and `c64.check` over the records, each call
   returning to the machine's sentinel (`exact`; `outcome` `exact`, `mismatch` (`mismatch`)
   or an agreement in `stop`: `halted` at the run's last record, `interrupt` in a call the
   machine entered an interrupt in; `deopts`). A flat lift that differs replays the machine's
   device reads through the flat model (`wpcorpus.flat_evidence`) up to that call: a data
   load or store where the CPU under sidplay's $01 sees something else (`data-in-rom`,
   `store-in-ram`, ...) is bank evidence, and the song lifts banked; without one the flat
   model ran as the machine did and the mismatch stands. Timed from that state
   (`timing`): the reference over the calls run (`reference_s`, with the bank map when
   banked), then the lifted program and, for flat songs, jennings `run_to_rts` (jennings has
   no bank map) over the calls the reference ran.

RSID, or PSID without play: the machine starts the song as sidplayfp does (below), runs init and
then the tune's own interrupts for the song's frames, scanned by vagrant-moses' native `smc.Scan` (else,
as the reference, a minimal decode hook dropped once the main context is seen busy;
`tools/traceequiv.py --irq` checks they agree); `irq` records the vector path after init
(`kernal` when the KERNAL is banked in, so IRQs go through $0314, else `hardware` via RAM
$FFFE), both vectors, interrupts taken, whether init returned, and the main context's
instructions until then and the frame it first stored outside page 1 or touched I/O
(`first_busy_frame`; `idle` when it never did). The scan run's recorder labels the frames
(`oracle` `frames`); a smaller `--label-calls` labels the first that many in a second run. An idle-main song (`irq.idle`, not jammed, with interrupts) is then lifted and checked frame by
frame (below); the others are skipped with their reason: `async main` (the main context does real
work beside the interrupts), `init jam`, `no interrupts`, or `interrupt outside a frame: brk`.

### Interrupt frames

`deity_informant.frames` (`wpcorpus.frame_song`) treats one interrupt, from its entry to its
`RTI`, as a call. A second machine run (`frames.FrameHost`, on vagrant-moses' native `smc.Frames`) detects
every IRQ and NMI entry at the vector target the CPU took; at an entry the machine points the pushed return address at the machine's sentinel and opens a call record
(`c64.CallStream`, facts and RAM changes as for a PSID's calls), and the handler's `RTI` landing
on the sentinel closes it and resumes the interrupted pc. The record's entry state is the
registers and status the hardware pushed (`frames.ENTRY`). The lift is always banked, through the
song's ROMs (a BASIC song's patched BASIC included), one entry per entry address;
`Program.call(..., irq=True)` pushes the frame as the hardware does (PC, status with B clear, I
set) and the entry returns by `RTI` to the sentinel. A frame cut by the end of the run is dropped;
one that jams or exceeds `--max-cycles` ends it (`halted`). Frame rows have `mode` `banked`, no
`jennings_s`, and a facts record (`state` over the song's image, `entries`, `facts`).

An interrupt entered inside a frame (an NMI digi player inside an IRQ frame; one taken just
after the frame's `RTI` opens the next frame instead) is part of that frame's record: its device
accesses and RAM changes join the record in the order the machine made them, and the entry is an
event (`c64.EVENT`) of that record: instructions retired, device accesses made before it, the
entry and the registers before the interrupt. `Program.check(..., starts=, nested=)` takes
them for every frame of a record chunk in one native loop; `wholeprog.reference_call(...,
nested=)` takes the same events on the reference interpreter.

The machine takes an interrupt at an instruction boundary, and vagrant-moses' native
`smc.Frames` records it: each event has the instructions the interrupted context had retired
(`icount`), the device accesses logged so far (`pos`) and the registers. `Program.call`
runs a frame with events on the reference interpreter instruction by instruction and takes
each interrupt at its boundary, its handler on the lifted program (`docs/wholeprog.md`), so a
difference is a `mismatch` (`boundary` when the interpreter's registers or device access count
at an interrupt are not the machine's). Frames without interrupts run on the lifted code;
frame rows have `timing.interrupted`, the frames of the timed window that ran on the
reference. In PSID calls interrupts are recorded the same way by the native call queue.

A frame whose code reaches the call's return address with the interrupt's frame still on the stack
(a handler that resets SP and returns into the code the machine resumes at) ends there, as the
machine's sentinel trap ends it, whatever SP is.

Memory does not grow with the frames: the records, the entry states and the nested interrupts of
each window go to `c64.Spill`s (`FrameHost.chunks`), and the check and the timed window read them
a window at a time; the machine's own event list is cleared after each window.

The recording is native, so an NMI digi player's hundred frames per video frame cost no Python.
The run has a deterministic budget (`smc.Budget`): a frame may run `--max-cycles` cycles and
half as many instructions, the run that many per video frame; the row's `budget` is the limit
that stopped it (`none`, `unit_cycles`, `unit_insns`, `song_cycles`, `song_insns`, `jam`), a
song limit giving `halted` with stop `budget` when the lift is exact.

### Boot environment

`wpcorpus.irq_boot` builds the start (`c64.boot`) from HVSC `SID_file_format.txt` ("The SID
file environment") and libsidplayfp (`psiddrv::install`, `psiddrv.a65`,
`SidTuneBase::placeSidTuneInC64mem`). RSIDs run on the real ROMs (`c64.real_roms`), PSIDs on
the ROM-less set.

- RAM: $0000-$03FE cleared; for an RSID, libsidplayfp's booted-KERNAL image (`poweron.bin`).
  $02A6 is the video flag (1 PAL, 0 NTSC; for every PSID image too, `smclabel.sidfile`).
  $0314 is the KERNAL's IRQ exit $EA7E (the code of the driver's `irqret`) except under BASIC
  ($EA31); a PSID's BRK/NMI vectors go to the ROM-less KERNAL's JAM at $FCE2 (the driver's
  `exception`). $2D/$2F/$31 and $AC/$AE cover the load range, then the data loads.
- I/O, each access in the cycle the driver's cold start makes it (`c64.driver_io`, made from a
  trap at the CPU's first fetch after a reset to that cycle): interrupts off and acknowledged,
  $D418=$0F, CIA 1 timer A latched at $4025 (PAL) / $4295 (NTSC), raster line $137 with
  $D011=$9B for an RSID (line 0 for a PSID), then for an RSID or a CIA-speed PSID timer A
  started with its interrupt, else the raster interrupt enabled. Init's first opcode is fetched
  in the cycle sidplayfp's is (147 cycles after the reset on PAL, 145 on NTSC).
- CPU: $01=$37 for an RSID (a PSID: `iomap` of init), the song (0-based) in A; an RSID runs init
  with I clear, a PSID with I set (`sei` in place of the driver's `plp`). Init returns into code
  before the sentinel that takes the driver's path after `jsr init` cycle for cycle (`lda`,
  `beq idle`; a PSID's `cli` too), so interrupts land on the same idle-loop phase.
- BASIC flag (`basic`): the BASIC ROM's statement loop (NEWSTT, $A7AE) jumps to a hook at $BF53
  that stores the song number (0-based) in $030C before continuing the interpreter at $A7B1;
  init enters that hook, which runs the program from $0801 as RUN does.

Where sidplayfp differs from real hardware or the spec it is followed: the SID volume write, the
driver's IRQ exit, and a VBI PSID's CIA timer left stopped. Not reproduced: the driver's own
bytes in RAM, its STOP vector ($0328) and NEWSTT trap for an RSID without the BASIC flag.
`tools/sidcmp.py` compares the SID register changes of interrupt-driven songs with sidplayfp
(the `anarkiwi/sidtrace` image, with the same ROMs).

Labelling goes through `label(oracle, ...)`; it uses vagrant-moses' native SMC recorder when
installed (`label_backend` `native`), else the Python classifier.
Every labelled row has `cells` (scenario sids), `cells_first` (the call or frame each was
first seen at), `label_calls`, and `reasons` (`smc.expect.lift_reason` per cell, `lift-exact`
when the lifter can reproduce it).

## Output

- `rows.jsonl`: one row per (path, song), appended as songs finish, `song_ms` its wall time.
  Failures carry `error` (the exception, or `timeout`).
- `facts.jsonl.zst`: one zstd frame per lifted song with the post-init state as runs that
  differ from the loaded image (`[[addr, hex]]`), its registers and the facts, enough to
  re-lift or re-check without re-tracing.
- `summary.json`, `summary.md`: coverage by kind, bank evidence, `stack_private` and
  `kept_bytes`, gap reasons and top cells by songs, speed ratios and rates, lift and facts
  times (p10/median/p90), first-seen indices and the cells first seen in the last 10% of a
  capped labelling, per-stage cost (`cost_us`: microseconds per call or frame, nanoseconds per
  reference cycle), and the failure list.

A rerun skips the (path, song) keys already in `rows.jsonl` (a torn last line or facts frame
is cut off, and a lifted row whose facts were lost runs again). `--redo REASON` (repeatable) drops
the rows whose `skip` is `REASON` from `rows.jsonl` first, so they run again and are replaced
rather than duplicated (e.g. `--redo "needs the C64 BASIC ROM"` for rows of earlier runs).

## Tools

`tools/framediff.py ROOT PATH [--song N --frames N --raw --bisect]` records an interrupt-driven song's frames and shows where the first frame the lift differs in differs (entry state, interrupts, device logs of lift, reference and machine, memory); `--bisect` runs the reference interpreter alone over the recorded frames and shows the first whose memory (page 1 included) differs from the machine's.
`tools/wpsong.py ROOT PATH [--song N --calls N --label-calls N --out ROW.json]` runs one song
through the pipeline in-process (for `py-spy record`, whose raw output `tools/profsum.py
PROFILE --within FRAME --match FRAME...` summarises). `tools/corpussum.py ROWS [--paths FILE] [--diff OTHER]` prints a rows file's counts by kind, skip, mode and outcome, the non-exact songs, speedup and cost percentiles, stats coverage and the interrupt breakdown (`--diff`: outcome moves to a second file). `tools/corpusequiv.py BEFORE AFTER` lists
the songs whose rows (bar `*_ms` and `timing.*_s`) or facts differ between two runs and the
`song_ms` speedup per mode. `tools/factsequiv.py ROOT BEFORE AFTER [--list]` lifts each flat
song of both runs from every pairing of their post-init states and facts, attributing a change
in the lift statistics to the state or the facts.

## Decimal mode

`python tools/decimal_scan.py ROOT CORPUS_DIR` lists the songs of a corpus run that execute
`ADC`, `SBC`, `ARR`, `RRA` or `ISC` with the decimal flag set. A song's facts record names the
instruction starts it executed and the RAM they ran from; only a song that executes `SED`, `PLP`
or `RTI` can have the flag set, and a PSID song with a play routine that does is run on the native
core call by call with a trace of every instruction and its flags (a banked one on flat RAM). The
records go to `--out`, the paths of the songs that ran decimal arithmetic to `--paths`, which
`tools/wpcorpus.py --paths` re-runs.
