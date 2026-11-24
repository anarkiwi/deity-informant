//! Pointer cells and their regions (the last-entry rule, [`crate::Opts::overrun_last`]).
//!
//! A *pointer cell* is a pair of bytes an access forms its address from: the zero-page pair of
//! a `(zp),Y` or `(zp,X)` access, or the operand bytes of an absolute instruction the program
//! stores. Its value comes, along the definitions reaching the access ([`crate::pdefs`]), from
//! two bytes stored together: words zipped from two tables at one index (each plus a base the
//! code adds), constants, the words a pointer cell held where it was copied (a copy into
//! another pair, an operand or a stack of pointers indexed by a byte), the bytes read through
//! another pointer, or itself plus an offset (a 16-bit add, `INC lo; BNE; INC hi`): what it
//! holds when the entry starts is what init left and what any store of it leaves. The words
//! of the tables and constants it may hold are its *bases*; an add keeps the bases, a copy
//! takes the source's, a read through a pointer takes that pointer's.
//!
//! A pointer's *region* is the block of read-only data (a run of data bytes no instruction,
//! cell or variable of the entries lies in) holding every base. An access through it reads
//! the region at the pointer plus its index: past the region's end the last entry (the
//! region's last byte), as a table read past its extent does, and a store there writes
//! nothing. The address the pointer holds is not bounded, its access is: by construction.
//! A pointer whose bases lie in no block or in several has no region, and is reported. A
//! zero high byte is a null pointer, an idle voice's, taken not to be read through.
//!
//! A pointer is *exact* at an access where it holds one of its bases on every path: each store
//! of its bytes writes both in one straight run of code (no entry between them), from a
//! constant, a zipped table word or a copy of an exact pointer, and it never adds to itself,
//! is read through another pointer, is null or holds a word outside its region. Its access
//! then reads the region at a base plus its index: no address past the last base plus the
//! index's greatest value ([`Regions::exact`]).

use crate::analysis::{operand_bytes, Access, Graph, Iv, Var};
use crate::objects::{mem_src, reg_src, writes, RegSrc};
use crate::pdefs::{Flows, Loc, Local};
use crate::ptr::StoreKey;
use di_pcode::Template;
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::BTreeSet;

/// The most values a register's definitions are combined over.
const CAP: usize = 64;

/// What a register or byte holds before an instruction start, from its definitions.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
enum Sym {
    Imm(u8),
    /// The byte at an address, read at an instruction start.
    Mem(u16, u16),
    /// A load at `at` from constant `base` plus register `r`, `ix` the definitions of `r`
    /// reaching it: two loads of one `ix` read at one index value.
    Tab {
        base: u16,
        at: u16,
        r: usize,
        ix: Vec<Option<u16>>,
    },
    /// A byte read through the pointer cell at `q` (its low byte) at an instruction start.
    Data(u16, u16),
    /// An add with carry of two values (`CLC; ADC`, the high byte's carry the low byte's).
    Add(Box<Sym>, Box<Sym>),
    Other,
}

/// A pointer cell: a pair (its low byte's address), or a stack of pointers: two arrays at
/// constant bases indexed by a byte, at the index values given.
#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
enum Cell {
    Pair(u16),
    Stack(u16, u16, Vec<u32>),
}

impl Cell {
    /// The addresses of its low and high bytes.
    fn bytes(&self) -> (Vec<u32>, Vec<u32>) {
        match self {
            Cell::Pair(q) => (vec![*q as u32], vec![q.wrapping_add(1) as u32]),
            Cell::Stack(l, h, ks) => (
                ks.iter().map(|k| (*l as u32 + k) & 0xffff).collect(),
                ks.iter().map(|k| (*h as u32 + k) & 0xffff).collect(),
            ),
        }
    }
}

/// What a store of a pointer cell's high byte writes into the cell.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Kind {
    /// The word zipped from the low and high tables at one index, read at `lo_at` and `hi_at`,
    /// plus each of `offs`.
    Zip {
        lo: u16,
        hi: u16,
        lo_at: u16,
        hi_at: u16,
        offs: Vec<u32>,
    },
    Const(u32),
    /// The word the pair at this low byte holds.
    Copy(u16),
    /// Itself plus an offset.
    Add,
    /// The bytes read through the pair at this low byte.
    Data(u16),
    Other,
}

/// A store of a pointer pair's high byte: its instruction start, its low byte's store in one
/// straight run with it, and what it writes.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Store {
    pub hi: u16,
    pub lo: Option<u16>,
    pub kind: Kind,
}

/// An access through a pointer pair: whether its pointer is exact, the definitions of the
/// low and high bytes reaching it (`None`: what the entry holds, which every store may leave),
/// whether a word init leaves is one of its bases, and the least and greatest index.
#[derive(Clone, Debug)]
pub struct Through {
    pub key: StoreKey,
    pub exact: bool,
    pub lo: Vec<Option<u16>>,
    pub hi: Vec<Option<u16>>,
    pub init: bool,
    pub y: (u8, u8),
}

/// A pointer pair: its low byte, its region, the stores of its high and low bytes (each low
/// byte's store with its high byte's mate), its accesses, and every other read of its bytes
/// with the definitions of the low and high byte reaching it.
#[derive(Clone, Debug)]
pub struct PCell {
    pub q: u16,
    pub region: Iv,
    pub stores: Vec<Store>,
    pub los: Vec<(u16, Option<u16>)>,
    pub accesses: Vec<Through>,
    pub reads: Vec<crate::ir::PtrRead>,
    /// The definitions of the low and high byte reaching the entries' returns out of the
    /// program: what the next entry starts with.
    pub exits: (Vec<Option<u16>>, Vec<Option<u16>>),
}

/// The region of each pointer access and why each other pointer access has none.
#[derive(Clone, Debug, Default)]
pub struct Regions {
    /// Per access, its region `[lo, hi]`.
    pub of: FxHashMap<StoreKey, Iv>,
    /// Per instruction start of a pointer access with no region, why.
    pub notes: Vec<(u16, String)>,
    /// Per access whose pointer is exact, its least and greatest base.
    pub exact: FxHashMap<StoreKey, (u32, u32)>,
    /// The pairs one access reads through whose accesses all have one region.
    pub cells: Vec<PCell>,
}

/// What one access's pointer may hold: its bases, and why it has none where it has none.
#[derive(Default)]
struct Found {
    /// Per base, the store giving it (`None`: init's).
    words: std::collections::BTreeMap<u32, Option<u16>>,
    fail: Option<String>,
    /// Whether the pointer may hold a word other than its bases (it is not exact).
    loose: bool,
}

struct Cx<'a> {
    g: &'a Graph,
    roots: &'a [u16],
    /// Per register every entry declares, the values they declare.
    declared: FxHashMap<usize, Vec<u8>>,
    pcs: Vec<u16>,
    flows: Flows<'a>,
    free: &'a FxHashMap<u16, Vec<u8>>,
    locs: FxHashMap<Loc, (Local, FxHashMap<u16, FixedBitSet>)>,
    /// The bytes some store of the entries writes (pointer accesses aside).
    stored: FixedBitSet,
    /// Per store (its instruction start and address interval), the bytes it writes, ascending.
    writes: FxHashMap<(u16, u32, u32), Vec<u32>>,
    seen: FxHashSet<(Cell, Option<u16>)>,
    out: Found,
    /// The store whose value is being classified (`None`: init's).
    by: Option<u16>,
    /// Per instruction start, how many starts flow into it (an entry counting one).
    preds: FxHashMap<u16, u32>,
    /// Per pair (its low byte) and store of its high byte, what each value it stores writes.
    kinds: FxHashMap<(u16, u16), Vec<Kind>>,
}

/// Whether access `a` of variant `v` at `pc` goes through a pointer cell: under the rule it
/// stays in its region, a data block, so it writes no pointer cell.
fn through(g: &Graph, pc: u16, v: &Var, a: &Access) -> bool {
    let t = Template::of(v.op);
    let celled = t.len == 3
        && (g.cells.contains(pc.wrapping_add(1) as usize)
            || g.cells.contains(pc.wrapping_add(2) as usize));
    a.addr.value().is_none() && (a.ptr.is_some() || celled)
}

impl<'a> Cx<'a> {
    fn loc(&mut self, loc: Loc) -> &(Local, FxHashMap<u16, FixedBitSet>) {
        let (g, pcs, flows, roots) = (self.g, &self.pcs, &self.flows, self.roots);
        self.locs.entry(loc).or_insert_with(|| {
            let skip = |pc: u16, v: &Var, a: &Access| through(g, pc, v, a);
            let l = Local::with(g, pcs, loc, &skip);
            let ins = l.reaching(flows, roots);
            (l, ins)
        })
    }

    /// The writers (`None`: the entry's value) of byte `b` of `loc` reaching past the returns
    /// out of the program.
    fn exits(&mut self, loc: Loc, b: u8) -> Vec<Option<u16>> {
        let ends = self.flows.exits(self.roots);
        let (l, ins) = self.loc(loc);
        let mut v: Vec<Option<u16>> = (ends.iter())
            .filter_map(|pc| ins.get(pc).map(|i| l.transfer(*pc, i)))
            .flat_map(|o| o.ones().map(|d| l.defs[d]).collect::<Vec<_>>())
            .filter(|d| d.1 == b)
            .map(|d| d.2)
            .collect();
        v.sort_unstable();
        v.dedup();
        v
    }

    /// The writers (`None`: the entry's value) of byte `b` of `loc` reaching `at`.
    fn defs(&mut self, loc: Loc, b: u8, at: u16) -> Vec<Option<u16>> {
        let (l, ins) = self.loc(loc);
        let Some(i) = ins.get(&at) else {
            return Vec::new();
        };
        let mut v: Vec<Option<u16>> = (i.ones().map(|d| l.defs[d]))
            .filter(|d| d.1 == b)
            .map(|d| d.2)
            .collect();
        v.sort_unstable();
        v.dedup();
        v
    }

    /// The values register `r` holds before `at`.
    fn reg(&mut self, r: usize, at: u16) -> Vec<Sym> {
        let mut out = Vec::new();
        for d in self.defs(Loc::Reg(r as u8, 0), 0, at) {
            let Some(d) = d else {
                // An entry's register: the values every entry declares for it.
                match self.declared.get(&r) {
                    Some(vs) => out.extend(vs.iter().map(|&v| Sym::Imm(v))),
                    None => out.push(Sym::Other),
                }
                continue;
            };
            let vs = self.g.vars.get(&d).cloned().unwrap_or_default();
            for v in vs.iter().filter(|v| writes(v.op, r)) {
                for s in self.def(r, d, v) {
                    if !out.contains(&s) && out.len() < CAP {
                        out.push(s);
                    }
                }
            }
        }
        out
    }

    /// The values variant `v` at `d` leaves in register `r`.
    fn def(&mut self, r: usize, d: u16, v: &Var) -> Vec<Sym> {
        match reg_src(v.op) {
            RegSrc::Imm => vec![self.imm(d)],
            RegSrc::Copy(q) => self.reg(q, d),
            RegSrc::Load => vec![self.load(d, v)],
            // ADC: the sum of `A` and the operand; any other operation is no word's.
            RegSrc::Alu if v.op >> 5 == 3 && r == di_pcode::reg::A => {
                let b = match v.acc.iter().any(|a| !a.store && !a.dummy) {
                    true => self.load(d, v),
                    false => self.imm(d),
                };
                let a = self.reg(di_pcode::reg::A, d);
                a.into_iter()
                    .map(|a| Sym::Add(Box::new(a), Box::new(b.clone())))
                    .collect()
            }
            _ => vec![Sym::Other],
        }
    }

    fn imm(&self, d: u16) -> Sym {
        let a = d.wrapping_add(1);
        match self.g.cells.contains(a as usize) {
            true => Sym::Other,
            false => Sym::Imm(self.g.img[a as usize]),
        }
    }

    /// What the load of variant `v` at `d` reads.
    fn load(&mut self, d: u16, v: &Var) -> Sym {
        let g = self.g;
        // The data load: through its pointer where it has one, not the pointer's fetch.
        let loads = || v.acc.iter().filter(|a| !a.store && !a.dummy);
        let Some(a) = loads()
            .find(|a| a.ptr.is_some())
            .or_else(|| loads().next_back())
        else {
            return Sym::Other;
        };
        if let Some(p) = a.ptr {
            return match p.lo.and_then(Iv::value) {
                Some(l) => Sym::Data(l as u16, d),
                None => Sym::Other,
            };
        }
        let t = Template::of(v.op);
        let mut b = operand_bytes(&g.img, &g.cells, d, t.len);
        // An operand only init writes holds its value: one per run, alike in every run.
        for (k, x) in b
            .iter_mut()
            .enumerate()
            .take(t.len.saturating_sub(1) as usize)
        {
            let at = d.wrapping_add(1 + k as u16) as u32;
            if x.is_none() && !self.stored.contains(at as usize) {
                let vs = self.init(at);
                *x = vs.iter().all(|&y| y == vs[0]).then_some(vs[0]);
            }
        }
        if t.len == 3 && b.iter().any(Option::is_none) {
            return Sym::Data(d.wrapping_add(1), d);
        }
        if let Some(x) = a.addr.value() {
            return Sym::Mem(x as u16, d);
        }
        match (crate::dispatch::index_reg(t), b) {
            (Some(r), [Some(l), Some(h)]) if t.len == 3 => Sym::Tab {
                base: u16::from_le_bytes([l, h]),
                at: d,
                r,
                ix: self.defs(Loc::Reg(r as u8, 0), 0, d),
            },
            (Some(r), [Some(l), _]) if t.len == 2 => Sym::Tab {
                base: l as u16,
                at: d,
                r,
                ix: self.defs(Loc::Reg(r as u8, 0), 0, d),
            },
            _ => Sym::Other,
        }
    }

    /// The pointer may hold a word other than its bases.
    fn lose(&mut self) {
        self.out.loose = true;
    }

    fn fail(&mut self, why: String) {
        self.out.fail.get_or_insert(why);
    }

    /// The values the host's init leaves in byte `x`, one per run (the image's where it is no
    /// variable of init's).
    fn init(&self, x: u32) -> Vec<u8> {
        match self.free.get(&(x as u16)) {
            Some(v) if !v.is_empty() => v.clone(),
            _ => vec![self.g.img[x as usize & 0xffff]],
        }
    }

    /// The words init leaves in a pair of bytes, run by run.
    fn init_words(&self, lo: u32, hi: u32) -> Vec<u32> {
        let (l, h) = (self.init(lo), self.init(hi));
        let n = l.len().max(h.len());
        (0..n)
            .map(|i| l[i.min(l.len() - 1)] as u32 | (h[i.min(h.len() - 1)] as u32) << 8)
            .collect()
    }

    fn word(&mut self, w: u32) {
        match w >> 8 != 0 {
            true => _ = self.out.words.entry(w & 0xffff).or_insert(self.by),
            false => self.lose(),
        }
    }

    /// The store of the low bytes `los` running with store `s` in one straight run of code:
    /// each instruction from the first to the other flowing to the next alone, and each after
    /// the first entered from the one before alone.
    fn mate(&self, s: u16, los: &[u32]) -> Option<u16> {
        let run = |a: u16, b: u16| {
            let mut pc = a;
            for _ in 0..8 {
                let next = match self.flows.succs(pc)[..] {
                    [n] if self.preds.get(&n) == Some(&1) => n,
                    _ => return false,
                };
                if next == b {
                    return true;
                }
                pc = next;
            }
            false
        };
        let ms: Vec<u16> = (self.writers(los).into_iter())
            .filter(|&m| m != s && (run(m, s) || run(s, m)))
            .collect();
        match ms[..] {
            [m] => Some(m),
            _ => None,
        }
    }

    /// The values the store of `s`'s low byte mate stores, none where it has no mate.
    fn mated(&mut self, s: u16, los: &[u32]) -> Vec<Sym> {
        match self.mate(s, los) {
            Some(m) => self.stored(m, los).into_iter().flatten().collect(),
            None => Vec::new(),
        }
    }

    /// The bases of the pointer cell `c` at `at` (`None`: wherever the entries run).
    fn visit(&mut self, c: Cell, at: Option<u16>) {
        if !self.seen.insert((c.clone(), at)) {
            return;
        }
        let Cell::Pair(q) = c else {
            return self.entry(&c);
        };
        let Some(at) = at else {
            return self.entry(&c);
        };
        for d in self.defs(Loc::Word(q), 1, at) {
            match d {
                Some(s) => self.store(&c, s),
                None => self.visit(c.clone(), None),
            }
        }
    }

    /// What `c` holds when an entry starts: what init left, or what a store of it left.
    fn entry(&mut self, c: &Cell) {
        let (los, his) = c.bytes();
        let by = self.by.take();
        for (&l, &h) in los.iter().zip(&his) {
            for w in self.init_words(l, h) {
                self.word(w);
            }
        }
        self.by = by;
        for s in self.writers(&his) {
            self.store(c, s);
        }
    }

    /// Whether store `a` at `pc` may write one of the bytes `xs`: one of its address set's,
    /// else of its interval.
    fn hits(&self, pc: u16, a: &Access, xs: &[u32]) -> bool {
        let w = self.writes.get(&(pc, a.addr.lo, a.addr.hi));
        w.is_some_and(|w| xs.iter().any(|x| w.binary_search(x).is_ok()))
    }

    /// The instruction starts whose stores may write one of the bytes `xs`.
    fn writers(&self, xs: &[u32]) -> Vec<u16> {
        let g = self.g;
        let mut out: Vec<u16> = (self.pcs.iter().copied())
            .filter(|&pc| {
                g.vars[&pc].iter().any(|v| {
                    v.acc.iter().any(|a| {
                        a.store
                            && !a.dummy
                            && !a.back
                            && !through(g, pc, v, a)
                            && self.hits(pc, a, xs)
                    })
                })
            })
            .collect();
        out.dedup();
        out
    }

    /// The stored values of the instruction at `s` into one of the bytes `xs`.
    fn stored(&mut self, s: u16, xs: &[u32]) -> Vec<Option<Sym>> {
        let vs = self.g.vars.get(&s).cloned().unwrap_or_default();
        let mut out = Vec::new();
        for v in &vs {
            let hits = (v.acc.iter()).any(|a| a.store && !a.dummy && self.hits(s, a, xs));
            if !hits {
                continue;
            }
            match mem_src(v.op) {
                Some(Some(r)) => out.extend(self.reg(r, s).into_iter().map(Some)),
                // A read-modify-write of the byte itself: the pointer plus an offset.
                Some(None) => out.push(None),
                None => out.push(Some(Sym::Other)),
            }
        }
        out
    }

    /// The bases a store at `s` of the high byte of `c` gives it.
    fn store(&mut self, c: &Cell, s: u16) {
        let (los, his) = c.bytes();
        let by = self.by.replace(s);
        let mut kinds = Vec::new();
        for v in self.stored(s, &his) {
            let Some(v) = v else {
                self.lose();
                kinds.push(Kind::Add);
                continue;
            };
            kinds.push(self.high(c, s, v, &los, &his));
        }
        self.by = by;
        if let Cell::Pair(q) = c {
            self.kinds.entry((*q, s)).or_insert(kinds);
        }
    }

    /// The bases high byte value `v` stored at `s` into `c` gives it, and what it writes.
    fn high(&mut self, c: &Cell, s: u16, v: Sym, los: &[u32], his: &[u32]) -> Kind {
        let mine = |x: u16| his.contains(&(x as u32));
        match v {
            Sym::Mem(x, _) if mine(x) => {
                self.lose();
                Kind::Add
            }
            Sym::Mem(x, l) => {
                let copied = |m: &Sym| matches!(m, Sym::Mem(y, _) if *y == x.wrapping_sub(1));
                let mated = self.mated(s, los);
                let whole = !mated.is_empty() && mated.iter().all(copied);
                if !whole {
                    self.lose()
                }
                self.copy(x, l);
                match whole {
                    true => Kind::Copy(x.wrapping_sub(1)),
                    false => Kind::Other,
                }
            }
            Sym::Add(a, b) => match (*a, *b) {
                (Sym::Mem(x, _), _) | (_, Sym::Mem(x, _)) if mine(x) => {
                    self.lose();
                    Kind::Add
                }
                (Sym::Imm(h), Sym::Imm(k)) => {
                    self.constant(s, h.wrapping_add(k), los);
                    Kind::Other
                }
                (Sym::Mem(x, l), Sym::Imm(_)) | (Sym::Imm(_), Sym::Mem(x, l)) => {
                    self.lose();
                    self.copy(x, l);
                    Kind::Other
                }
                (t @ Sym::Tab { .. }, base) | (base, t @ Sym::Tab { .. }) => {
                    self.zip(c, s, t, Some(base), los)
                }
                (Sym::Data(q, l), _) | (_, Sym::Data(q, l)) => {
                    self.lose();
                    self.visit(Cell::Pair(q), Some(l));
                    Kind::Other
                }
                _ => {
                    self.fail(format!("high byte from an add at ${s:04X}"));
                    Kind::Other
                }
            },
            t @ Sym::Tab { .. } => self.zip(c, s, t, None, los),
            Sym::Data(q, l) => {
                self.lose();
                self.visit(Cell::Pair(q), Some(l));
                Kind::Data(q)
            }
            Sym::Imm(h) => self.constant(s, h, los),
            Sym::Other => {
                self.fail(format!("high byte from an operation at ${s:04X}"));
                Kind::Other
            }
        }
    }

    /// A constant high byte `h`: with the constant the low byte's store adds (a word of the
    /// code plus an offset, `ADC #lo(tab)` beside `LDA #hi(tab); ADC #0`), that word; else
    /// its page. A zero page is a null pointer.
    fn constant(&mut self, s: u16, h: u8, los: &[u32]) -> Kind {
        if h == 0 {
            self.lose();
            return Kind::Other;
        }
        let imm = |v: Sym| match v {
            Sym::Imm(l) => Some(l),
            Sym::Add(a, b) => match (*a, *b) {
                (Sym::Imm(l), _) | (_, Sym::Imm(l)) => Some(l),
                _ => None,
            },
            _ => None,
        };
        // The low byte its mate stores, else one some store of the low byte adds.
        let mated: Vec<Option<u8>> = self.mated(s, los).into_iter().map(imm).collect();
        let lo = match mated.first() {
            Some(&Some(l)) => Some(l),
            _ => self.partner(los).into_iter().find_map(imm),
        };
        let whole = lo.is_some() && !mated.is_empty() && mated.iter().all(|m| *m == lo);
        if !whole {
            self.lose()
        }
        match lo {
            Some(l) => self.word(l as u32 | (h as u32) << 8),
            None => {
                self.word((h as u32) << 8);
                self.word((h as u32) << 8 | 0xff);
            }
        }
        // A whole constant: both bytes immediates, no carry or offset between them.
        let plain = self.mated(s, los).iter().all(|m| matches!(m, Sym::Imm(_)));
        match (whole && plain, lo) {
            (true, Some(l)) => Kind::Const(l as u32 | (h as u32) << 8),
            _ => Kind::Other,
        }
    }

    /// A copy of the high byte at `x` read at `l`: the pair it is the high byte of.
    fn copy(&mut self, x: u16, l: u16) {
        self.visit(Cell::Pair(x.wrapping_sub(1)), Some(l));
    }

    /// The values the stores of the low bytes `los` write.
    fn partner(&mut self, los: &[u32]) -> Vec<Sym> {
        let mut out = Vec::new();
        for s in self.writers(los) {
            out.extend(self.stored(s, los).into_iter().flatten());
        }
        out
    }

    /// The index values a load at `at` from `base` reads with register `r`, each address
    /// held to the load's table: past it, its last entry ([`crate::tables`]).
    fn indexed(&self, at: u16, base: u16, r: usize) -> Vec<u32> {
        let g = self.g;
        let ks: Vec<u32> = match g.regs.get(&at) {
            Some(x) => crate::vsa::Bits::from_words(x[r]).values().collect(),
            None => (0..0x100).collect(),
        };
        let t = (g.vars.get(&at).into_iter().flatten())
            .flat_map(|v| (0..v.acc.len()).map(move |ai| (at, v.op, ai)))
            .find_map(|k| g.owned.get(&k));
        ks.into_iter()
            .map(|k| {
                let a = base as u32 + k;
                match t {
                    Some(t) if a > t.hi => t.hi + 1 - t.stride + (a - t.lo) % t.stride,
                    _ => a,
                }
            })
            .collect()
    }

    /// The bases of a high byte read from table `t` (plus `base`) zipped with the low byte
    /// its partner store reads at the same index value.
    fn zip(&mut self, c: &Cell, s: u16, t: Sym, base: Option<Sym>, los: &[u32]) -> Kind {
        let Sym::Tab {
            base: th,
            at,
            r,
            ix,
        } = t
        else {
            return Kind::Other;
        };
        let his = self.indexed(at, th, r);
        // A table of pointers the entries write is a stack of pointer cells.
        if his
            .iter()
            .any(|&a| self.stored.contains(a as usize & 0xffff))
        {
            let tab = |v: Sym| match v {
                Sym::Tab {
                    base, r: q, ix: i, ..
                } if q == r && alike(&i, &ix) => Some(base),
                _ => None,
            };
            let lo = self.partner(los).into_iter().find_map(tab);
            let mated: Vec<Option<u16>> = self.mated(s, los).into_iter().map(tab).collect();
            if mated.is_empty() || mated.iter().any(|m| *m != lo) {
                self.lose()
            }
            match lo {
                Some(tl) => {
                    let ks: Vec<u32> = his.iter().map(|a| a - th as u32).collect();
                    self.visit(Cell::Stack(tl, th, ks), None);
                }
                None => self.fail(format!("stack of pointers with no low bytes at ${s:04X}")),
            }
            return Kind::Other;
        }
        let pick = |v: &Sym| match v {
            Sym::Tab {
                base,
                r: q,
                ix: i,
                at,
                ..
            } if *q == r && alike(i, &ix) => Some((*base, *at, None)),
            Sym::Add(a, b) => match (&**a, &**b) {
                (
                    Sym::Tab {
                        base,
                        r: q,
                        ix: i,
                        at,
                        ..
                    },
                    o,
                )
                | (
                    o,
                    Sym::Tab {
                        base,
                        r: q,
                        ix: i,
                        at,
                        ..
                    },
                ) if *q == r && alike(i, &ix) => Some((*base, *at, Some(o.clone()))),
                _ => None,
            },
            _ => None,
        };
        let parts = self.partner(los);
        let picked = parts.iter().find_map(pick);
        let mated: Vec<_> = self.mated(s, los).iter().map(pick).collect();
        let whole = !mated.is_empty() && mated.iter().all(|m| *m == picked);
        if picked.is_some() && !whole {
            self.lose()
        }
        let Some((tl, lat, lbase)) = picked else {
            let lo = match c {
                Cell::Pair(q) if self.writers(&[*q as u32]).is_empty() => {
                    Some(self.g.img[*q as usize])
                }
                _ => None,
            };
            match lo {
                Some(l) => {
                    for &a in &his {
                        self.word(l as u32 | (self.g.img[a as usize & 0xffff] as u32) << 8);
                    }
                }
                None => self.fail(format!(
                    "high byte at ${s:04X} from a table with no low byte"
                )),
            }
            return Kind::Other;
        };
        let offs = match (lbase, base) {
            (None, None) => vec![0],
            (Some(l), Some(h)) => self.bases(&l, &h),
            _ => {
                self.fail(format!(
                    "one byte of the pointer stored at ${s:04X} adds a base"
                ));
                return Kind::Other;
            }
        };
        let los = self.indexed(lat, tl, r);
        let img = &self.g.img;
        let ws: Vec<u32> = (los.iter().zip(&his))
            .map(|(&l, &h)| {
                img[l as usize & 0xffff] as u32 | (img[h as usize & 0xffff] as u32) << 8
            })
            .collect();
        for w in ws {
            for &o in &offs {
                self.word((w + o) & 0xffff);
            }
        }
        match whole {
            true => Kind::Zip {
                lo: tl,
                hi: th,
                lo_at: lat,
                hi_at: at,
                offs,
            },
            false => Kind::Other,
        }
    }

    /// The base words two bytes added to a table's word may hold: constants, or bytes holding
    /// init's values (run by run, where the entries store neither) and the constants the
    /// entries store into them (every pair of their values).
    fn bases(&mut self, l: &Sym, h: &Sym) -> Vec<u32> {
        let ran =
            |s: &Sym, cx: &Self| matches!(s, Sym::Mem(x, _) if cx.stored.contains(*x as usize));
        let paired = !ran(l, self) && !ran(h, self);
        match (self.consts(l), self.consts(h)) {
            (Some(a), Some(b)) if paired => {
                let n = a.len().max(b.len());
                (0..n)
                    .map(|i| a[i.min(a.len() - 1)] as u32 | (b[i.min(b.len() - 1)] as u32) << 8)
                    .collect()
            }
            (Some(a), Some(b)) => {
                let mut ws: Vec<u32> = (a.iter())
                    .flat_map(|&x| b.iter().map(move |&y| x as u32 | (y as u32) << 8))
                    .collect();
                ws.sort_unstable();
                ws.dedup();
                ws
            }
            _ => {
                self.fail("a base the entries write from no constant".into());
                Vec::new()
            }
        }
    }

    /// The values byte value `s` may hold where they are constants: an immediate, or a byte
    /// holding init's values (run by run) and the constants the entries store into it.
    fn consts(&mut self, s: &Sym) -> Option<Vec<u8>> {
        let x = match s {
            Sym::Imm(v) => return Some(vec![*v]),
            Sym::Mem(x, _) => *x as u32,
            _ => return None,
        };
        let mut vals = self.init(x);
        if self.stored.contains(x as usize) {
            for w in self.writers(&[x]) {
                for v in self.stored(w, &[x]) {
                    match v {
                        Some(Sym::Imm(k)) if vals.len() < CAP => vals.push(k),
                        v => {
                            self.fail(format!("a base stored at ${w:04X} from {v:?}"));
                            return None;
                        }
                    }
                }
            }
        }
        Some(vals)
    }
}

/// Whether two loads whose index register has the definitions `a` and `b` read at one
/// index value: the definitions of one are among the other's (no write of the register
/// between them on a path through both).
fn alike(a: &[Option<u16>], b: &[Option<u16>]) -> bool {
    a.iter().all(|d| b.contains(d)) || b.iter().all(|d| a.contains(d))
}

/// Per store of `g` but through a pointer cell, keyed by its instruction start and address
/// interval, the bytes it writes: its one address, its address set, its operand plus each
/// value its index holds or, where the index may hold any byte, its own table ([`crate::tables`],
/// else up to the next operand of an indexed access: each table its extent).
pub(crate) fn written(g: &Graph) -> FxHashMap<(u16, u32, u32), Vec<u32>> {
    let mut bases: Vec<u32> = Vec::new();
    let mut indexed = Vec::new();
    for (&pc, vs) in &g.vars {
        for v in vs {
            let t = Template::of(v.op);
            let r = crate::dispatch::index_reg(t);
            let b = operand_bytes(&g.img, &g.cells, pc, t.len);
            let base = match (t.len, b) {
                (3, [Some(l), Some(h)]) => Some(u16::from_le_bytes([l, h]) as u32),
                (2, [Some(l), _]) => Some(l as u32),
                _ => None,
            };
            if let (Some(r), Some(base)) = (r, base) {
                if v.acc.iter().all(|a| a.ptr.is_none()) {
                    bases.push(base);
                    indexed.push((pc, base, r, t.len));
                }
            }
        }
    }
    bases.sort_unstable();
    bases.dedup();
    let ix: FxHashMap<u16, (u32, usize, u8)> = indexed
        .iter()
        .map(|&(pc, b, r, n)| (pc, (b, r, n)))
        .collect();
    let mut out: FxHashMap<(u16, u32, u32), Vec<u32>> = FxHashMap::default();
    for (&pc, vs) in &g.vars {
        for v in vs {
            let accs = v.acc.iter().enumerate();
            for (ai, a) in accs.filter(|(_, a)| a.store && !a.dummy && !through(g, pc, v, a)) {
                let table = g.owned.get(&(pc, v.op, ai));
                let xs: Vec<u32> = if let Some(x) = a.addr.value() {
                    vec![x]
                } else if let Some(set) = g.addr_sets.get(&(pc, true)) {
                    set.iter().map(|&x| x as u32).collect()
                } else if let Some(&(base, r, n)) = ix.get(&pc) {
                    let held = g.regs.get(&pc).map(|x| crate::vsa::Bits::from_words(x[r]));
                    let wrap = |x: u32| if n == 2 { x & 0xff } else { x & 0xffff };
                    match held.filter(|b| *b != crate::vsa::Bits::ALL) {
                        Some(b) => b.values().map(|k| wrap(base + k)).collect(),
                        None if table.is_some() => {
                            let t = table.unwrap();
                            (t.lo..=t.hi).collect()
                        }
                        None => {
                            let next = bases.iter().find(|&&x| x > base).copied();
                            let end = next.unwrap_or(base + 0x100).min(base + 0x100);
                            (base..end).map(wrap).collect()
                        }
                    }
                } else if a.addr.hi - a.addr.lo < 0x100 {
                    (a.addr.lo..=a.addr.hi).collect()
                } else {
                    Vec::new()
                };
                let e = out.entry((pc, a.addr.lo, a.addr.hi)).or_default();
                e.extend(xs);
                e.sort_unstable();
                e.dedup();
            }
        }
    }
    out
}

/// The pointer cells access `ai` of variant `v` at `pc` forms its address from.
fn cells_of(g: &Graph, pc: u16, v: &Var, a: &Access) -> Vec<u16> {
    if let Some(p) = a.ptr {
        // `(zp,X)`: the pairs at the operand plus each value `X` holds there.
        let xs = (g.regs.get(&pc)).filter(|_| p.lo.and_then(Iv::value).is_none());
        return match (p.lo, xs) {
            (Some(_), Some(x)) => {
                let zp = g.img[pc.wrapping_add(1) as usize] as u32;
                let bits = crate::vsa::Bits::from_words(x[di_pcode::reg::X]);
                bits.values().map(|x| ((zp + x) & 0xff) as u16).collect()
            }
            (Some(l), None) => (l.lo..=l.hi.min(0xff)).map(|q| q as u16).collect(),
            (None, _) => Vec::new(),
        };
    }
    match through(g, pc, v, a) {
        true => vec![pc.wrapping_add(1)],
        false => Vec::new(),
    }
}

/// The regions of the pointer accesses of `g` from `entries` in the blocks `blocks`
/// (`[lo, hi)` runs of read-only data) of the bytes `held` (`[lo, hi)`) the host holds of
/// the program, `free` the values init leaves in its variables.
pub fn regions(
    g: &Graph,
    entries: &[crate::Entry],
    blocks: &[(u32, u32)],
    held: (u32, u32),
    free: &FxHashMap<u16, Vec<u8>>,
) -> Regions {
    let _span = crate::prof::span("pcells");
    let roots: Vec<u16> = entries.iter().map(|e| e.pc).collect();
    let mut declared: FxHashMap<usize, Vec<u8>> = FxHashMap::default();
    for (i, e) in entries.iter().enumerate() {
        for (r, vs) in &e.regs {
            if i == 0 {
                declared.insert(*r, vs.clone());
            } else if let Some(d) = declared.get_mut(r) {
                for &v in vs {
                    if !d.contains(&v) {
                        d.push(v);
                    }
                }
            }
        }
        declared.retain(|r, _| e.regs.iter().any(|x| x.0 == *r));
    }
    let mut pcs: Vec<u16> = g.vars.keys().copied().collect();
    pcs.sort_unstable();
    let writes = written(g);
    let mut stored = FixedBitSet::with_capacity(0x10000);
    stored.extend(writes.values().flatten().map(|&x| x as usize));
    let flows = Flows::new(g);
    let mut preds: FxHashMap<u16, u32> = roots.iter().map(|&r| (r, 1)).collect();
    for &pc in &pcs {
        for n in flows.succs(pc) {
            *preds.entry(n).or_default() += 1;
        }
    }
    let mut cx = Cx {
        g,
        roots: &roots,
        declared,
        pcs: pcs.clone(),
        flows,
        free,
        locs: FxHashMap::default(),
        stored,
        writes,
        seen: FxHashSet::default(),
        out: Found::default(),
        by: None,
        preds,
        kinds: FxHashMap::default(),
    };
    let block = |w: u32| blocks.iter().position(|b| b.0 <= w && w < b.1);
    let mut out = Regions::default();
    // Per pair one access reads through, its accesses and their regions.
    let mut pairs: std::collections::BTreeMap<u16, Vec<(Through, Option<Iv>)>> = Default::default();
    for &pc in &pcs {
        for v in &g.vars[&pc] {
            if crate::analysis::is_stack_op(v.op) {
                continue;
            }
            for (ai, a) in v.acc.iter().enumerate() {
                if a.dummy || a.addr.value().is_some() {
                    continue;
                }
                let qs = cells_of(g, pc, v, a);
                if qs.is_empty() {
                    continue;
                }
                let one = (qs.len() == 1).then_some(qs[0]);
                cx.seen.clear();
                cx.out = Found::default();
                for q in qs {
                    cx.visit(Cell::Pair(q), Some(pc));
                }
                let mut found = std::mem::take(&mut cx.out);
                // A base outside the held bytes would leave the program (the machine model: a
                // table entry naming no data is never followed), and a word init leaves outside
                // every block is no pointer read through (an idle voice's, or the operand the
                // code holds before play first sets it).
                let inside = |w: u32| held.0 <= w && w < held.1;
                let n = found.words.len();
                found
                    .words
                    .retain(|&w, by| inside(w) && (by.is_some() || block(w).is_some()));
                found.loose |= found.words.len() != n;
                let init = found.words.values().any(Option::is_none);
                let why = found.fail.or_else(|| {
                    let bs: BTreeSet<Option<usize>> =
                        found.words.keys().map(|&w| block(w)).collect();
                    match bs.iter().collect::<Vec<_>>()[..] {
                        [] => Some("no base".into()),
                        [Some(b)] => {
                            let (lo, hi) = blocks[*b];
                            out.of.insert((pc, v.op, ai), Iv { lo, hi: hi - 1 });
                            let ws = &found.words;
                            if let (false, Some(&a), Some(&z)) =
                                (found.loose, ws.keys().next(), ws.keys().next_back())
                            {
                                out.exact.insert((pc, v.op, ai), (a, z));
                            }
                            None
                        }
                        _ => Some(
                            format!(
                                "bases {} in {} blocks",
                                hexes(&found.words, block),
                                bs.iter().flatten().count()
                            ) + if bs.contains(&None) {
                                " and outside them"
                            } else {
                                ""
                            },
                        ),
                    }
                });
                if let Some(w) = why {
                    out.notes.push((pc, w));
                }
                if let Some(q) = one {
                    let key = (pc, v.op, ai);
                    let r = crate::dispatch::index_reg(Template::of(v.op));
                    let ys = match (r, g.regs.get(&pc)) {
                        (Some(r), Some(x)) => crate::vsa::Bits::from_words(x[r]).values().collect(),
                        (Some(_), None) => vec![0, 0xff],
                        (None, _) => vec![0],
                    };
                    let y = (
                        *ys.iter().min().unwrap_or(&0) as u8,
                        *ys.iter().max().unwrap_or(&0) as u8,
                    );
                    let t = Through {
                        key,
                        exact: out.exact.contains_key(&key),
                        lo: cx.defs(Loc::Word(q), 0, pc),
                        hi: cx.defs(Loc::Word(q), 1, pc),
                        init,
                        y,
                    };
                    pairs
                        .entry(q)
                        .or_default()
                        .push((t, out.of.get(&key).copied()));
                }
            }
        }
    }
    out.notes.dedup();
    for (q, accs) in pairs {
        let rs: BTreeSet<Option<(u32, u32)>> =
            accs.iter().map(|a| a.1.map(|r| (r.lo, r.hi))).collect();
        let [Some((lo, hi))] = rs.into_iter().collect::<Vec<_>>()[..] else {
            continue;
        };
        let Some(reads) = reads_of(&mut cx, q) else {
            continue;
        };
        let c = Cell::Pair(q);
        let mut stores = Vec::new();
        for s in cx.writers(&[q.wrapping_add(1) as u32]) {
            if !cx.kinds.contains_key(&(q, s)) {
                cx.seen.clear();
                cx.out = Found::default();
                cx.store(&c, s);
            }
            let ks = cx.kinds.get(&(q, s)).cloned().unwrap_or_default();
            let kind = match &ks[..] {
                [k, rest @ ..] if rest.iter().all(|x| x == k) => k.clone(),
                _ => Kind::Other,
            };
            let lo = cx.mate(s, &[q as u32]);
            stores.push(Store { hi: s, lo, kind });
        }
        let los = (cx.writers(&[q as u32]).into_iter())
            .map(|m| (m, stores.iter().find(|s| s.lo == Some(m)).map(|s| s.hi)))
            .collect();
        out.cells.push(PCell {
            q,
            region: Iv { lo, hi },
            stores,
            los,
            accesses: accs.into_iter().map(|a| a.0).collect(),
            reads,
            exits: (cx.exits(Loc::Word(q), 0), cx.exits(Loc::Word(q), 1)),
        });
    }
    out
}

/// The reads of pair `q`'s bytes but through it, each with the definitions of its low and high
/// byte reaching it; None where a read of unknown address may read one.
fn reads_of(cx: &mut Cx, q: u16) -> Option<Vec<crate::ir::PtrRead>> {
    let g = cx.g;
    let (l, h) = (q as u32, q.wrapping_add(1) as u32);
    let mut out = Vec::new();
    for pc in cx.pcs.clone() {
        let (mut lo, mut hi) = (false, false);
        for v in &g.vars[&pc] {
            for a in v.acc.iter().filter(|a| !a.store && !a.dummy) {
                if a.ptr.is_some() || through(g, pc, v, a) {
                    continue;
                }
                if let Some(x) = a.addr.value() {
                    (lo, hi) = (lo || x == l, hi || x == h);
                } else if let Some(set) = g.addr_sets.get(&(pc, false)) {
                    lo |= set.iter().any(|&x| x as u32 == l);
                    hi |= set.iter().any(|&x| x as u32 == h);
                } else if a.addr.lo <= h && l <= a.addr.hi {
                    return None;
                }
            }
        }
        if lo || hi {
            let ld = if lo {
                cx.defs(Loc::Word(q), 0, pc)
            } else {
                Vec::new()
            };
            let hd = if hi {
                cx.defs(Loc::Word(q), 1, pc)
            } else {
                Vec::new()
            };
            out.push((pc, ld, hd));
        }
    }
    Some(out)
}

/// The bases `ws` as text, outside every block first, each with the store giving it.
fn hexes(
    ws: &std::collections::BTreeMap<u32, Option<u16>>,
    block: impl Fn(u32) -> Option<usize>,
) -> String {
    let mut v: Vec<(&u32, &Option<u16>)> = ws.iter().collect();
    v.sort_by_key(|(w, _)| block(**w).is_some());
    let s: Vec<String> = (v.iter().take(8))
        .map(|(w, by)| match by {
            Some(b) => format!("${w:04X}@${b:04X}"),
            None => format!("${w:04X}@init"),
        })
        .collect();
    s.join(",") + if ws.len() > 8 { ",..." } else { "" }
}

/// [`Regions::cells`] as [`crate::ir::Program::ptr_cells`] lists them, the accesses at the
/// instruction starts `live` holds.
pub fn export(cells: &[PCell], live: impl Fn(u16) -> bool) -> Vec<crate::ir::PtrCell> {
    let kind = |k: &Kind| match k {
        Kind::Zip {
            lo,
            hi,
            lo_at,
            hi_at,
            offs,
        } => (
            "zip",
            [*lo as u32, *hi as u32, *lo_at as u32, *hi_at as u32]
                .into_iter()
                .chain(offs.iter().copied())
                .collect(),
        ),
        Kind::Const(w) => ("const", vec![*w]),
        Kind::Copy(q) => ("copy", vec![*q as u32]),
        Kind::Add => ("add", Vec::new()),
        Kind::Data(q) => ("data", vec![*q as u32]),
        Kind::Other => ("other", Vec::new()),
    };
    let mut out: Vec<crate::ir::PtrCell> = Vec::new();
    for c in cells {
        let mut accs: Vec<crate::ir::PtrAccess> = Vec::new();
        let join = |a: &mut Vec<Option<u16>>, b: &[Option<u16>]| {
            a.extend(b.iter().copied());
            a.sort_unstable();
            a.dedup();
        };
        for t in c.accesses.iter().filter(|a| live(a.key.0)) {
            match accs.iter_mut().find(|a| a.0 == t.key.0) {
                Some(a) => {
                    a.1 &= t.exact;
                    join(&mut a.2, &t.lo);
                    join(&mut a.3, &t.hi);
                    a.4 |= t.init;
                    a.5 = (a.5 .0.min(t.y.0), a.5 .1.max(t.y.1));
                }
                None => accs.push((t.key.0, t.exact, t.lo.clone(), t.hi.clone(), t.init, t.y)),
            }
        }
        if accs.is_empty() {
            continue;
        }
        accs.sort_unstable();
        let stores = (c.stores.iter())
            .map(|s| {
                let (n, a) = kind(&s.kind);
                (s.hi, s.lo, n.to_string(), a)
            })
            .collect();
        out.push((
            c.q,
            (c.region.lo, c.region.hi),
            stores,
            c.los.clone(),
            accs,
            c.reads.clone(),
            c.exits.clone(),
        ));
    }
    out
}
