# SIDC language specification

SIDC is the canonical structured text a decompiled playroutine serialises to.
It is a *specified* language: the grammar below is normative, `dumps`/`loads`
(aliases of `emit`/`parse` in `deity_informant.stext`) are exact inverses on
canonical models, and one interpreter core (`structured.Walker` over
`structured.compile_block`) is the single execution semantics for the text —
the model walker and the text walker are the same class over the same compiled
blocks (no drift; the byte-exact laws in §*Laws* below hold them equal).

## Versioning

The document opens with `sidc <major>`. The current major is **0**
(`stext.SIDC_VERSION`). Majors are incompatible: a reader accepts only its own
major and rejects any other with `stext.SidcVersionError` (a `ValueError`
subclass) — so an unknown future-version document fails cleanly at the header
rather than mis-parsing later constructs. Backward-compatible growth within a
major is additive (new optional header directives a reader may ignore); any
change that alters the meaning of existing constructs bumps the major.

## Grammar (EBNF)

Lexical: `hex = "$" , hexdigit , { hexdigit }`. A hex literal's **width in
bytes** is `max(1, digits / 2)` (`$05` is 1 byte, `$0005`/`$1234` are 2). `ws`
is spaces; a `;` begins a comment to end-of-line; blank lines are ignored.
Indentation is insignificant (the emitter indents block bodies two spaces for
readability only).

```ebnf
document    = version , { header } , image , { proc } ;
version     = "sidc" , ws , integer , newline ;

header      = init | play | subtune | dispatch ;
init        = "init" , ws , hex , newline ;          (* required *)
play        = "play" , ws , hex , newline ;          (* required *)
subtune     = "subtune" , ws , integer , newline ;   (* optional; default 0 *)
dispatch    = "dispatch" , ws , hex , ":" , { ws , hex } , newline ;
                                        (* proven opcode set for an SMC cell *)

image       = "image" , ws , "{" , newline ,
              { ws , hex , ":" , { ws , bytehex } , newline } ,   (* <=16 / row *)
              "}" , newline ;                        (* only non-zero cells *)

proc        = "proc" , ws , hex , "{" , newline ,
              { block } ,
              "}" , newline ;
block       = label , newline , { stmt , newline } , [ term , newline ] ;
label       = hex , [ "/" , bytehex ] ;   (* "/op" iff pc is a dispatch cell *)

stmt        = cyc | pen | load | store | regset ;
cyc         = "@" , integer ;                         (* cycle cost, >=1 *)
pen         = ( "@x" | "@xi" ) , "(" , expr , "," , ws , expr , ")" ;
                                        (* indexed / (ind),Y page-cross penalty *)
load        = uni , ws , "=" , ws , "mem[" , expr , "]" ;
store       = "mem[" , expr , "]" , ws , "=" , ws , expr ;
regset      = reg , ws , "=" , ws , expr ;            (* out-expr != identity *)

term        = goto | branch | cgoto | igoto | call | ret ;
goto        = "goto" , ws , hex ;                     (* elided iff == next pc *)
cgoto       = "goto" , ws , "(" , expr , ")" ;        (* computed jump *)
branch      = ( "if" | "ifnot" ) , ws , expr , ws , "goto" , ws , target ,
              [ ws , "else" , ws , hex ] ;            (* else elided iff next *)
igoto       = "igoto" , ws , ( hex | "(" , expr , ")" ) ;   (* jmp (indirect) *)
call        = "call" , ws , target , ws , "ret" , ws , hex ;
ret         = "ret" ;
target      = hex | "(" , expr , ")" ;   (* "(expr)" is a proven dynamic target *)

expr        = atom ;
atom        = hex | reg | uni | mem | zext | carry | group ;
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
```

Node/width correspondences (the `expr` algebra of `deity_informant.expr`):
`INT_SUB`'s right operand is never a constant (a constant subtrahend
canonicalises to `INT_ADD` of its negation); `compare`/`carry` results are one
byte and carry no `:n`; `mem` loads are one byte; a lone `-$k` inside an
`addsub` is the two's-complement of an added constant.

## Laws

- **Canonical fixpoint.** `dumps` is idempotent after one round trip:
  `dumps(loads(dumps(m))) == dumps(m)` for every model `m`. Emission elides a
  `goto`/`else` to the textually-next block and coalesces adjacent `@cyc`
  costs, so the fixpoint is the canonical form.
- **Round-trip identity.** On a canonical model, `loads(dumps(m))` reproduces
  `m` structurally (image, header, dispatch sets, and every block's events,
  terminator and register out-expressions).
- **Executable equivalence.** The parsed `TextModel` drives the same `Walker`
  as the in-memory `Model`; both reproduce the original's cycle-stamped
  `(cycle, reg, value)` write log and end memory bit-exact (Gate C).

Both serialisation laws are property-tested over generated models
(`tests/test_sidc_language.py`), not only corpus samples.
