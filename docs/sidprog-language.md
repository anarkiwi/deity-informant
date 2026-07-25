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

## Grammar (EBNF)

Lexical: `hex = "$" , hexdigit , { hexdigit }`. A hex literal's **width in
bytes** is `max(1, digits / 2)` (`$05` is 1 byte, `$0005`/`$1234` are 2).
`bytehex` is a 2-digit `hex`; `hexpair` is two bare hex digits. `ws` is
spaces; a `;` begins a comment to end-of-line; blank lines are ignored.
Indentation is insignificant (the emitter indents one space per nesting depth
for readability only).

```ebnf
document    = version , { header } , image , { proc } ;
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

proc        = "proc" , ws , hex , ws , "{" , newline , { item } , "}" , newline ;
item        = block | ifregion | loop | opswitch | gotoswitch | callswitch
            | flow ;
loop        = "loop" , ws , "{" , newline , { item } , "}" , newline ;
flow        = "goto" , ws , hex , newline            (* to a labelled block *)
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
              "{" , newline , { item } ,
              [ "} else {" , newline , { item } ] , "}" , newline ;
              (* then-arm = branch taken; @tP = static taken-cycle penalty *)
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
  their pcs in their own syntax and are never labelled redundantly.
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
  arm as plain continuation (the vector cannot change). Executing any
  unlisted target faults (`WalkError`).
- **Opcode dispatch.** A `dispatch` cell's variants appear under
  `switch code[$addr] { case $op: {…} }` — also for a single proven variant,
  so the run-time opcode guard is always explicit; an opcode outside the
  proven set faults.
- **CSE bindings are textual sugar.** `tN = expr` lines name subtrees the
  emitter chose to share (bound iff the binding prints shorter than inlining);
  `parse` inlines every `tN` back, so the model never contains binding nodes.

## Two executors, one semantics

`structured.Walker` executes the in-memory model pc-by-pc (terminator tuples
carry the pcs). `sidprog.parse` produces a `TextModel` holding region trees
with *synthetic* block keys — pcs exist only where the text serialises them
(labels, proc entries, switch subjects and case pcs, `call … ret` values) —
and `TextModel.link()` resolves the trees to a flat control program that
`sidprog.TreeWalker` (`TextModel.run(frames)`) executes: same compiled block
payloads (`structured.compile_block`), same volatile-read/cycle model, `call`
pushes the real return bytes, `ret` pops and unwinds (an RTS-trick mismatch
re-enters via the serialized-pc map), `igoto` reads its vector from memory.
Gate C requires both executors to reproduce the evidence log bit-exactly.

## Laws

- **Canonical fixpoint.** `dumps(loads(dumps(m))) == dumps(m)` for every
  model `m` (property-tested over generated models, `tests/test_sidprog.py`).
- **Header identity.** `loads(dumps(m))` preserves the image, play/init/
  subtune, prologue and dispatch sets exactly; block pcs are intentionally
  not round-tripped (structure replaces them).
- **Executable equivalence (Gate C).** `TextModel.run(frames)` equals
  `structured.Walker(model).run(frames)` equals the evidence log — cycle-
  stamped `(cycle, reg, value)` writes and end memory bit-exact for the full
  Songlengths duration, on the fuzz corpus and real tunes.
- **Size (Gate L).** The text is smaller than the disassembly listing.
