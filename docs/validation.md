# Layout validation tools

External references for validating recovered memory layouts: player identity (SIDId), address
bytes found by tracing (sidreloc), and layouts known from source (GoatTracker 2's player).

## Image

`docker/vtools.Dockerfile` builds `di-vtools` with one cached stage per tool; every source is
fetched at build time and verified (git commit, sha256), never committed:

| Tool | Source | Pin |
| --- | --- | --- |
| `sidid`, `sidid.cfg` | Cadaver's SIDId, github.com/cadaver/sidid | commit `SIDID_COMMIT` |
| `sidreloc` | Linus Akesson's sidreloc 1.0, linusakesson.net/software/sidreloc | `SIDRELOC_SHA256` |
| `gt2reloc`, `examples/*.sng` | GoatTracker 2.77, sourceforge.net/projects/goattracker2 | `GT2_SHA256` |

```bash
python tools/vtools.py build     # docker build -f docker/vtools.Dockerfile -t di-vtools docker/vtools
python tools/vtools.py check     # exit 0 when the image exists
```

Containers run as the calling user. Bind mounts must come from a path the Docker socket proxy
allows (`/scratch`); the tools mount the inputs' common parent read-only and write into `out/`
(gitignored) of the worktree.

## SIDId

`python tools/sidid.py PATH... [--json OUT]` prints `path<TAB>players` for every `.sid` named or
found under a directory; `sidid.identify(paths)` returns `{path: [players]}`. All matching
signatures are listed (`sidid -m`), SIDId's own pick first; versioned sub-signatures keep their
`sidid.cfg` names (`(JCH_NewPlayer_V17)`), and an unidentified file has no names. Tunes of one
player should yield one layout modulo relocation.

## sidreloc

`python tools/sidreloc.py TUNE.sid... [--pages N] [--zp LO-HI] [--frames F] [--out DIR]`
relocates each PSID by `N` whole pages (default 1; zero page kept unless `--zp`) and writes
`DIR/<name>.reloc.json` (default `out/sidreloc`):

| Field | Meaning |
| --- | --- |
| `load`, `size`, `pages` | the tune's load address and length, the page offset |
| `roles` | addresses by sidreloc's program map: `hi` (address high byte, `R`), `zp` (zero-page address, `Z`), `static` (`=`), `undetermined` (`?`), `unused` (`.`) |
| `zp` | `{old: new}` zero-page addresses |
| `changed` | `{address: [old, new]}` bytes that differ in the relocated image |
| `disagree` | addresses where `changed` and `roles` disagree (a `hi` byte not moved by the offset, a `zp` byte not moved as `zp` says, any other change) |
| `error`, `warnings` | the exit status (`ok`, `no solution`, `verification failed`, ...) and its warning bits |

Whole-page relocation changes only high bytes; sidreloc does not report which low byte pairs with
a high byte.

## Ground truth from source

`python tools/gtruth.py [SNG...] [--variant NAME=OPTS] [--out DIR]` packs each song (default
GoatTracker's bundled examples) with `gt2reloc` per variant (default `base`, and `w2000`: player
at `$2000`, zero page at `$80`). `gt2reloc` is patched (`docker/vtools/gt2-listing.patch`) to
write its assembler's input (`GT2_SOURCE`) and a JSON listing (`GT2_LISTING`): one line per
assembled atom (`off` from the load address, `type`, `op` and `arg` or `exprs`/`res`, `len`) with
expressions as trees (`["%", "mt_patt3", 256]`), then one line per symbol (`sym`, `value`,
`label`: defined as a label rather than by `=`). `DIR/<variant>/` (default `out/gtruth`) holds
`<song>.sid`, `.s`, `.jsonl` and `<song>.truth.json`:

| Field | Meaning |
| --- | --- |
| `load`, `size`, `sid` | load address, image length, the `.sid` file |
| `symbols` | `{name: {value, label}}` |
| `regions` | data regions (below), in address order, then `parallel` groups |
| `refs` | address references: code operands (`in` `code`; `mode` from the opcode, `mn`) and data elements (`in` `data`), each with `at`, `size`, `role` (`addr`, `lo`, `hi`), `target`, `sym` (the label it names, else an equate) and `offset` (`target` minus `sym`) |
| `mismatches` | addresses where the listing's bytes differ from the image (empty) |

Regions are the runs of data atoms split at label addresses; unlabelled bytes continue the
region before them, and a run that starts unlabelled is a region without labels:

| Kind | Declaration | Fields |
| --- | --- | --- |
| `bytes`, `words` | constant `.BYTE` / `.WORD` (or `.RES`) | `count` |
| `pointers` | `.WORD label`, or `X % 256, X / 256` pairs | `targets` |
| `addr_table` | a `X % 256` table and a `X / 256` table of equal targets | `hi`, `hi_labels`, `targets` |
| `addr_lo`, `addr_hi` | an unpaired half (a jump table of low bytes) | `targets` |
| `records` | one-byte labels `f1..fk` at consecutive addresses, then rows of `k` values | `stride`, `count`, `fields` (`labels`, `offset`) |
| `parallel` | adjacent `bytes` regions of one length (more than one element, none a pointer target) | `labels`, `addrs`, `count` |

Every region has `kind`, `labels`, `addr`, `size`, `count`; a region some table or pointer
points at has `target_of` (the pointing regions' addresses). Code indexes arrays from bases that
need not be their labels (`lda mt_insad-1,y`): those are the `refs` with a nonzero `offset`.

## Scoring layout maps

`tools/layoutscore.py` runs `tools/layout.py` (`--calls N`, default 1000, `--jobs J` processes,
maps cached by file md5 under `out/layoutscore`) and validates the maps three ways:

```bash
python tools/vtools.py build && python tools/gtruth.py           # out/gtruth fixtures
python tools/sidid.py "$HVSC" --json out/hvsc.sidid.json         # player groups
python tools/layoutscore.py truth [--json OUT]                   # vs ground truth
python tools/layoutscore.py consistency [--sidid out/hvsc.sidid.json] [--players P...]
python tools/layoutscore.py sidreloc [--sidid out/hvsc.sidid.json]
```

### Ground truth

Each fixture's truth record and map become items per kind; a predicted item is a true
positive when it matches some truth item, a truth item is recalled when some prediction
matches it. Every match needs overlapping spans (`extent`, else `base` and `size`; the truth's
`addr` and `size`):

| Kind | Truth | Prediction | Also required |
| --- | --- | --- | --- |
| `words` | `words` regions | `word` | |
| `pointers` | `pointers` regions, and `p, p+1` for each `(zp,X)`, `(zp),Y` or `JMP (a)` operand `p` in code | `pointer`, interleaved `address_table` | |
| `tables` | `addr_table` (low half span) | split `address_table` (first to last entry's low byte) | operand bases at one offset from both labels: `lo - addr == hi - hi_addr` |
| `records` | `records` | `record` | equal stride; field offsets (mod stride) among the truth's |
| `arrays` | every other region of more than one element (each `addr_table` half; stride 1, 2 for `words`/`pointers`, a record's stride), not pointed at | `array` | stride a multiple of the truth's, at a truth field offset |
| `parallel` | `parallel` | `parallel_array` | one shift puts two member bases on truth member addresses |
| `targets` | regions with `target_of` | `targets` values of pointers and tables | the same address |
| `bytes_lo`, `bytes_hi` | the `lo`/`hi` bytes of data `refs` (both bytes of a 2-byte `addr`) | `layout.address_bytes` in the image | the same address |

Predictions are judged only in zero page or the image (the truth declares nothing else) and
never in device ranges. `reached` counts the truth items with a byte in the map's `accessed`
runs, and `recall_reached` is recall over those: a run that never touches a table cannot
recover it.

### Consistency

Pairs of tunes: each fixture song's `base` with its `w2000` variant, and with `--sidid`
consecutive tunes (in path order) of each SIDId group whose name starts with one of
`--players` (default `GoatTracker_V2`, `JCH_NewPlayer`), grouped by the most specific name
(versioned sub-signature). The two tunes' instructions (executed `starts` and the regions'
evidence addresses) are aligned by opcode sequence in address order
(`difflib.SequenceMatcher`), which is relocation-invariant. A region corresponds when the
other map has a region of its kind with evidence at an aligned instruction; of those,
`same_shape` have equal kind, stride, fields and words (for parallel arrays and split tables,
whose member offsets the data decide, the field widths and word count), and `same_place`
have equal `base - operand` at an aligned evidence instruction (`placed`: regions with
instruction evidence, parallel arrays apart). Shares are over both tunes' memory regions.

### sidreloc

For the fixtures' `base` and `w2000` tunes and every grouped HVSC tune, `sidreloc.relocate`
(one page) against the map's implied high bytes in the image (`address_bytes`):
`sidreloc` counts its high bytes outside the executed instructions (code operands are not
layout), `both` the shared ones, `static` the map's high bytes sidreloc keeps unchanged and
`undecided` those it leaves open (`?`, `.`, or unlisted). Precision is `both` over the map's
high bytes sidreloc decided; recall is `both` over sidreloc's.

### Results

1000 play calls of each tune's start song; 14 GoatTracker 2.77 example songs in each variant,
and the HVSC tunes SIDId names `GoatTracker_V2*` or `JCH_NewPlayer*` (PSID with a play routine;
every one mapped). The `base` and `w2000` scores are identical, count for count:

| Kind | Pred | TP | Precision | Truth | Recall | Reached | Recall (reached) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| words | 6 | 0 | 0.00 | 0 | - | 0 | - |
| pointers | 14 | 14 | 1.00 | 14 | 1.00 | 14 | 1.00 |
| tables | 28 | 28 | 1.00 | 28 | 1.00 | 28 | 1.00 |
| records | 2 | 0 | 0.00 | 57 | 0.00 | 57 | 0.00 |
| arrays | 301 | 287 | 0.95 | 366 | 0.80 | 364 | 0.81 |
| parallel | 39 | 29 | 0.74 | 53 | 0.57 | 53 | 0.57 |
| targets | 133 | 133 | 1.00 | 458 | 0.29 | 203 | 0.66 |
| lo bytes | 202 | 202 | 1.00 | 731 | 0.28 | 231 | 0.57 |
| hi bytes | 202 | 202 | 1.00 | 458 | 0.44 | 133 | 0.98 |

- Records: the player's reset loop (`sta mt_chnsongptr,x` for `x` = 41 down to 0) is a
  stride-1 sweep over the stride-7 channel records. Layout merges overlapping aggregates
  whose strides divide each other into the gcd, so one stride-1 array covers all four record
  groups and no records are left. Those arrays are also most of the `arrays` false positives.
  The other two predicted records are stride-3 readings of the filter tables, where the index
  only took multiples of 3.
- Words: every predicted word is a channel's `freqlo`/`freqhi` field pair, which a carry chain
  makes one 16-bit value. The source declares these as separate `.BYTE` record fields, so the
  truth has no words.
- Parallel false positives: the filter time/speed tables, which the truth leaves unpaired
  because their declared lengths differ, and groups of channel records, which the truth's
  `parallel` (equal-length byte regions only) does not model.
- Recall of targets and address bytes is limited by coverage: patterns and table entries the
  run never touched. Missed low bytes the run did reach are mostly the effect jump tables
  (`addr_lo`, high byte in code).

Consistency (share of both tunes' memory regions; `same_place` over `placed`):

| Group | Pairs | Regions | Corresponding | Same shape | Same place |
| --- | --- | --- | --- | --- | --- |
| fixtures base/w2000 | 14 | 780 | 1.000 | 1.000 | 1.000 |
| GoatTracker_V2.x | 7679 | 456580 | 0.860 | 0.770 | 0.981 |
| JCH_NewPlayer_V20 | 1723 | 188290 | 0.942 | 0.889 | 0.984 |
| JCH_NewPlayer_V14 | 712 | 73175 | 0.955 | 0.894 | 0.955 |
| JCH_NewPlayer_V15 | 310 | 28060 | 0.921 | 0.855 | 0.976 |
| JCH_NewPlayer_V17 | 232 | 19580 | 0.950 | 0.916 | 0.939 |
| JCH_NewPlayer (unversioned) | 118 | 10760 | 0.667 | 0.621 | 0.977 |
| other JCH_NewPlayer versions (V0x-V19) | 566 | 53566 | 0.953 | 0.908 | 0.951 |

`GoatTracker_V2.x` covers every 2.x release and its assembly options, and consecutive tunes
need not share the player build.

sidreloc (high bytes; precision over the bytes sidreloc decided):

| Tunes | Relocated | Layout | sidreloc | Both | Static | Undecided | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fixtures | 28 | 404 | 1152 | 404 | 0 | 0 | 1.000 | 0.351 |
| GoatTracker_V2 | 7243 | 141491 | 445470 | 139940 | 353 | 1198 | 0.997 | 0.314 |
| JCH_NewPlayer | 3584 | 60500 | 267212 | 47719 | 8 | 12747 | 0.999 | 0.179 |

537 HVSC tunes did not relocate (`bad header` 231, `invalid range` 225, `verification failed`
66, `no solution` 13, `cycles exhausted` 2). sidreloc emulates every subtune, while the map
covers one song's first calls, which bounds recall.

## Tests

`tests/test_vtools.py` covers the parsers without Docker; `tests/test_vtools_docker.py` runs the
image (skipped unless it exists, or `DI_VTOOLS_BUILD=1` builds it): the listing matches the image,
the relocated variant has the base layout shifted by its load delta, sidreloc's high bytes are
truth high bytes, and SIDId names the packed tunes `GoatTracker_V2.x`. CI runs both in the
`vtools-integration` job. `tests/test_layoutscore.py`
checks the matching rules, and on one program assembled at two load addresses exact scores,
full correspondence and sidreloc agreement (sidreloc stubbed).
