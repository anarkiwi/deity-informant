# The sidprog grammar (generated)

`deity_informant/sidprog.lark` is the ONE grammar of the decompiler's text
layers and the normative definition of both dialects. It is parsed LALR(1) by
`deity_informant.grammar`, the only reader either layer has. The document
header selects the dialect:

- `sidprog <major>` — the cycle-exact language ([sidprog-language.md](sidprog-language.md)).
- `frameprog <major>` — the same language with the cycle-annotation
  productions removed (`CYC`, `CYCT`, `PENTAG`, `code[...]` switch subjects)
  and the frame-level surface added: `state { }` / `inputs { }` header
  sections, named locals, procedure calls with inferred parameters and
  returns, and `for` ranges ([frameprog.md](frameprog.md)).

Everything else — expressions, memrefs, `data { }`/`symbols { }`, loops,
switches, case arms, flow items — is shared: lark templates parameterise the
region productions over the two item alphabets (`sitem`, `fitem`), so a
construct cannot drift between the layers. A block is a run of payload lines
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
the two. Like the width suffix it is a frameprog form; a sidprog document
carrying one is rejected.

A reference — named, indexed, deref or raw — carries an optional `:N` **width suffix**,
absent for the one byte a 6502 access moves and `:2` for the 16-bit forms rung
(d) fuses ([frameprog.md](frameprog.md) §4): `m_0021:2` is the word at `$21`, and
`m_0021:2 = e` stores it. A store's suffix must equal the width of the value
stored, so the width is stated once; the suffix is a frameprog form, and a
sidprog document carrying one is rejected. `state { }` fields are `u8` or `u16`
accordingly.

A `state { }` field may carry a **block extent** — `ptr_0021: u16 in m_7338,
m_7401` — naming the declared data blocks the derefs through that pointer land
in. An extent is a pointer's, so only a scalar `u16` field carries one; each
block is named by its base cell, an alias included, and the blocks are emitted
ascending. The clause sits between the array brackets and `observed`.

A width-2 store may carry the **write order** `hi-first`, which says its two
bytes leave in descending address order: `hi-first sid.v1.freq_lo[y]:2 = e`
writes the high cell before the low one. Absent the word, a word store emits
ascending. The order is a fact about the store rather than about its address, so
rung (d) can merge a pair the program wrote hi-first without resolving the index
it was written through ([frameprog.md](frameprog.md) §7.10.4); `framelog` keeps
write order inside the ctrl/AD/SR and `$19`-`$1C` sections, which is where the
difference is observable. It is a frameprog form on a store, and a sidprog
document carrying one is rejected.

Reserved words are exactly the grammar's literal identifier terminals
(`grammar.keywords()`); `symbols { }` aliases may shadow none of them, nor a
canonical cell name, register or `uN`/`tN`/`rN` slot.

The block below is generated from the grammar file. `tests/test_grammar.py`
fails when it drifts; regenerate with
`SYNC_GRAMMAR_DOC=1 pytest tests/test_grammar.py`.

<!-- BEGIN GENERATED GRAMMAR: deity_informant/sidprog.lark -->
```lark
// One grammar, two dialects. The document header selects the dialect:
//   sidprog N   -- the cycle-exact language (docs/sidprog-language.md)
//   frameprog N -- the same language minus the cycle-annotation productions
//                  (CYC/CYCT/PENTAG, code[] dispatch subjects), plus the
//                  state/inputs header, named locals, procedure calls and
//                  for-ranges (docs/frameprog.md)
// Everything else -- expressions, memrefs, data/symbols sections, loops,
// switches, flow items -- is shared. Parsed LALR(1); templates parameterise
// the shared region productions over the two item alphabets. The width suffix
// and the *ptr[i] deref form are frameprog forms a sidprog document rejects,
// as is trunc1/trunc2 (a width-suffixed local name is the 16-bit local) and the
// hi-first write order of a word store.

start: sidprog_doc
     | frameprog_doc

sidprog_doc: sphead _sheader* image_sec? data_sec? symbols_sec? proc*
frameprog_doc: fphead _fheader* state_sec? data_sec? symbols_sec? sub*
sphead: "sidprog" INT _NL
fphead: "frameprog" INT _NL

_sheader: play | init | subtune | sidinit | dispatch_set
_fheader: play | init | subtune | sidinit | inputs_sec

play: "play" HEX _NL
init: "init" HEX _NL
subtune: "subtune" INT _NL
sidinit: _SIDINIT "{" _NL sidwrite* "}" _NL
sidwrite: HEX "=" HEX _NL
dispatch_set: "dispatch" HEX ":" HEX* _NL
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

state_sec: "state" "{" _NL statedef* "}" _NL
statedef: NAME ":" NAME [array] [statext] [statobs] _NL
array: "[" "]"
statext: "in" NAME ("," NAME)*
statobs: "observed" HEX*

// ---- procedures ----------------------------------------------------------------
proc: "proc" HEX "{" _NL sitem* "}" _NL
sub: NAME "(" params ")" [rets] "{" _NL fitem* "}" _NL
params: (NAME ("," NAME)*)?
rets: "->" NAME ("," NAME)*

?sitem: sblock | loop{sitem} | swgoto{sitem} | swcall{sitem} | opsw_code
?fitem: fblock | loop{fitem} | swgoto{fitem} | swcall{fitem} | opsw_cell | forloop

// ---- shared region productions (parameterised over the item alphabet) ----------
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

opsw_code: [label] "switch" "code" "[" HEX "]" "{" _NL case{sitem}* "}" _NL
opsw_cell: [label] "switch" NAME "{" _NL case{fitem}* "}" _NL
forloop: "for" NAME "in" HEX ".." HEX "{" _NL fitem* "}" _NL

label: HEX ":" _NL
target: HEX -> tgt_static
      | "(" expr ")" -> tgt_dyn

// ---- blocks: payload lines plus the closers that end them ----------------------
sblock: label _sbody?
      | _sbody
_sbody: sline+ _scloser*
      | _scloser+
sline: [CYC] pen _NL -> s_pen
     | [CYC] asg _NL -> s_asg
     | CYC _NL -> s_cyc
pen: PENTAG "(" expr "," expr ")"

fblock: label _fbody?
      | _fbody
_fbody: fline+ _fcloser*
      | _fcloser+
fline: asg _NL -> f_asg
      | _HIFIRST asg _NL -> f_asg_hifirst
      | pcall _NL -> f_pcall_void
      | lvalue ("," NAME)* "=" pcall _NL -> f_pcall_ret
pcall: NAME "(" (expr ("," expr)*)? ")"

_scloser: dynbr | cgoto | igoto | callstmt{sitem} | retline | sif | flowline
_fcloser: dynbr | cgoto | igoto | callstmt{fitem} | fretline | fif | flowline

sif: ifw CYCT expr "{" _NL sitem* selse{sitem} -> sif_body
   | ifw CYCT expr "unobserved" HEX _NL -> sif_front
fif: ifw expr "{" _NL fitem* selse{fitem} -> fif_body
   | ifw expr "unobserved" HEX _NL -> fif_front

dynbr: ifw expr "goto" "(" expr ")" "else" HEX _NL
cgoto: "goto" "(" expr ")" _NL
igoto: "igoto" HEX _NL -> igoto_static
     | "igoto" "(" expr ")" _NL -> igoto_dyn
retline: "ret" _NL
fretline: "ret" (NAME ("," NAME)*)? _NL
flowline: "goto" HEX _NL -> fl_goto
        | "unobserved" HEX _NL -> fl_unobs
        | "continue" _NL -> fl_cont
        | "break" _NL -> fl_brk

ifw: "if" -> w_if
   | "ifnot" -> w_ifnot

// ---- statements and expressions (shared) ---------------------------------------
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
  | "|" -> o_or
  | "^" -> o_xor
  | "&" -> o_and

HEX: /\$[0-9A-Fa-f]+/
HEXBYTES: /[0-9A-F]+/
INT: /\d+/
NAME: /[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_0-9]+)*/
CYC: /@\d+/
CYCT: /@t\d+/
PENTAG: /@xi?/
_SIDINIT.5: "sid-init"
// a word store's own byte-emission order (frameprog form); hyphenated, so no
// NAME can spell it and no symbol alias can shadow it
_HIFIRST.5: "hi-first"
_NL: /(?:;[^\n]*)?\r?\n(?:[ \t]*(?:;[^\n]*)?\r?\n)*/

%ignore /[ \t]+/
```
<!-- END GENERATED GRAMMAR -->
