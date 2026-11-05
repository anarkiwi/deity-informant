//! Init folding: the host calls an init entry once, first, on the loaded image, and every
//! other entry after it returns. Its run is the same on every input along its decided path
//! ([`crate::dispatch::Walk`]): from its entry while each byte it reads is a loaded byte or one
//! it stored earlier, each branch is decided and each store's address is known. So the program
//! starts from the memory that path leaves, and the folded entry starts where it ends, at its
//! entry height with every register it wrote known: it makes the stores of registers the path
//! never wrote (the entry's input, as a song number) and sets the registers it wrote. A byte
//! only the path writes is then a constant of the program, and code only the path runs is not
//! lifted.

use crate::analysis::{discover, Entry, Facts, Iv, Var};
use crate::dispatch::{stored_reg, Walk, STEPS};
use crate::vsa::Code;
use di_pcode::{reg, Space, Template};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::cell::RefCell;
use std::collections::BTreeMap;

pub use crate::analysis::Val;

/// The registers the folded entry hands the code it starts: `A`, `X`, `Y` and the flags.
const ARCH: [usize; 9] = [
    reg::A,
    reg::X,
    reg::Y,
    reg::C,
    reg::Z,
    reg::I,
    reg::D,
    reg::V,
    reg::N,
];

/// An init entry folded at the end of its decided path.
#[derive(Clone, Debug, Default)]
pub struct Fold {
    /// The entry's index and its pc.
    pub index: usize,
    pub entry: u16,
    /// Where the folded entry starts.
    pub pc: u16,
    /// The bytes the path leaves: the program's starting memory over the image.
    pub image: Vec<(u16, u8)>,
    /// The stores the folded entry makes and the registers it sets.
    pub pre: Vec<(u16, Val)>,
    pub set: Vec<(usize, u8)>,
    /// The path's instructions `(pc, length)` whose opcode it does not store: code the
    /// program ran, constant where the folded program writes none of their bytes.
    pub code: Vec<(u16, u8)>,
}

/// The path of an entry after `n` steps (or where it stops) and the last step count it could
/// be folded at: on its entry height, every register it wrote known, every byte it stored
/// outside the stack known or a register it never wrote, no access to `volatile`, no store
/// above the entry SP, and not at another entry.
struct Path {
    walk: Walk,
    /// Per byte stored outside the stack, its value (`None`: unknown).
    stored: BTreeMap<u16, Option<Val>>,
    /// The registers it wrote, by `lift()` index.
    written: u32,
    /// The instructions it ran, `pc: length`.
    ran: BTreeMap<u16, u8>,
    best: Option<usize>,
}

#[allow(clippy::too_many_arguments)]
fn path(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    e: &Entry,
    sp: u8,
    loaded: (u32, u32),
    volatile: &[(u32, u32)],
    others: &[u16],
    n: usize,
) -> Path {
    let mut p = Path {
        walk: Walk::new(e, Some(sp)),
        stored: BTreeMap::new(),
        written: 0,
        ran: BTreeMap::new(),
        best: None,
    };
    // A byte the path stored is read from what it stored, never from the image.
    let dirty: RefCell<FxHashSet<u16>> = RefCell::default();
    let konst = |a: u32| {
        (loaded.0 <= a && a < loaded.1 && !dirty.borrow().contains(&(a as u16)))
            .then(|| code.img[a as usize])
    };
    let (mut unknown, mut spoiled, mut read) = (0usize, false, Vec::new());
    for i in 0..=n.min(STEPS) {
        let w = &p.walk;
        if i > 0
            && !spoiled
            && unknown == 0
            && w.rets.is_empty()
            && w.env[reg::SP] == Iv::exact(sp as u32)
            && ARCH
                .iter()
                .all(|&r| p.written >> r & 1 == 0 || w.env[r].value().is_some())
            && !others.contains(&w.pc)
        {
            p.best = Some(i);
        }
        if i == n {
            break;
        }
        let Some((op, acc, more)) = p.walk.step(code, vars, &konst, &mut read) else {
            break;
        };
        read.clear();
        if acc.iter().any(|a| a.store && a.addr.value().is_none()) {
            break;
        }
        let t = Template::of(op);
        p.ran.insert(p.walk.at, t.len);
        for (ai, a) in acc.iter().enumerate() {
            spoiled |= volatile.iter().any(|&(lo, hi)| a.addr.meets(lo, hi));
            if !a.store || a.dummy {
                continue;
            }
            let x = a.addr.lo as u16;
            dirty.borrow_mut().insert(x);
            if (0x100..0x200).contains(&x) {
                spoiled |= x > 0x100 + sp as u16;
                continue;
            }
            let v = match (a.val.value(), stored_reg(t, ai)) {
                (Some(v), _) => Some(Val::K(v as u8)),
                (None, Some(r)) if p.written >> r & 1 == 0 => Some(Val::R(r)),
                _ => None,
            };
            let was = matches!(p.stored.insert(x, v), Some(None));
            unknown = unknown + usize::from(v.is_none()) - usize::from(was);
        }
        for o in t.ops().iter().filter(|o| o.out.space() == Space::Reg) {
            p.written |= 1u32.checked_shl(o.out.off()).unwrap_or(0);
        }
        if !more {
            break;
        }
    }
    p
}

/// The folded program for init entry `ix` of `entries` in a static lift (`loaded`, entry SP
/// `sp`): its starting memory, its entries and the fold, or `None` when the path folds
/// nothing.
pub fn prepare(
    img: &[u8; 0x10000],
    entries: &[Entry],
    ix: usize,
    volatile: &[(u32, u32)],
    loaded: (u32, u32),
    sp: u8,
) -> Option<(Box<[u8; 0x10000]>, Vec<Entry>, Fold)> {
    let g = discover(
        img,
        entries,
        &Facts::default(),
        None,
        true,
        Some(loaded),
        Some(sp),
    );
    let code = Code {
        img: &g.img,
        cells: &g.cells,
        pull_clear: g.pull_clear,
        vecs: Vec::new(),
        observed: FixedBitSet::with_capacity(0x10000),
        constant: FixedBitSet::with_capacity(0x10000),
    };
    let others: Vec<u16> = (entries.iter().enumerate())
        .filter(|&(i, _)| i != ix)
        .map(|(_, e)| e.pc)
        .collect();
    let e = &entries[ix];
    let run = |n| path(&code, &g.vars, e, sp, loaded, volatile, &others, n);
    let p = run(run(STEPS).best?);
    let mut out = Box::new(*img);
    let mut f = Fold {
        index: ix,
        entry: e.pc,
        pc: p.walk.pc,
        ..Fold::default()
    };
    for (&a, &v) in &p.stored {
        match v {
            Some(Val::K(x)) => {
                out[a as usize] = x;
                f.image.push((a, x));
            }
            Some(v) => f.pre.push((a, v)),
            None => unreachable!("a fold point stores no unknown byte"),
        }
    }
    f.code = (p.ran.iter())
        .filter(|e| !p.stored.contains_key(e.0))
        .map(|(&pc, &n)| (pc, n))
        .collect();
    f.set = (ARCH.iter())
        .filter(|&&r| p.written >> r & 1 == 1)
        .map(|&r| (r, p.walk.env[r].lo as u8))
        .collect();
    let mut es = entries.to_vec();
    es[ix] = Entry {
        pc: f.pc,
        regs: (e.regs.iter())
            .filter(|(r, _)| p.written >> r & 1 == 0)
            .cloned()
            .collect(),
        set: f.set.clone(),
        pre: f.pre.clone(),
    };
    Some((out, es, f))
}

impl Fold {
    /// Keep the path's instructions no byte of which the folded program `g` (entries
    /// `entries`) writes, and give their bytes.
    pub(crate) fn settle(&mut self, g: &crate::analysis::Graph, entries: &[Entry]) -> FixedBitSet {
        let mut moved = g.cells.clone();
        moved.union_with(&g.written);
        moved.extend(crate::analysis::pre_stored(entries).map(|a| a as usize));
        let bytes = |&(pc, n): &(u16, u8)| (0..n as u16).map(move |i| pc.wrapping_add(i) as usize);
        self.code.retain(|c| bytes(c).all(|a| !moved.contains(a)));
        let mut out = FixedBitSet::with_capacity(0x10000);
        out.extend(self.code.iter().flat_map(bytes));
        out
    }
}
