# Examples — historical prototype / oracle harness

These scripts are the original prototype and oracle-validation harness that
proved out the lifter + VM against the sidplayfp hardware oracle. They are **not**
part of the shippable package (package discovery is scoped to `deity_informant*`,
so they are excluded from the wheel).

Most require external dependencies that are not shipped:

- **`pysidtracker`** — the parent project's SID plumbing (`registers`, `image`,
  `oracle`, `trace`, `detect`).
- **HVSC tunes** and rendered sidplayfp oracle CSVs — fetched/rendered locally,
  never committed.

| file | purpose |
|---|---|
| `deity_informant_flat_prototype.py` | standalone flat prototype of the lifter + VM |
| `fuzz.py` | differential fuzz vs py65 (legal opcodes) |
| `oracle_match.py` | 60 s byte-exact match vs the sidplayfp oracle |
| `data_swap2.py` | cross-tune data-swap (same driver P-Code, different song data) |
| `defmon_recon.py`, `defmon_sched.py` | IRQ-driven defMON reconnaissance |
| `fetch_fixtures.py` | fetch/render tunes + oracle CSVs |

The maintained, self-contained validation lives in `tests/` and needs none of the
above.
