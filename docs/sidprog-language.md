# sidprog language specification

sidprog is the canonical structured text a decompiled playroutine serialises
to — the ONE language of the decompiler (spec §6). It is *specified*: the
grammar below is normative and `dumps`/`loads` (aliases of `emit`/`parse` in
`deity_informant.sidprog`) satisfy the canonical-fixpoint law
`dumps(loads(dumps(m))) == dumps(m)`. The REGION STRUCTURE of the text *is*
the control flow: a block that ends without an explicit terminator falls
through to the next item its nesting dictates (next sibling, if-join, loop
header via `continue`, loop exit via `break`). Two executors share one
semantics: the pc-driven `structured.Walker` runs the in-memory block model;
the tree-driven `sidprog.TreeWalker` (via `TextModel.run`) runs the parsed
region trees. Gate C enforces their bit-exact equality (cycle-stamped SID
write log + end memory) corpus-wide at full Songlengths duration.

## Versioning

The document opens with `sidprog <major>`. The current major is **1**
(`sidprog.SIDPROG_VERSION`). Majors are incompatible: a reader accepts only
its own major and rejects any other with `sidprog.SidprogVersionError` (a
`ValueError` subclass), so an unknown future-version document fails cleanly at
the header rather than mis-parsing later constructs. Backward-compatible
growth within a major is additive (new optional header directives a reader may
ignore); any change that alters the meaning of existing constructs bumps the
major.

Pre-release changes within major 1 (never released, no bump):

- memory references over 2-byte constant addresses serialise as canonical cell
  names (`sid.vN.*`/`filter.*`/`zp_XX`/`m_XXXX`), canonical indexed addresses
  as `name[REG]`; raw `mem[expr]` remains for every other address shape.
- structural flow: per-block terminator `goto` lines and universal block
  labels are gone. Fallthrough is implicit in the region nesting, a static
  branch serialises as an `if @tP` region whose then-arm is the taken edge,
  and labels appear only on goto/dynamic-branch targets (plus rare payload
  boundary markers). Earlier emitters wrote a labelled block per pc with
  explicit `goto`/`if … goto … else …` terminator lines.
- statement cleanup: a single-use non-volatile machine load serialises as its
  memref at the use site instead of a `uN =` line, and `if`/`ifnot` condition
  positions canonicalise sub/add compare-to-zero to direct compares (see
  "Statement sugar" below). Earlier emitters wrote every machine load as a
  `uN =` line and every condition verbatim.
- typed song data + role aliases: classified data regions move from anonymous
  `image { }` hex into a `data { }` section (bytes inline, exact partition),
  and classified state cells gain `symbols { }` aliases used throughout the
  procedure bodies (see "Data declarations" and "Symbols" below). Earlier
  emitters wrote the whole image anonymously and only canonical cell names.
- evidence frontier: an edge the static terminator proves but the evidence
  never took serialises as an `unobserved $XXXX` marker instead of a `goto`
  to a label that leads nowhere real, and evidence-unexecuted blocks outside
  the dynamic-landing closure drop their serialization behind the marker (see
  "Evidence frontier" below). Earlier emitters wrote `goto`+label for every
  such edge and serialised statically materialized never-executed blocks.

## Grammar (EBNF)

Lexical: `hex = "$" , hexdigit , { hexdigit }`. A hex literal's **width in
bytes** is `max(1, digits / 2)` (`$05` is 1 byte, `$0005`/`$1234` are 2).
`bytehex` is a 2-digit `hex`; `hexpair` is two bare hex digits. `ws` is
spaces; a `;` begins a comment to end-of-line; blank lines are ignored.
Indentation is insignificant (the emitter indents one space per nesting depth
for readability only).

```ebnf
document    = version , { header } , image , [ data ] , [ symbols ] , { proc } ;
version     = "sidprog" , ws , integer , newline ;

header      = play | init | subtune | sidinit | dispatch ;
play        = "play" , ws , hex , newline ;          (* required *)
init        = "init" , ws , hex , newline ;          (* required; provenance *)
subtune     = "subtune" , ws , integer , newline ;   (* optional; default 0 *)
sidinit     = "sid-init" , ws , "{" , newline ,
              { bytehex , ws , "=" , ws , bytehex , newline } ,
              "}" , newline ;   (* init-phase SID register writes, in order *)
dispatch    = "dispatch" , ws , hex , ":" , { ws , bytehex } , newline ;
                                        (* proven opcode set for an SMC cell *)

image       = "image" , ws , "{" , newline ,
              { hex , ":" , ws , { hexpair } , newline } ,   (* <=16 bytes/row *)
              "}" , newline ;   (* runs of non-zero cells, packed hex pairs *)

data        = "data" , ws , "{" , newline , { decl } , "}" , newline ;
decl        = ws , kind , ws , cellname , "[" , integer , "]" , { ws , attr } ,
              ":" , newline , { ws , { hexpair } , newline } ;
                              (* extent bytes inline, <=16 bytes/row *)
kind        = "table" | "stream" ;
attr        = "stride" , ws , integer          (* record size in bytes *)
            | "+" , cellname                   (* co-base read inside the region *)
            | ( "lo" | "hi" ) , ws , cellname  (* pointer-table pairing: partner *)
            | "via" , ws , cellname            (* stream: the walking pair's lo cell *)
            | "->" , ws , hex , ".." , hex     (* pointer-table entry value span *)
            | "cmp" , { ws , bytehex }         (* stream byte-class compare alphabet *)
            | "dispatch" , { ws , hex }        (* dispatch sites consuming the bytes *)
            | "observed" ;                     (* extent observed, not proven *)

symbols     = "symbols" , ws , "{" , newline ,
              { ws , "alias" , ws , aliasname , ws , "=" , ws , cellname , newline } ,
              "}" , newline ;
aliasname   = letter , { letter | digit | "_" } ;   (* must not shadow any
              canonical cell name, register, or uN/tN/rN slot *)

proc        = "proc" , ws , hex , ws , "{" , newline , { item } , "}" , newline ;
item        = block | ifregion | loop | opswitch | gotoswitch | callswitch
            | flow ;
loop        = "loop" , ws , "{" , newline , { item } , "}" , newline ;
flow        = "goto" , ws , hex , newline            (* to a labelled block *)
            | "unobserved" , ws , hex , newline      (* proven, never-observed edge *)
            | "continue" , newline                   (* back to loop header *)
            | "break" , newline ;                    (* to loop exit *)

block       = [ label , newline ] , { binding } , { stmt } , [ term , newline ] ;
label       = hex , ":" ;   (* only on goto/dyn-branch targets + boundaries *)
binding     = tref , ws , "=" , ws , expr , newline ;  (* per-block CSE binding *)

stmt        = [ cyc , ws ] , ( pen | load | store | regset ) , newline
            | cyc , newline ;             (* trailing cost before the terminator *)
cyc         = "@" , integer ;                         (* cycle cost, >=1 *)
pen         = ( "@x" | "@xi" ) , "(" , expr , "," , ws , expr , ")" ;
                                        (* indexed / (ind),Y page-cross penalty *)
load        = uni , ws , "=" , ws , memref ;
store       = memref , ws , "=" , ws , expr ;
regset      = reg , ws , "=" , ws , expr ;            (* out-expr != identity *)

memref      = cellname                       (* mem[const:2], named *)
            | cellname , "[" , reg , "]"     (* mem[zext2(reg) + const:2 >= $100] *)
            | "mem[" , expr , "]" ;          (* any other address shape *)
cellname    = sidname | "zp_" , 2 * hexdigit | "m_" , 4 * hexdigit ;
sidname     = "sid.v" , ( "1" | "2" | "3" ) , "." , voicereg
            | "filter." , filterreg ;
voicereg    = "freq_lo" | "freq_hi" | "pw_lo" | "pw_hi" | "ctrl"
            | "attack_decay" | "sustain_release" ;
filterreg   = "cutoff_lo" | "cutoff_hi" | "resonance" | "mode_vol" ;

term        = dynbranch | cgoto | igoto | call | ret ;
                       (* an unconditional goto is never written: implicit *)
dynbranch   = ( "if" | "ifnot" ) , ws , expr , ws , "goto" , ws ,
              "(" , expr , ")" , ws , "else" , ws , hex ;
                       (* escape hatch: SMC branch displacement; see below *)
cgoto       = "goto" , ws , "(" , expr , ")" ;        (* computed jump *)
igoto       = "igoto" , ws , ( hex | "(" , expr , ")" ) ;   (* jmp (indirect) *)
call        = "call" , ws , target , ws , "ret" , ws , hex ;
                       (* ret = the address the jsr pushes (real memory) *)
ret         = "ret" ;
target      = hex | "(" , expr , ")" ;   (* "(expr)" is a proven dynamic target *)

ifregion    = ( "if" | "ifnot" ) , ws , "@t" , integer , ws , expr , ws ,
              ( "{" , newline , { item } ,
                ( [ "} else {" , newline , { item } ] , "}"
                | "} else unobserved" , ws , hex )
              | "unobserved" , ws , hex ) , newline ;
              (* then-arm = branch taken; @tP = static taken-cycle penalty;
                 the unobserved forms are pure-frontier arms *)
gotoswitch  = "switch goto {" , newline ,
              { "case" , ws , hex , ":" , ws , "{" , newline , { item } ,
                "}" , newline } ,
              "}" , newline ;   (* proven targets of the preceding cgoto/igoto *)
callswitch  = "switch call { " , [ hex , { ws , hex } ] , " }" , newline
            | "switch call {" , newline , [ hex , { ws , hex } , newline ] ,
              { "case" , ws , hex , ":" , ws , "{" , newline , { item } ,
                "}" , newline } ,
              "}" , newline ;
              (* proven targets of the preceding dynamic call; a case arm is
                 that handler's tree inlined, bare targets are procs *)
opswitch    = [ label , newline ] ,
              "switch code[" , hex , "] {" , newline ,
              { "case" , ws , bytehex , ":" , ws , "{" , newline , { item } ,
                "}" , newline } ,
              "}" , newline ;   (* opcode-SMC dispatch over a `dispatch` cell *)

expr        = atom ;
atom        = hex | reg | uni | tref | memref | zext | carry | group ;
zext        = ( "zext1" | "zext2" ) , "(" , expr , ")" ;
carry       = "carry" , "(" , expr , "," , ws , expr , ")" ;
group       = "(" , chain , ")" , [ ":" , integer ] ; (* :n = result width *)
chain       = addsub | binop | compare | logic ;
addsub      = expr , { ( "+" | "-" ) , ws , expr } ;  (* INT_ADD / INT_SUB *)
binop       = expr , ( "<<" | ">>" ) , ws , expr ;    (* INT_LEFT / INT_RIGHT *)
compare     = expr , ( "==" | "!=" | "<" | "<=" ) , ws , expr ;  (* 1-byte *)
logic       = expr , ( "|" | "^" | "&" ) , { same-op , expr } ;  (* N>=2 *)

reg         = "A" | "X" | "Y" | "SP" | "C" | "Z" | "I" | "D" | "B" | "V" | "N"
            | "r" , integer ;            (* r4-r7,r15: unnamed 6510 status slots *)
uni         = "u" , integer , [ ":" , integer ] ;    (* per-block load temp *)
tref        = "t" , integer ;            (* per-block CSE binding reference *)
```

Node/width correspondences (the `expr` algebra of `deity_informant.expr`):
`INT_SUB`'s right operand is never a constant (a constant subtrahend
canonicalises to `INT_ADD` of its negation); `compare`/`carry` results are one
byte and carry no `:n`; `mem` loads are one byte; a lone `-$k` inside an
`addsub` is the two's-complement of an added constant.

## Named machine state (normative bijection)

`memref` names are a **bijection with exact address expression trees**, so
`parse` reconstructs the tree byte-for-byte and re-`emit` is a fixpoint:

- `cellname` ⇔ `("mem", ("const", addr, 2), 1)`. The name of a 16-bit `addr`
  is total and unique: the SID table (`$D400`-`$D414` ⇒ `sid.v{1,2,3}.<reg>`
  seven registers per voice, `$D415`-`$D418` ⇒ `filter.*`; the table in
  `render.sid_name` is normative), else `zp_XX` for `addr < $100`, else
  `m_XXXX` (uppercase hex, fixed width). Non-canonical spellings (`m_D400`,
  `m_00FB`, `zp_5`) are rejected; a 1-byte const address stays raw `mem[$XX]`.
- `cellname[REG]` ⇔ `("mem", INT_ADD:2(INT_ZEXT:2(reg), const:2), 1)` with
  the constant `>= $100` and **last** (the `expr.simplify` canonical operand
  order). Any other shape — const-first, non-reg index, other widths, a CSE
  `tN` in the address — is not sugared and round-trips as raw `mem[expr]`.

## Data declarations (typed song data)

`data { }` carves classified song-data regions out of the post-init image,
derived mechanically from the streams classification
(`deity_informant.datadecl.declarations` over `streams.classify`/`streams`):

- **Partition law.** Every declared region carries its bytes inline and the
  regions are mutually disjoint; `image { }` holds exactly the residue.
  `parse` writes declarations and image rows into one 64 KB buffer, so mem0
  reconstructs BYTE-EXACT (the emitter asserts the partition; declarations
  move bytes, never duplicate them). Zero page, the stack page and executed
  code bytes are never carved.
- **Forms.** `table` covers byte tables, parallel pointer-table pairs
  (`lo`/`hi` name the partner and `->` spans the entry values) and
  fixed-stride record arrays (`stride`, with `+name` co-bases for the other
  fields read inside the region). `stream` covers pointer-walked
  command/script/pattern byte streams (`via` names the walking pair's lo
  cell; `cmp`/`dispatch` attach the proven byte-class alphabet and consuming
  dispatch sites where the analysis found them).
- **Extent honesty.** A declared extent without a marker is proven: every
  index expression reaching the region is statically bounded below its width
  mask and the domain fits before the next boundary. Anything else is emitted
  with the `observed` marker and the extent of the full-length evidence reads
  attributed to the region's own read sites — the text never claims more than
  the analysis knows. Ambiguous or overlapping classifications stay in
  `image { }`.
- Declarations are serialization only: both executors read the same mem0, so
  walker semantics are byte-identical with or without the section.

## Symbols (role aliases)

`symbols { }` is a strict bijection `alias NAME = cell` generated from the
state-cell classification: pointer pairs alias to `ptr_XXXX_lo`/`ptr_XXXX_hi`
(XXXX = the pair's lo address), counters to `pos_XXXX` when they position a
pointer-pair deref else `ctr_XXXX`, index cells to `idx_XXXX` — mechanical,
address-embedding names, so collisions are impossible by construction and an
alias may never shadow a canonical cell name, register or `uN`/`tN` slot
(`parse` rejects it). Procedure bodies use the aliases; the alias table is
the ONLY mapping and `parse` resolves body names through it before the
expression grammar, so the memref bijection above is untouched. This table is
also the hook for a future user-supplied symbol map.

## Structure semantics

- **The nesting IS the flow.** A block with no terminator line falls through
  to the continuation its tree position dictates: the next sibling item, the
  join after its enclosing `if` arm, or wherever an explicit `goto`/
  `continue`/`break` flow item says. Only returns (`ret`), calls, computed
  jumps and the SMC-branch escape hatch are written as lines.
- **`if @tP expr { … } else { … }`.** Replaces the branch terminator. The
  then-arm is always the **taken** edge; `if` means taken when the flag is 1,
  `ifnot` when it is 0 (branch polarity is a keyword, never folded into the
  flag expression, which serialises unchanged). `@tP` is the static
  taken-cycle penalty `P = 1 + page-cross(target, fallthrough)` computed at
  emit time from the two static pcs; the executor adds `P` cycles when the
  branch is taken and nothing otherwise. An empty `else` arm is omitted.
- **Labels.** `$XXXX:` appears only where a pc must be addressable: `goto`
  targets (including cross-procedure "hidden" chains), call and
  dynamic-branch targets whose blocks serialize inside another procedure's
  tree, and the rare *boundary label* that separates two adjacent fallthrough
  payloads (e.g. a block split at the 64-instruction cap) so `parse` cannot
  merge them. Proc entries, `switch` subjects and inlined call arms carry
  their pcs in their own syntax and are never labelled redundantly. A label
  appears only where a REAL serialized block is targeted: an edge whose
  target has no serialized block is an `unobserved` marker, never a label.
- **Evidence frontier (`unobserved $XXXX`).** An edge the static terminator
  proves but the evidence never took, whose target has no serialized block.
  The marker replaces `goto` + label: the pc stays in the text (nothing is
  dropped silently), and if control ever reaches it the walker faults with a
  `WalkError` carrying that pc — the guarded-envelope doctrine applied to
  control flow. A pure-frontier taken edge collapses into the branch header
  (`if @t1 (cond) unobserved $XXXX`; the else arm, when present, simply
  continues after the line, since the marker never joins); a pure-frontier
  fallthrough arm collapses onto the closer (`} else unobserved $XXXX`); a
  frontier fallthrough or arm interior is the standalone flow line. The
  codec treats the marker as a verified edge to its pc, so tree flow still
  equals terminator flow exactly. *Keep rule* (which blocks serialize): a
  block is kept iff any variant of its pc executed in the evidence, or its
  pc is in the dynamic-landing closure of the kept set (committed dynamic
  dispatch/call target sets, static call targets, RTS-trick landings — the
  pcs run-time control can resolve to, which must stay readable and
  resolvable). Every other block — statically materialized code reachable
  only through never-taken edges — drops its serialization, and each edge
  into that set serialises as the marker recording the pc.
- **Dynamic-target branch (escape hatch).** A branch whose displacement byte
  is self-modified keeps the explicit line
  `if|ifnot expr goto (dynexpr) else $FT`: the taken target is computed at
  run time (resolved against the serialized-pc map; fault when absent, and
  the emitter labels every proven target), the `else` pc exists solely for
  the run-time page-cross penalty, and the structural continuation after the
  line is the fallthrough edge.
- **Dynamic flow is evidence-scoped.** `switch goto { case $XXXX: {…} }`
  lists the proven targets of the preceding computed jump with each arm's
  tree inline; `switch call` lists a dynamic call's proven targets, inlining
  single-site handlers as `case` arms (their `ret` unwinds to the call's
  continuation) and leaving shared handlers as bare pcs resolved to their
  `proc`s. An `igoto $addr` whose vector cell is image-derived emits its sole
  arm as plain continuation. At run time a computed target resolves through
  the case arms first, then the serialized-pc map (the emitter labels every
  proven and evidence-observed landing); a pc outside the serialized program
  faults (`WalkError`) — the same guard the pc-driven walker's block lookup
  applies.
- **Opcode dispatch.** A `dispatch` cell's variants appear under
  `switch code[$addr] { case $op: {…} }` — also for a single proven variant,
  so the run-time opcode guard is always explicit; an opcode outside the
  proven set faults.
- **CSE bindings are textual sugar.** `tN = expr` lines name subtrees the
  emitter chose to share (bound iff the binding prints shorter than inlining);
  `parse` inlines every `tN` back, so the model never contains binding nodes.
  A binding always has at least two references; a subtree used once is never
  bound (bind-site elimination is implied by the shorter-print rule).

## Statement sugar (emit-time, walker-equivalent)

- **Single-use load inlining.** A machine load (`uN = memref` line) whose slot
  is consumed exactly once serialises as its `memref` at the use site — the
  `uN =` line and the slot number disappear from the text — iff the move is
  provably semantics-free: no `st` event lies between the load and its
  consumer in the block's event stream, and every address the load can read
  is non-volatile (a constant cell outside the volatile set, or a
  byte-bounded index over a constant base whose 256-byte window is disjoint
  from it; anything unprovable keeps its line). The load's leading `@n` cycle
  stamp coalesces into the following statement's stamp, so the cycle sum at
  every remaining event is unchanged. `parse` rebuilds an expression-level
  memory read at the consumer's machine position; execution is bit-identical
  because the cell's value cannot change across the move (no intervening
  store) and does not depend on the cycle count (non-volatile), and a load
  contributes no cycles of its own. Volatile-address loads are never inlined.
- **Condition canonicalisation.** In `if`/`ifnot` condition positions only
  (the `if @tP` region header and the dynamic-branch escape hatch), the
  emitter rewrites `(a - b) == $00` to `(a == b)` and the CMP idiom
  `(a + k) == $00` (a two's-complement added constant) to `(a == $konst)`
  with `konst = (-k) & mask` — likewise the `!=` forms — when the operand,
  result and constant widths all agree (skipped otherwise; `<`, `<=` and
  sign-bit tests are untouched). This is a canonicalisation, not a bijection:
  `parse` builds the direct-compare tree, which is walker-equivalent to the
  sub/add-compare on equal widths, and re-emission is a fixpoint because the
  direct form matches no rewrite pattern.

## Two executors, one semantics

`structured.Walker` executes the in-memory model pc-by-pc (terminator tuples
carry the pcs). `sidprog.parse` produces a `TextModel` holding region trees
with *synthetic* block keys — pcs exist only where the text serialises them
(labels, proc entries, switch subjects and case pcs, `call … ret` values) —
and `TextModel.link()` resolves the trees to a flat control program that
`sidprog.TreeWalker` (`TextModel.run(frames)`) executes: same compiled block
payloads (`structured.compile_block`), same volatile-read/cycle model, `call`
pushes the real return bytes, `ret` pops the real bytes from stack memory and
resolves the popped pc through the serialized-pc map (call continuations are
indexed by their serialized `ret` operands; RTS-trick landings are labelled),
`igoto` reads its vector from memory. An `unobserved` marker links to a fault
node: reaching it raises `WalkError` carrying the marker's pc. Gate C requires
both executors to reproduce the evidence log bit-exactly.

## Laws

- **Canonical fixpoint.** `dumps(loads(dumps(m))) == dumps(m)` for every
  model `m` (property-tested over generated models, `tests/test_sidprog.py`).
- **Header identity.** `loads(dumps(m))` preserves the image (data regions +
  residue reassembled byte-exact), play/init/subtune, prologue, dispatch sets,
  data declarations and the alias table exactly; block pcs are intentionally
  not round-tripped (structure replaces them).
- **Executable equivalence (Gate C).** `TextModel.run(frames)` equals
  `structured.Walker(model).run(frames)` equals the evidence log — cycle-
  stamped `(cycle, reg, value)` writes and end memory bit-exact for the full
  Songlengths duration, on the fuzz corpus and real tunes.
- **Size (Gate L).** The text is smaller than the disassembly listing.
