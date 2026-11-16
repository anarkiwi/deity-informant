//! Why a deopt is taken: the guard, untranslated path or unplanned transfer each `deopt`
//! (and each `BRANCHIND`'s default) stands for, and which guards are implied by others.
//!
//! The emitter tags a deopt with the guards whose conditions it leaves by
//! ([`crate::ir::LOp::why`]: one plus an index into [`crate::ir::Function::exits`], each a
//! list of indices into [`crate::ir::Function::whys`]); a `BRANCHIND` is tagged with its transfer, which a deopt it folds to keeps. [`imply`] then
//! marks the register guards an earlier one, or a constant, already implies on every path.

use crate::analysis::Iv;
use crate::ir::{Block, Function};
use di_pcode::{Opc, Space};

/// What a deopt guards or stands for.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Kind {
    /// An entry register outside its declared values.
    Entry,
    /// An entry's SP other than the one it is specialized to.
    EntrySp,
    /// An entry's bank port registers other than those every call was entered with.
    Port,
    /// An index register past its table's extent ([`Extent`]).
    Index,
    /// An opcode cell holding a value outside its variants.
    Opcode,
    /// A store changing a byte of constant code (`codechk`).
    Code,
    /// A zero-page pointer pair's word inside a zone's window.
    Window,
    /// A pointer outside its object (`objchk`).
    Obj,
    /// An access reaching page 1 outside the bytes it may.
    Stack,
    /// A pointer store reaching page 1 or a volatile page.
    Zone,
    /// A callee returning through an address other than the one its call pushed.
    Return,
    /// A `TXS` restoring an SP other than the saved height's.
    Txs,
    /// A pulled status setting the decimal flag where pulls are taken to clear it.
    Decimal,
    /// An SP-setting instruction leaving SP at no planned height.
    Sp,
    /// A bank configuration with no planned node.
    Bank,
    /// A computed transfer to an address its table does not hold.
    Transfer,
    /// A successor the plan holds no node for: a path the lift does not translate.
    Path,
    /// A `JAM`: an instruction the lift does not translate.
    Jam,
}

impl Kind {
    pub const fn name(self) -> &'static str {
        match self {
            Kind::Entry => "entry",
            Kind::EntrySp => "entry_sp",
            Kind::Port => "port",
            Kind::Index => "index",
            Kind::Opcode => "opcode",
            Kind::Code => "codechk",
            Kind::Window => "window",
            Kind::Obj => "objchk",
            Kind::Stack => "stack",
            Kind::Zone => "zone",
            Kind::Return => "return",
            Kind::Txs => "txs",
            Kind::Decimal => "decimal",
            Kind::Sp => "sp",
            Kind::Bank => "bank",
            Kind::Transfer => "transfer",
            Kind::Path => "path",
            Kind::Jam => "jam",
        }
    }
}

/// The table an index guard keeps a load or store inside.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Of {
    /// A computed transfer's target words.
    Jump,
    /// A computed branch's offsets.
    Branch,
    /// The values a store writes into a code cell.
    Cell,
    /// Data: the words a pointer pair is seeded with, or another table read as values.
    Data,
    /// A store's run inside the loaded program.
    Store,
}

/// What ends a table's extent.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum By {
    /// The first address of another object the code accesses.
    Object,
    /// An instruction byte.
    Code,
    /// The end of the loaded program.
    Loaded,
    /// A target of the transfer the table serves.
    Target,
    /// A byte another column of the same table reads at an earlier index value.
    Column,
    /// The end of the address space.
    Wrap,
}

impl Of {
    pub const fn name(self) -> &'static str {
        match self {
            Of::Jump => "jump",
            Of::Branch => "branch",
            Of::Cell => "cell",
            Of::Data => "data",
            Of::Store => "store",
        }
    }
}

impl By {
    pub const fn name(self) -> &'static str {
        match self {
            By::Object => "object",
            By::Code => "code",
            By::Loaded => "loaded",
            By::Target => "target",
            By::Column => "column",
            By::Wrap => "wrap",
        }
    }
}

/// A table's extent: the column at `base` ends at `end` (exclusive), for the reason `by`;
/// a cell's table, the store writing its values into the cell.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct Extent {
    pub of: Of,
    pub base: u16,
    pub end: u32,
    pub by: By,
    pub store: Option<u16>,
}

/// 256 bits: a set of byte values.
pub type Bits = [u64; 4];

pub const ALL: Bits = [!0; 4];

pub fn bits(vals: impl IntoIterator<Item = u32>) -> Bits {
    let mut b = [0; 4];
    for v in vals {
        b[(v as usize >> 6) & 3] |= 1 << (v & 63);
    }
    b
}

pub fn bits_iv(iv: Iv) -> Bits {
    bits(iv.lo.min(0xff)..=iv.hi.min(0xff))
}

fn subset(a: &Bits, b: &Bits) -> bool {
    a.iter().zip(b).all(|(x, y)| x & !y == 0)
}

/// One guard a deopt stands for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Why {
    pub kind: Kind,
    /// The instruction start guarded (a deopt of an instance function: the start its node
    /// is keyed by).
    pub at: u16,
    /// The register guarded (`A`, `X`, `Y`, `SP` as 0-3; none for other guards).
    pub reg: Option<u8>,
    /// The values it lets pass, as inclusive runs (a register's, an opcode cell's, a
    /// pointer's or an address's).
    pub ok: Vec<(u32, u32)>,
    /// An index guard's tables.
    pub tables: Vec<Extent>,
    /// The pointer pair checked (its low byte) and an object check's object.
    pub pair: Option<u8>,
    pub obj: Option<u32>,
    /// What an object check checks: the pair's pointer at an entry (`"entry"`), at a load
    /// defining it (`"load"`), after a store defining it (`"store"`) or after a walk's anchor
    /// (`"walk"`).
    pub def: Option<&'static str>,
    /// The guarded access's address interval.
    pub addr: Option<Iv>,
    /// The values the analysis finds the register holds at the guard.
    pub seen: Option<Bits>,
    /// What [`imply`] finds the register holds at the guard on every path: the values left
    /// by the register guards and constants before it, the guards (indices into
    /// [`Function::whys`]) that narrowed them, and the entry register it still holds, if any.
    /// A guard is implied when those values are among the ones it lets pass.
    pub held: Option<(Bits, Vec<u32>, Option<u8>)>,
}

impl Why {
    pub fn new(kind: Kind, at: u16) -> Self {
        Why {
            kind,
            at,
            reg: None,
            ok: Vec::new(),
            tables: Vec::new(),
            pair: None,
            obj: None,
            def: None,
            addr: None,
            seen: None,
            held: None,
        }
    }
    /// The values a register guard lets pass, when it guards `A`, `X` or `Y`.
    fn allowed(&self) -> Option<(usize, Bits)> {
        let r = self.reg.filter(|&r| r < 3)? as usize;
        let runs = self.ok.iter().flat_map(|&(lo, hi)| lo..=hi.min(0xff));
        Some((r, bits(runs)))
    }
}

/// Runs of the values in `0..=max` outside `bad` (inclusive runs).
pub fn complement(mut bad: Vec<(u32, u32)>, max: u32) -> Vec<(u32, u32)> {
    bad.sort_unstable();
    let mut out = Vec::new();
    let mut next = 0u32;
    for (lo, hi) in bad {
        if lo > next {
            out.push((next, lo - 1));
        }
        next = next.max(hi.saturating_add(1));
    }
    if next <= max {
        out.push((next, max));
    }
    out
}

/// What a register holds on every path: values, the guards that narrowed them, and the
/// entry register it still holds.
#[derive(Clone, PartialEq, Eq)]
struct Fact {
    vals: Bits,
    by: Vec<u32>,
    src: Option<u8>,
}

impl Fact {
    fn top(src: Option<u8>) -> Self {
        Fact {
            vals: ALL,
            by: Vec::new(),
            src,
        }
    }
    fn join(&mut self, o: &Fact) -> bool {
        let old = self.clone();
        for (a, b) in self.vals.iter_mut().zip(o.vals) {
            *a |= b;
        }
        for &t in &o.by {
            if !self.by.contains(&t) {
                self.by.push(t);
            }
        }
        self.by.sort_unstable();
        if self.src != o.src {
            self.src = None;
        }
        *self != old
    }
}

type State = [Fact; 3];

/// The register guard (its index in [`Function::whys`]) the deopt ending block `b` stands
/// for alone, its register and the values it lets pass.
fn reg_guard(f: &Function, b: &Block) -> Option<(usize, usize, Bits)> {
    let op = b.ops.last().filter(|o| o.is_deopt() && o.why > 0)?;
    let &[j] = &f.exits[op.why as usize - 1][..] else {
        return None;
    };
    let (r, ok) = f.whys[j as usize].allowed()?;
    Some((j as usize, r, ok))
}

/// Record in each register guard of `f` ([`Kind::Entry`], [`Kind::Index`]) what the register
/// holds there on every path ([`Why::held`]): a forward analysis over its blocks as emitted,
/// before optimization, where a register keeps its fact through copies, takes a constant's
/// value, is narrowed past each register guard and is unknown after any other write or a
/// call. An entry's registers start as its own.
pub fn imply(f: &mut Function) {
    let n = f.blocks.len();
    if n == 0 {
        return;
    }
    let top = f.top;
    let mut ins: Vec<Option<State>> = vec![None; n];
    ins[0] = Some(std::array::from_fn(|r| Fact::top(top.then_some(r as u8))));
    let mut work: Vec<usize> = vec![0];
    let mut held: Vec<Option<Fact>> = vec![None; f.whys.len()];
    let guard_of: Vec<Option<(usize, usize, Bits)>> =
        f.blocks.iter().map(|b| reg_guard(f, b)).collect();
    while let Some(bi) = work.pop() {
        let mut st = ins[bi].clone().unwrap();
        let mut out: Vec<(usize, State)> = Vec::new();
        for op in &f.blocks[bi].ops {
            match op.opc {
                Opc::CBranch => {
                    let t = op.aux as usize;
                    out.push((t, st.clone()));
                    if let Some((j, r, ok)) = guard_of[t] {
                        match &mut held[j] {
                            Some(e) => {
                                e.join(&st[r]);
                            }
                            e => *e = Some(st[r].clone()),
                        }
                        let s = &mut st[r];
                        if !subset(&s.vals, &ok) {
                            for (a, b) in s.vals.iter_mut().zip(ok) {
                                *a &= b;
                            }
                            s.by = vec![j as u32];
                        }
                    }
                }
                Opc::Branch => out.push((op.aux as usize, st.clone())),
                Opc::BranchInd => {
                    for &(_, b) in &f.tables[op.aux as usize] {
                        out.push((b as usize, st.clone()));
                    }
                }
                Opc::Call => st = std::array::from_fn(|_| Fact::top(None)),
                _ => {
                    let o = op.out;
                    if o.space() == Space::Reg && o.size() > 1 {
                        for r in (o.off() as usize..o.off() as usize + o.size() as usize)
                            .filter(|&r| r < 3)
                        {
                            st[r] = Fact::top(None);
                        }
                    } else if o.space() == Space::Reg && o.off() < 3 {
                        let a = op.ins[0];
                        let v = match (op.opc, a.space()) {
                            (Opc::Copy, Space::Reg) if a.off() < 3 && a.size() == 1 => {
                                st[a.off() as usize].clone()
                            }
                            (Opc::Copy, Space::Const) => Fact {
                                vals: bits([a.off() & 0xff]),
                                by: Vec::new(),
                                src: None,
                            },
                            _ => Fact::top(None),
                        };
                        st[o.off() as usize] = v;
                    }
                }
            }
        }
        for (t, s) in out {
            let changed = match &mut ins[t] {
                Some(e) => e.iter_mut().zip(&s).fold(false, |c, (a, b)| a.join(b) | c),
                e => {
                    *e = Some(s);
                    true
                }
            };
            if changed {
                work.push(t);
            }
        }
    }
    // A guard the register's values on every path already pass is not taken: each branch to
    // its deopt is never taken.
    let implied: Vec<bool> = (f.whys.iter().zip(&held))
        .map(|(w, h)| match (w.allowed(), h) {
            (Some((_, ok)), Some(h)) => subset(&h.vals, &ok),
            _ => false,
        })
        .collect();
    let cbranches = f.blocks.iter_mut().flat_map(|b| &mut b.ops);
    for op in cbranches.filter(|op| op.opc == Opc::CBranch) {
        if guard_of[op.aux as usize].is_some_and(|(j, _, _)| implied[j]) {
            op.ins[0] = crate::ir::k(0, 1);
        }
    }
    for (w, h) in f.whys.iter_mut().zip(held) {
        w.held = h.map(|h| (h.vals, h.by, h.src));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn complement_runs() {
        assert_eq!(
            complement(vec![(0x100, 0x1ff)], 0xffff),
            [(0, 0xff), (0x200, 0xffff)]
        );
        assert_eq!(complement(vec![(0, 0xffff)], 0xffff), []);
        assert_eq!(complement(vec![(5, 9), (0, 2)], 20), [(3, 4), (10, 20)]);
    }

    #[test]
    fn bits_sets() {
        let b = bits_iv(Iv { lo: 3, hi: 70 });
        assert!(subset(&bits([3, 64, 70]), &b));
        assert!(!subset(&bits([2]), &b));
    }
}
