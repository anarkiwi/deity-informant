# The frameprog grammar (generated)

`deity_informant/sidprog.lark` is the ONE grammar of the decompiler's text
layer and the normative definition of the artifact. It is parsed LALR(1) by
`deity_informant.grammar`, the only reader the layer has, and a document opens
`frameprog <major>` ([frameprog.md](frameprog.md)).

The cycle-exact `sidprog <major>` dialect this grammar grew out of is
**retired** with its emit path (docs/register-model-lift-impl.md,
housekeeping): the cycle-exact anchor is the committed model, the walker replay
and the VM/recorder against sidplayfp, never the text, so a text nothing emits
and nothing reads is gone. Its landed specification is
[decompiler-implementation.md](decompiler-implementation.md); the grammar file
and `deity_informant/sidprog.py` keep their names, which is why the artifact's
own header comment still cites them.

**frameprog major 1 (Phase 3a) is total**: the dialect gained `image { }`,
`dispatch` header lines and an `evidence { }` section, so
`frameprog.block_model(frameprog.loads(text))` rebuilds the committed block
model the text was emitted from — image, executed pcs, block leaders,
play-written cells, observed transfer targets, read sites, the recurrence
record and the reduced init-copy result. A major-0 artifact predates those
sections and is refused. `evidence { }` addresses come as `$A` / `$A..$B`
spans; `written` carries the evidence half only, because
`structured.Model` unions page one in as a rule. The one channel not carried
is the closure *diagnostic* (`Closure.diag`/`note`), which only the CLI proof
report reads and no rebuild consumes.

Expressions, memrefs, `data { }`/`symbols { }`, loops, switches, case arms and
flow items are written once: lark templates parameterise the region productions
over the item alphabet (`fitem`). A block is a run of payload lines
plus the closers that end it (`ret`, a computed jump, an `if` region, a call
body, a flow item), so the nesting carries the flow with no lookahead beyond
LALR(1).

Lexical: a `HEX` literal's **width in bytes** is `max(1, digits / 2)` (`$05`
is one byte, `$0005`/`$1234` two); `HEXBYTES` rows are packed uppercase byte
pairs; `;` starts a comment to end-of-line and blank lines are insignificant
(both are absorbed by the `_NL` terminal); indentation is insignificant (the
emitters indent one space per nesting depth for readability only).

A memory reference is either `mem[<address>]` or the indexed form
`<base>[<index>]`, which denotes `base + zext2(index)`: `base` is a canonical
cell name (a declared table base or one of its lane cobases, a state array, a
SID register) and `index` is **any expression**, so a computed read against a
declaration is written as the access it is rather than as address arithmetic.
The reader supplies the `zext2`, so an emitter may drop it; every other form of
address stays `mem[...]`.

A third form is the **pointer deref** `*<base>[<index>]` (and `*<base>` for row
zero), which denotes `mem[base:2] + zext2(index)`: `base` names the cell holding
the pointer *word*, so the `:2` is the form's own and never written. It is
rung (f)'s ([frameprog.md](frameprog.md) §4.4), and the emitter writes it only
where that rung proved every definition of the pointer against a declared
`lo`/`hi` table — an unproven deref stays `mem[...]`, so the text distinguishes
the two.

A reference — named, indexed, deref or raw — carries an optional `:N` **width suffix**,
absent for the one byte a 6502 access moves and `:2` for the 16-bit forms rung
(d) fuses ([frameprog.md](frameprog.md) §4): `m_0021:2` is the word at `$21`, and
`m_0021:2 = e` stores it. A store's suffix must equal the width of the value
stored, so the width is stated once. `state { }` fields are `u8` or `u16`
accordingly.

`:2` denotes two **adjacent** bytes in every form but one. Where the indexed
form's base is a table `data { }` declares `lo T`, `base[index]:2` is the **pair
row** `(base[index], T[index])` — the datum's two columns, at whatever distance
the declaration puts between them — and it reads as
`zext2(T[index]) << 8 | zext2(base[index])`, stores as those two byte stores. So
the `lo`/`hi` attributes are the disambiguator and a consumer must read the
declaration before a `:2` row: `Grid_Runner` declares `table m_1493[3] lo
m_1496`, whose row `m_1493[0]:2` is the pattern pointer `$167B`, three bytes
apart, where the adjacent reading gives `$0D7B`. A base with no `lo` attribute
carries no pair row, so its `:2` is the adjacent word rung (d) fuses.

A `state { }` field may carry a **block extent** — `ptr_0021: u16 in m_7338,
m_7401` — naming the declared data blocks the derefs through that pointer land
in. An extent is a pointer's, so only a scalar `u16` field carries one; each
block is named by its base cell, an alias included, and the blocks are emitted
ascending. The clause sits between the array brackets and `observed`.

A field's type may carry a **role** — `ptr_0021: cursor u16`, one of `cursor`,
`accumulator`, `counter`, `flags`, `parameter`, `vm` — naming how the play
routine updates that cell (docs/register-model-lift-impl.md, stage 2). The role
qualifies the type and **licenses nothing**: an un-roled `uN` field is legal and
means exactly what it always did, so a cell whose update shape no role covers is
declared without one rather than misdescribed.

A width-2 store may carry the **write order** `hi-first`, which says its two
bytes leave in descending address order: `hi-first sid.v1.freq_lo[y]:2 = e`
writes the high cell before the low one. Absent the word, a word store emits
ascending. The order is a fact about the store rather than about its address, so
rung (d) can merge a pair the program wrote hi-first without resolving the index
it was written through ([frameprog.md](frameprog.md) §7.10.4); `framelog` keeps
write order inside the ctrl/AD/SR and `$19`-`$1C` sections, which is where the
difference is observable.

An operator chain's comparisons are p-code's, one spelling per mnemonic in operand
order: `==`, `!=`, `<`, `<=` and the signed pair `<s`, `<=s`
(`INT_SLESS`/`INT_SLESSEQUAL`). There is no `>=s`: the minimizer's `sge` term is
`INT_SLESSEQUAL` with its operands the other way, and it prints as one.

Reserved words are exactly the grammar's literal identifier terminals
(`grammar.keywords()`); `symbols { }` aliases may shadow none of them, nor a
canonical cell name, register or `uN`/`tN`/`rN` slot.

The block below is generated from the grammar file. `tests/test_grammar.py`
fails when it drifts; regenerate with
`SYNC_GRAMMAR_DOC=1 pytest tests/test_grammar.py`.

<!-- BEGIN GENERATED GRAMMAR: deity_informant/sidprog.lark -->
```lark
// The frameprog document grammar (docs/frameprog.md), parsed LALR(1);
// templates parameterise the region productions over the item alphabet. The
// sidprog text dialect it grew out of is retired (register-model-lift-impl.md
// housekeeping), so the cycle-annotation productions (CYC/CYCT/PENTAG, code[]
// dispatch subjects) and its proc/block forms are gone with it.

start: frameprog_doc

frameprog_doc: fphead _fheader* image_sec? state_sec? operators_sec? data_sec? symbols_sec? evidence_sec? sub*
fphead: "frameprog" INT _NL

_fheader: play | init | subtune | sidinit | inputs_sec | dispatch_set | relocated

play: "play" HEX _NL
init: "init" HEX _NL
subtune: "subtune" INT _NL
sidinit: _SIDINIT "{" _NL sidwrite* "}" _NL
sidwrite: HEX "=" HEX _NL
dispatch_set: "dispatch" HEX ":" HEX* _NL
relocated: "relocated" HEX ".." HEX "->" HEX _NL
inputs_sec: "inputs" "{" NAME* "}" _NL

// ---- image / data / symbols / state sections ---------------------------------
image_sec: "image" "{" _NL imgrow* "}" _NL
imgrow: HEX ":" HEXBYTES _NL

data_sec: "data" "{" _NL decl* "}" _NL
decl: kind NAME "[" INT "]" attr* ":" _NL datarow*
datarow: HEXBYTES _NL
kind: "table" -> k_table
    | "stream" -> k_stream
attr: "stride" INT      -> at_stride
    | "mut" INT*        -> at_mut
    | "+" NAME          -> at_cobase
    | "lo" NAME         -> at_lo
    | "hi" NAME         -> at_hi
    | "via" NAME        -> at_via
    | "->" HEX ".." HEX -> at_targets
    | "cmp" HEX*        -> at_cmp
    | "dispatch" HEX*   -> at_dispatch
    | "observed"        -> at_observed

symbols_sec: "symbols" "{" _NL aliasdef* "}" _NL
aliasdef: "alias" NAME "=" NAME _NL

// ---- evidence section (frameprog only) ---------------------------------------
// The trace channels a block-model rebuild consumes: executed pcs, block
// leaders, play-written cells, observed transfer targets, read sites, the
// recurrence record and the reduced init-copy result. Addresses come as spans;
// `written` is the evidence half, since page one is a rule of the model.
evidence_sec: "evidence" "{" _NL evline* "}" _NL
evline: "code" span* _NL               -> ev_code
      | "leaders" span* _NL            -> ev_leaders
      | "written" span* _NL            -> ev_written
      | "targets" HEX ":" HEX* _NL     -> ev_targets
      | "reads" HEX ":" span* _NL      -> ev_reads
      | "closure" INT INT INT INT _NL  -> ev_closure
      | "copy" HEX "=" HEX "@" HEX _NL -> ev_copy
      | "staged" HEX ":" INT INT _NL   -> ev_staged
      | "census" NAME INT _NL          -> ev_census
span: HEX [".." HEX]

state_sec: "state" "{" _NL statedef* "}" _NL
statedef: NAME ":" [srole] NAME [array] [statinit] [statext] [statobs] [statbnd] _NL
array: "[" "]"
// the initial value: the byte the init phase leaves in the cell, so the state
// block reads without the image behind it (stage 4 landing 4)
statinit: "=" HEX
statext: "in" NAME ("," NAME)*
statobs: "observed" HEX*
// the accumulator's bound (stage 4 landing 4): the constant the program ands the
// cell with, else the extent its values are witnessed in over the run
statbnd: "mask" HEX -> st_mask
       | "bound" HEX ".." HEX -> st_bound
// the role qualifies the field's type: it names how the cell is updated and
// licenses nothing, so an un-roled uN stays legal (register-model-lift stage 2)
!srole: "cursor" | "accumulator" | "counter" | "flags" | "parameter" | "vm"

// ---- operator section (a script VM's own operator set; stage 4 landing 4) -----
// One line per opcode of an SMC-operand dispatch. The name is the handler the
// paired tables select at that opcode, the arity is the operand bytes the arm
// consumes -- its cursor advance -- and the writes are the cells the arm's own
// blocks assign. A decoded-length arm repeats its operand group while the byte
// at `at + k*arity` stays in the span, and consumes `tail` bytes past it.
operators_sec: "operators" "{" _NL opdef* "}" _NL
opdef: "op" NAME HEX "arity" INT [oprep] "writes" NAME* _NL
oprep: "repeat" HEX ".." HEX "at" INT "tail" INT

// ---- procedures ----------------------------------------------------------------
sub: NAME "(" params ")" [rets] "{" _NL fitem* "}" _NL
params: (NAME ("," NAME)*)?
rets: "->" NAME ("," NAME)*

?fitem: fblock | loop{fitem} | swgoto{fitem} | swcall{fitem} | opsw_cell | forloop

// ---- region productions (parameterised over the item alphabet) -----------------
loop{item}: "loop" "{" _NL item* "}" _NL
case{item}: "case" HEX ":" "{" _NL item* "}" _NL
swgoto{item}: "switch" "goto" "{" _NL case{item}* "}" _NL
swcall{item}: "switch" "call" "{" HEX* "}" _NL -> swcall_flat
            | "switch" "call" "{" _NL [pclist] case{item}* "}" _NL -> swcall_deep
pclist: HEX+ _NL
callstmt{item}: "call" target "ret" HEX _NL -> call_flat
              | "call" target "ret" HEX "{" _NL item* "}" _NL -> call_deep
selse{item}: "}" _NL -> els_none
           | "}" "else" "{" _NL item* "}" _NL -> els_body
           | "}" "else" "unobserved" HEX _NL -> els_unobs

opsw_cell: [label] "switch" NAME "{" _NL case{fitem}* "}" _NL
forloop: "for" NAME "in" HEX ".." HEX "{" _NL fitem* "}" _NL

label: HEX ":" _NL
target: HEX -> tgt_static
      | "(" expr ")" -> tgt_dyn

// ---- blocks: payload lines plus the closers that end them ----------------------
fblock: label _fbody?
      | _fbody
_fbody: fline+ _fcloser*
      | _fcloser+
fline: asg _NL -> f_asg
      | _HIFIRST asg _NL -> f_asg_hifirst
      | pcall _NL -> f_pcall_void
      | lvalue ("," NAME)* "=" pcall _NL -> f_pcall_ret
pcall: NAME "(" (expr ("," expr)*)? ")"

_fcloser: dynbr | cgoto | igoto | callstmt{fitem} | fretline | fif | flowline

fif: ifw expr "{" _NL fitem* selse{fitem} -> fif_body
   | ifw expr "unobserved" HEX _NL -> fif_front

dynbr: ifw expr "goto" "(" expr ")" "else" HEX _NL
cgoto: "goto" "(" expr ")" _NL
igoto: "igoto" HEX _NL -> igoto_static
     | "igoto" "(" expr ")" _NL -> igoto_dyn
fretline: "ret" (NAME ("," NAME)*)? _NL
flowline: "goto" HEX _NL -> fl_goto
        | "unobserved" HEX _NL -> fl_unobs
        | "continue" _NL -> fl_cont
        | "break" _NL -> fl_brk

ifw: "if" -> w_if
   | "ifnot" -> w_ifnot

// ---- statements and expressions -------------------------------------------------
asg: lvalue "=" expr
lvalue: NAME [wsuf] -> lv_name
      | NAME "[" expr "]" [wsuf] -> lv_index
      | "*" NAME [wsuf] -> lv_deref_bare
      | "*" NAME "[" expr "]" [wsuf] -> lv_deref
      | "mem" "[" expr "]" [wsuf] -> lv_mem

?expr: HEX -> e_hex
     | NAME [wsuf] -> e_name
     | NAME "[" expr "]" [wsuf] -> e_index
     | "*" NAME [wsuf] -> e_deref_bare
     | "*" NAME "[" expr "]" [wsuf] -> e_deref
     | "mem" "[" expr "]" [wsuf] -> e_mem
     | zextw "(" expr ")" -> e_zext
     | truncw "(" expr ")" -> e_trunc
     | "carry" "(" expr "," expr ")" -> e_carry
     | "(" chain ")" [wsuf] -> e_group
wsuf: ":" INT
chain: expr (op expr)+
zextw: "zext1" -> z1
     | "zext2" -> z2
truncw: "trunc1" -> t1
      | "trunc2" -> t2
op: "+" -> o_add
  | "-" -> o_sub
  | "<<" -> o_shl
  | ">>" -> o_shr
  | "==" -> o_eq
  | "!=" -> o_ne
  | "<=" -> o_le
  | "<" -> o_lt
  | "<=s" -> o_sle
  | "<s" -> o_slt
  | "|" -> o_or
  | "^" -> o_xor
  | "&" -> o_and

HEX: /\$[0-9A-Fa-f]+/
HEXBYTES: /[0-9A-F]+/
INT: /\d+/
NAME: /[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_0-9]+)*/
_SIDINIT.5: "sid-init"
// a word store's own byte-emission order (frameprog form); hyphenated, so no
// NAME can spell it and no symbol alias can shadow it
_HIFIRST.5: "hi-first"
_NL: /(?:;[^\n]*)?\r?\n(?:[ \t]*(?:;[^\n]*)?\r?\n)*/

%ignore /[ \t]+/
```
<!-- END GENERATED GRAMMAR -->
