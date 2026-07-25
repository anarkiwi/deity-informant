# sidprog language specification

sidprog is the canonical structured text a decompiled playroutine serialises
to — the ONE language of the decompiler (spec §6). It is *specified*:
the grammar below is normative, `dumps`/`loads` (aliases of `emit`/`parse` in
`deity_informant.sidprog`) are exact inverses on canonical models, and one
interpreter core (`structured.Walker` over `structured.compile_block`) is the
single execution semantics for the text. `parse` additionally re-verifies the
structurer codec: the region nesting of the document must flatten back to
exactly the block CFG it carries (`codec.verify`).

## Versioning

The document opens with `sidprog <major>`. The current major is **1**
(`sidprog.SIDPROG_VERSION`). Majors are incompatible: a reader accepts only
its own major and rejects any other with `sidprog.SidprogVersionError` (a
`ValueError` subclass), so an unknown future-version document fails cleanly at
the header rather than mis-parsing later constructs. Backward-compatible
growth within a major is additive (new optional header directives a reader may
ignore); any change that alters the meaning of existing constructs bumps the
major.

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
item        = block | loop | opswitch | flow ;
loop        = "loop" , ws , "{" , newline , { item } , "}" , newline ;
flow        = "goto" , ws , hex , newline            (* region-flow echo *)
            | "continue" , newline                   (* back to loop header *)
            | "break" , newline ;                    (* to loop exit *)

block       = label , newline , { binding } , { stmt } , tail ;
label       = hex , [ "/" , bytehex ] , ":" ;  (* "/op" iff pc is a dispatch cell *)
binding     = tref , ws , "=" , ws , expr , newline ;  (* per-block CSE binding *)

stmt        = [ cyc , ws ] , ( pen | load | store | regset ) , newline
            | cyc , newline ;             (* trailing cost before the terminator *)
cyc         = "@" , integer ;                         (* cycle cost, >=1 *)
pen         = ( "@x" | "@xi" ) , "(" , expr , "," , ws , expr , ")" ;
                                        (* indexed / (ind),Y page-cross penalty *)
load        = uni , ws , "=" , ws , "mem[" , expr , "]" ;
store       = "mem[" , expr , "]" , ws , "=" , ws , expr ;
regset      = reg , ws , "=" , ws , expr ;            (* out-expr != identity *)

tail        = ifregion | term , newline , [ dynswitch ] ;
term        = goto | branch | cgoto | igoto | call | ret ;
goto        = "goto" , ws , hex ;   (* elided iff the target label is next *)
cgoto       = "goto" , ws , "(" , expr , ")" ;        (* computed jump *)
branch      = ( "if" | "ifnot" ) , ws , expr , ws , "goto" , ws , target ,
              ws , "else" , ws , hex ;
igoto       = "igoto" , ws , ( hex | "(" , expr , ")" ) ;   (* jmp (indirect) *)
call        = "call" , ws , target , ws , "ret" , ws , hex ;
ret         = "ret" ;
target      = hex | "(" , expr , ")" ;   (* "(expr)" is a proven dynamic target *)

ifregion    = branch , ws , "{" , newline , { item } ,
              [ "} else {" , newline , { item } ] , "}" , newline ;
                          (* then-arm = branch taken; else-arm = fallthrough *)
dynswitch   = gotoswitch | callswitch ;
gotoswitch  = "switch goto {" , newline ,
              { "case" , ws , hex , ":" , ws , "{" , newline , { item } ,
                "}" , newline } ,
              "}" , newline ;   (* recorded targets of the preceding cgoto/igoto *)
callswitch  = "switch call {" , [ ws , hex , { ws , hex } ] , ws , "}" , newline ;
                          (* recorded targets of the preceding dynamic call *)
opswitch    = "switch code[" , hex , "] {" , newline ,
              { "case" , ws , bytehex , ":" , ws , "{" , newline , { item } ,
                "}" , newline } ,
              "}" , newline ;   (* opcode-SMC dispatch over a `dispatch` cell *)

expr        = atom ;
atom        = hex | reg | uni | tref | mem | zext | carry | group ;
mem         = "mem[" , expr , "]" ;                   (* 1-byte load *)
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

## Structure semantics

- **Regions carry no semantics of their own** — every edge is stated by a
  block's terminator line; `loop`/`ifregion`/`continue`/`break`/`goto` echoes
  are a *verified rearrangement* of those edges. `parse` rebuilds the block
  CFG from the terminator lines alone and `codec.verify` re-checks that
  structuring the rebuilt model reproduces the same nesting.
- **CSE bindings are textual sugar.** `tN = expr` lines name subtrees the
  emitter chose to share (bound iff the binding prints shorter than inlining);
  `parse` inlines every `tN` back, so the model never contains binding nodes.
- **Fallthrough elision.** A block whose terminator is an unconditional `goto`
  to the very next emitted label omits the `goto` line; a `branch` header
  always carries its `else` target (terminator lines are position-independent).
- **Dynamic flow is evidence-scoped.** `switch goto`/`switch call` list the
  recorded targets of a computed jump/call; an `igoto $addr` whose pointer
  cell is image-derived emits its arms inline with no switch wrapper. A
  `dispatch` cell's block variants appear under `switch code[$addr]` with
  `$pc/$op` labels; executing an opcode outside the proven set faults.

## Laws

- **Canonical fixpoint.** `dumps` is idempotent after one round trip:
  `dumps(loads(dumps(m))) == dumps(m)` for every model `m`.
- **Round-trip identity.** On a canonical model, `loads(dumps(m))` reproduces
  `m` structurally (image, header, prologue, dispatch sets, and every block's
  events, terminator and register out-expressions).
- **Codec inversion.** `loads` re-verifies `flatten(structure(model)) ≡ CFG`
  on the rebuilt model (build-time structurer check, spec §5).
- **Executable equivalence.** The parsed `TextModel` drives the same `Walker`
  as the in-memory `Model`; both reproduce the original's cycle-stamped
  `(cycle, reg, value)` write log and end memory bit-exact for the full
  Songlengths duration (Gate C), and the text is smaller than the disassembly
  listing (Gate L).

The serialisation laws are property-tested over generated models
(`tests/test_sidprog.py`), not only corpus samples.
