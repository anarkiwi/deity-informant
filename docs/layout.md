# Memory layout recovery

`deity_informant.layout.build(prog, facts, image, volatile)` recovers a program's data layout
(multi-byte words, pointers, address tables, record arrays and parallel arrays) from a lifted
[`Program`](wholeprog.md#python) and the effective-address evidence of a run. The map is for
ranking and reporting (and the Ghidra export and symbol scoring built on it); the lifter never
reads it.

```bash
python tools/layout.py SIDFILE [--song N] [--calls N] [--max-cycles C] [--json OUT]
```

runs a PSID's init on the reference (`wpcorpus.flat_init`), `N` play calls (default 1000)
through `trace.collect(..., ea=True)` with progress bars, lifts the play routine flat with
the device window (and extra SIDs) volatile (`wpcorpus.volatile_of`), and prints one line per
region, the conflicts and the region counts by kind (`*` and `(io)`: in a volatile range).
`tools/layout.song_layout(data, song, calls)` returns the map.

## Effective-address evidence

`trace.collect(image, driver, ea=True)` observes every data access of the second pass and
folds it per executed `(pc, opcode)` into fixed-size abstractions, so the evidence does not
grow with the run (accesses are buffered up to `trace.EA.LIMIT` and folded in numpy between
calls). An opcode's mode is jennings' (`jennings.opcodes.OPCODES`); the core makes exactly
the mode's data accesses (two pointer bytes before an indirect access, a load and a store for
a read-modify-write), which `tests/test_layout.py` checks for every opcode. `Facts.ea` is
`{pc: {opcode: record}}`, kept by `Facts.to_dict`/`from_dict` (absent when empty):

| Field | Meaning |
|---|---|
| `mode` | `zp`, `abs`, `zpx`, `zpy`, `absx`, `absy`, `indx` or `indy` |
| `n` | executions |
| `addr` | the data addresses |
| `index` | the index register's values (`X` for `zpx`/`absx`/`indx`, else `Y`) |
| `base` | the operand base (`address - index`, in page 0 for `zp` modes), or the zero-page pointer's address for `(zp),Y` and the operand for `(zp,X)` |
| `ptr` | the pointer values (`(zp),Y`: `address - Y`; `(zp,X)`: the address) |

Each interval is `deity_informant.strided.Strided` (`Strided.from_list`, or
`trace.EA.strided(record, field)`) serialized as `[lo, hi, stride, bits]`: the least strided
interval `{lo + k * stride}` holding every value seen (Balakrishnan and Reps' value-set
analysis; stride 0 for one value) and `bits`, a hexadecimal bit set of the lattice points seen
(bit `k` for `lo + k * stride`), `null` once the lattice exceeds `strided.CAP` points. An
8-bit index is therefore always exact.

## Constraints

Regions are merged by union-find with offsets (`layout.UnionFind`), in the style of Aggregate
Structure Identification (Ramalingam, Field and Tip, POPL 1999) and DIVINE (Balakrishnan and
Reps, VMCAI 2007). The constraints:

1. **Carry chains** (`Program.chains`, [Carry chains](wholeprog.md#carry-chains)). Operand or
   result bytes at adjacent absolute addresses are one word. Bytes indexed by one register or
   one computed value (`["v", base, op]`) at adjacent bases make a field of that width; at
   other bases they are parallel byte arrays whose `i`-th elements form one word (split lo/hi
   tables).
2. **Pointers and address tables** (`layout.pointer_uses`, over the lifted ops with
   hash-consed expressions). A pair `(p, p+1)` whose bytes (loaded, or the values the
   function stored there) build an access's or an indirect branch's address as
   `OR(LEFT(ZEXT(hi), 8), ZEXT(lo))`, or a 2-byte value loaded from or stored at `p`, is a
   pointer, as is every `(zp),Y` pointer of the evidence. A pointer stored, in one block, from
   `LOAD lo+I` and `LOAD hi+I` with the same index `I` makes `(lo, hi)` an address table
   (interleaved when `hi = lo + 1`); its entries at the index values seen are the pointers'
   `targets`, read from the image unless the run wrote an entry byte (`written`). Such pairs
   that feed no pointer are words of parallel arrays.
3. **Strided accesses** (the evidence's indexed accesses). Accesses sharing an index sequence
   (register and `Strided`, memory and devices apart) with stride `s` and `n` lattice points
   join one aggregate when their bases are closer than `n * s`: a record array of stride `s`
   with fields at the bases' offsets modulo `s` (array of structures), or a plain array when
   there is one field. Aggregates farther apart are parallel arrays of that sequence
   (structure of arrays). Aggregates whose bytes overlap merge when one stride divides the
   other (an access seen at only some elements has a multiple of the element stride; the
   aggregate's stride is the gcd); other overlaps are conflicts.

Atoms sharing a byte are one region; a field or atom contained in another is absorbed, and a
partial overlap is reported and merged to the hull.

## Map

`build` returns `{"regions": [...], "conflicts": [...]}` (JSON); `tools/layout.py` adds
`tune` (`init`, `play`, `songs`, `song`, `calls`) and `init_halt`. Addresses and offsets are
integers. A region:

| Field | Kinds | Meaning |
|---|---|---|
| `kind` | all | `word`, `pointer`, `address_table`, `record`, `parallel_array` or `array` |
| `base` | all | lowest base address (a parallel array's: its first member's) |
| `size` | word, pointer, array, record | bytes spanned (from `base`, or the `extent`) |
| `stride` | arrays, tables | element stride |
| `count` | arrays, tables | elements spanned |
| `extent` | arrays, tables | `[lo, hi]` addresses accessed, inclusive |
| `fields` | all | `[{"offset", "width"}]`: within an element (record, array, interleaved table), the members' offsets from `base` (parallel array, split table), or the atoms (word, pointer) |
| `words` | parallel array, table | offset tuples (least significant first) of bytes forming one value |
| `index` | arrays, tables | index registers seen (`x`, `y`, `xy`); a pointer's: its `Y` index as a `Strided` list |
| `targets` | pointer, table | pointer values seen, or the table's entries; `[lo, hi]` when too many |
| `pointers` | table | zero-page pointers the table feeds |
| `written` | table | the run wrote an entry byte (no `targets`) |
| `io` | any | the region starts in a volatile range |
| `evidence` | all | `chain@PC` (block of a chain), `ops@PC` (block of a pointer use or table feed), `ea@PC` (instruction) |

A parallel array is listed with its member arrays and records, which follow it. Conflicts:
`{"kind": "atoms"|"fields", "base", "spans": [[offset, width], ...], "evidence"}` for
partially overlapping atoms or fields, and `{"kind": "strides", "regions": [i, j],
"strides": [s, t]}` for overlapping array or record regions (indices into `regions`) whose
strides do not divide.
