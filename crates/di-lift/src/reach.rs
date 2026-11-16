//! Reachable states: the loads whose addresses the states a closed program's entries reach
//! from each of init's runs bound.
//!
//! After init, memory changes only through the entries, whose inputs are their declared
//! registers and the volatile reads (whose values the device models give as sets). The
//! addresses of a load follow from a *slice* `K` of memory: the bytes (and volatile reads)
//! its address is computed from, closed under the stores into them. Every other byte is
//! abstracted to an interval: a byte no store may write holds its run's value; any other is
//! unknown at an entry, within the values another slice's states give it where one holds it.
//! An entry is evaluated on that state over every path an unknown branch condition, jump target
//! or opcode allows, so from each state of `K` it reaches a finite set of states of `K`; their
//! least fixpoint from a run's `K` holds every state any sequence of calls reaches, and each
//! load's addresses on those calls are all it reads.
//!
//! Each value carries its *provenance*: the bytes and volatile reads it was computed from
//! (through data and addresses), [`J`] where paths that differ in it joined, [`STAR`] where it
//! was read through an unknown address that may name a byte stores write, [`U`] for an
//! undeclared register. A store of an unknown value into `K`, or of unknown address that may
//! reach `K`, adds its provenance to `K`; a load whose unknown address is not inside the held
//! bytes and whose provenance meets more than one slice, or bytes in none, joins them into
//! one. Slices of disjoint provenance are evaluated apart (one voice's cursor and pointer
//! apart from the next voice's). A load reached with an unknown address in one slice's
//! evaluation whose provenance (no flag) lies in another's is read there: on that path its
//! address is computed from that slice's bytes alone, so the other evaluation, reaching every
//! path, computes it.
//!
//! A slice past [`Caps::cells`] bytes, a fixpoint past [`Caps::states`] states or
//! [`Caps::steps`] instructions, or the slices still growing after [`Caps::rounds`] rounds bound
//! nothing; the loads they would serve keep their checks.

use crate::analysis::{iv_op, Entry, Flow, Graph, Iv};
use crate::dev::Devices;
use di_pcode::ir::{CtrlKind, Opc, PenKind, Template, Vn, FILE, IMM, SLOT_BASE, UNIQ_BASE};
use di_pcode::reg as R;
use fixedbitset::FixedBitSet;
use rayon::prelude::*;
use rustc_hash::{FxHashMap, FxHashSet};
use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

/// The bounds of the search ([module documentation](self)).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Caps {
    /// States of one slice's fixpoint from one run.
    pub states: usize,
    /// Bytes and volatile reads of one slice.
    pub cells: usize,
    /// Instructions evaluated in one slice's fixpoints over every run.
    pub steps: u64,
    /// Instructions evaluated in the whole search.
    pub total: u64,
    /// Rounds of slices growing or their values narrowing.
    pub rounds: usize,
    /// Configurations of one call.
    pub configs: usize,
}

impl Default for Caps {
    fn default() -> Self {
        Caps {
            states: 1 << 13,
            cells: 32,
            steps: 1 << 26,
            total: 1 << 29,
            rounds: 12,
            configs: 1 << 14,
        }
    }
}

/// The instructions one slice's evaluations may spend, have spent, and whether one of them
/// stopped.
pub struct Budget<'a> {
    cap: u64,
    spent: AtomicU64,
    stop: AtomicBool,
    /// The instructions every slice's evaluations have spent, of `Caps::total`.
    total: &'a AtomicU64,
}

impl Budget<'_> {
    /// Spend `n` instructions of `cap` (and of `total`): whether the evaluations are to stop.
    fn spend(&self, n: u64, cap: u64, total: u64) -> bool {
        let over = self.spent.fetch_add(n, Ordering::Relaxed) + n > cap
            || self.total.fetch_add(n, Ordering::Relaxed) + n > total;
        if over {
            self.stop.store(true, Ordering::Relaxed);
        }
        over || self.stop.load(Ordering::Relaxed)
    }
}

/// Instructions spent between two looks at the budget.
const SPEND: u64 = 1 << 12;

/// States one task of a fixpoint's layer evaluates.
const CHUNK: usize = 4;

/// A load by instruction, opcode and access index.
pub type Key = (u16, u8, usize);

/// Provenance of a volatile read of address `a`: `DEV | a`.
pub const DEV: u32 = 0x1_0000;
/// Provenance of a join of paths whose values differ.
pub const J: u32 = 0x2_0000;
/// Provenance of a read through an unknown address that may name a byte stores write.
pub const STAR: u32 = 0x2_0001;
/// Provenance of an undeclared register.
pub const U: u32 = 0x2_0002;
/// Provenance of a join of paths whose values of byte `a` differ: `JC | a`.
pub const JC: u32 = 0x3_0000;
/// The return address an entry is called with: it returns to `RET + 1`.
const RET: u16 = 0x0001;

/// Interned sorted sets of provenance elements.
struct Provs {
    sets: Vec<Box<[u32]>>,
    ids: FxHashMap<Box<[u32]>, u32>,
    memo: FxHashMap<(u32, u32), u32>,
}

impl Provs {
    fn new() -> Self {
        let mut p = Provs {
            sets: Vec::new(),
            ids: FxHashMap::default(),
            memo: FxHashMap::default(),
        };
        p.intern(Box::new([]));
        p
    }
    fn intern(&mut self, s: Box<[u32]>) -> u32 {
        if let Some(&i) = self.ids.get(&s) {
            return i;
        }
        let i = self.sets.len() as u32;
        self.sets.push(s.clone());
        self.ids.insert(s, i);
        i
    }
    fn one(&mut self, x: u32) -> u32 {
        self.intern(Box::new([x]))
    }
    fn union(&mut self, a: u32, b: u32) -> u32 {
        if a == b || b == 0 {
            return a;
        }
        if a == 0 {
            return b;
        }
        let key = (a.min(b), a.max(b));
        if let Some(&r) = self.memo.get(&key) {
            return r;
        }
        let (x, y) = (&self.sets[a as usize], &self.sets[b as usize]);
        let mut m: Vec<u32> = x.iter().chain(y.iter()).copied().collect();
        m.sort_unstable();
        m.dedup();
        let r = self.intern(m.into_boxed_slice());
        self.memo.insert(key, r);
        r
    }
    fn inter(&mut self, a: u32, b: u32) -> u32 {
        if a == b || a == 0 || b == 0 {
            return if a == b { a } else { 0 };
        }
        let key = (a.max(b), a.min(b));
        if let Some(&r) = self.memo.get(&key) {
            return r;
        }
        let y = &self.sets[b as usize];
        let m: Vec<u32> = (self.sets[a as usize].iter())
            .filter(|e| y.binary_search(e).is_ok())
            .copied()
            .collect();
        let r = self.intern(m.into_boxed_slice());
        self.memo.insert(key, r);
        r
    }
}

/// A value: one of `lo..=hi` (known where they are one), from the provenance set `p` (of
/// every path joined into it), of which each path's holds `m`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
struct V {
    lo: u32,
    hi: u32,
    p: u32,
    m: u32,
}

impl V {
    fn top(self) -> bool {
        self.lo != self.hi
    }
    fn iv(self) -> Iv {
        Iv {
            lo: self.lo,
            hi: self.hi,
        }
    }
    fn of(iv: Iv, (p, m): (u32, u32)) -> V {
        V {
            lo: iv.lo,
            hi: iv.hi,
            p,
            m,
        }
    }
    /// Its byte `i`.
    fn byte(self, i: u32) -> V {
        let (lo, hi) = match self.top() {
            false => (self.lo >> (8 * i) & 0xff, self.lo >> (8 * i) & 0xff),
            true if i == 0 && self.hi <= 0xff => (self.lo, self.hi),
            true => (0, 0xff),
        };
        V { lo, hi, ..self }
    }
    fn with(self, (p, m): (u32, u32)) -> V {
        V { p, m, ..self }
    }
    fn prov(self) -> (u32, u32) {
        (self.p, self.m)
    }
}

const fn k(x: u32) -> V {
    V {
        lo: x,
        hi: x,
        p: 0,
        m: 0,
    }
}

const fn byte((p, m): (u32, u32)) -> V {
    V {
        lo: 0,
        hi: 0xff,
        p,
        m,
    }
}

impl Provs {
    /// The provenance of a value computed from `a` and `b`.
    fn both(&mut self, a: (u32, u32), b: (u32, u32)) -> (u32, u32) {
        (self.union(a.0, b.0), self.union(a.1, b.1))
    }
    /// The provenance of byte or volatile read `e` alone.
    fn only(&mut self, e: u32) -> (u32, u32) {
        let x = self.one(e);
        (x, x)
    }
}

/// A configuration within one call: the transfers to a lower PC it took, the registers, the
/// slice's bytes and their provenance,
/// the bytes the call wrote and, per address interval a store of unknown address may have
/// written, what it stored.
#[derive(Clone, Debug)]
struct Cf {
    pc: u16,
    back: u16,
    r: [V; 16],
    kv: Vec<u8>,
    kp: Vec<(u32, u32)>,
    mem: Mem,
    weak: BTreeMap<(u32, u32), V>,
}

/// The bytes a call wrote, by address.
#[derive(Clone, Debug, Default)]
struct Mem(Vec<(u16, V)>);

impl Mem {
    fn get(&self, a: &u16) -> Option<&V> {
        let i = self.0.binary_search_by_key(a, |e| e.0).ok()?;
        Some(&self.0[i].1)
    }
    fn insert(&mut self, a: u16, v: V) {
        match self.0.binary_search_by_key(&a, |e| e.0) {
            Ok(i) => self.0[i].1 = v,
            Err(i) => self.0.insert(i, (a, v)),
        }
    }
    /// The written addresses in `lo..=hi`.
    fn within(&self, lo: u16, hi: u16) -> impl Iterator<Item = u16> + '_ {
        let i = self.0.partition_point(|e| e.0 < lo);
        self.0[i..]
            .iter()
            .map(|e| e.0)
            .take_while(move |&a| a <= hi)
    }
}

/// What the search proved: per load the hulls of its addresses and bases, the slices whose
/// evaluations completed and their states over every run.
#[derive(Default, Debug)]
pub struct Bounds {
    pub loads: FxHashMap<Key, Hull>,
    pub slices: usize,
    pub states: usize,
}

/// Hulls `[lo, hi, base lo, base hi]` of a load's addresses and their bases.
pub type Hull = [u32; 4];

/// A provenance: of every path joined, of each.
pub type Prov = (Box<[u32]>, Box<[u32]>);

/// What a slice's evaluation from one run found: per load the hull of its known addresses and
/// the provenance and hull of each unknown one, the provenance the slice must take in, the
/// values of the slice's bytes over its states, and whether it ran out of a cap.
#[derive(Default, Debug)]
pub struct Found {
    pub addrs: FxHashMap<Key, Hull>,
    pub tops: FxHashMap<Key, FxHashMap<Prov, Hull>>,
    pub need: FxHashSet<Box<[u32]>>,
    pub ranges: FxHashMap<u16, [u32; 2]>,
    pub fail: bool,
    pub states: usize,
    pub steps: u64,
    /// The states it reached: a later evaluation of the slice evaluates them all at once.
    pub seen: Vec<Vec<u8>>,
}

impl Found {
    /// Whether the evaluation reached every state with every path: no cap, nothing to add.
    pub fn ok(&self) -> bool {
        !self.fail && self.need.is_empty()
    }
    fn absorb(&mut self, o: Found) {
        for (key, h) in o.addrs {
            let e = self.addrs.entry(key).or_insert(h);
            *e = hull(*e, h);
        }
        for (key, t) in o.tops {
            let m = self.tops.entry(key).or_default();
            for (p, h) in t {
                let e = m.entry(p).or_insert(h);
                *e = hull(*e, h);
            }
        }
        self.need.extend(o.need);
        self.fail |= o.fail;
        self.steps += o.steps;
    }
}

fn hull(a: Hull, b: Hull) -> Hull {
    [
        a[0].min(b[0]),
        a[1].max(b[1]),
        a[2].min(b[2]),
        a[3].max(b[3]),
    ]
}

/// The join of `a` and `b`, the values of byte `at` (or a register) on two paths.
fn join(provs: &mut Provs, a: V, b: V, at: Option<u16>) -> V {
    let p = provs.union(a.p, b.p);
    let h = V {
        lo: a.lo.min(b.lo),
        hi: a.hi.max(b.hi),
        p,
        m: provs.inter(a.m, b.m),
    };
    if !h.top() || (a.top() && b.top()) {
        return h;
    }
    let j = provs.one(match at {
        Some(c) => JC | c as u32,
        None => J,
    });
    V {
        p: provs.union(p, j),
        ..h
    }
}

/// One evaluation's interned provenance, what it found and the instructions it spent.
struct Run {
    provs: Provs,
    addrs: FxHashMap<Key, Hull>,
    tops: FxHashMap<Key, FxHashMap<(u32, u32), Hull>>,
    need: FxHashSet<u32>,
    fail: bool,
    steps: u64,
}

/// An instruction stopped at a volatile read of the slice: the values it may answer.
struct Fork(Vec<u8>);

/// The program, its run and the slice an evaluation reads.
pub struct Cx<'a> {
    g: &'a Graph,
    w: &'a FixedBitSet,
    vol: &'a FixedBitSet,
    dev: &'a Devices,
    entries: &'a [Entry],
    sp: u8,
    targets: &'a FxHashSet<Key>,
    mem0: &'a [u8; 0x10000],
    ranges: &'a FxHashMap<u16, [u32; 2]>,
    caps: Caps,
    budget: &'a Budget<'a>,
    kcell: FxHashMap<u16, usize>,
    kmask: FixedBitSet,
    kdev: FxHashSet<u16>,
    cells: Vec<u16>,
    keyed: Vec<u16>,
}

impl<'a> Cx<'a> {
    /// The evaluation of slice `slice` (bytes, and `DEV | a` volatile reads) of `g` from
    /// memory `mem0`: entries `entries` called at `sp`, `w` the bytes its stores may write,
    /// `ranges` the values bytes of other slices hold at an entry, loads `targets` recorded,
    /// within `caps`, spending `budget` with the slice's other evaluations; what the call wrote
    /// into the bytes `others` of other slices keys its configurations.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        g: &'a Graph,
        w: &'a FixedBitSet,
        vol: &'a FixedBitSet,
        dev: &'a Devices,
        entries: &'a [Entry],
        sp: u8,
        targets: &'a FxHashSet<Key>,
        mem0: &'a [u8; 0x10000],
        ranges: &'a FxHashMap<u16, [u32; 2]>,
        (caps, budget): (Caps, &'a Budget<'a>),
        (slice, others): (&[u32], &[u16]),
    ) -> Self {
        let cells: Vec<u16> = slice
            .iter()
            .filter(|&&e| e < DEV)
            .map(|&e| e as u16)
            .collect();
        Cx {
            g,
            w,
            vol,
            dev,
            entries,
            sp,
            targets,
            mem0,
            ranges,
            caps,
            budget,
            kcell: cells.iter().enumerate().map(|(i, &c)| (c, i)).collect(),
            kmask: {
                let mut m = FixedBitSet::with_capacity(0x10000);
                m.extend(cells.iter().map(|&c| c as usize));
                m
            },
            kdev: (slice.iter())
                .filter(|&&e| (DEV..J).contains(&e))
                .map(|&e| e as u16)
                .collect(),
            keyed: (others.iter())
                .filter(|&&c| !cells.contains(&c))
                .copied()
                .collect(),
            cells,
        }
    }

    fn volatile(&self, a: u16) -> bool {
        self.vol.contains(a as usize)
    }

    /// The index of byte `a` in the slice.
    fn kcell(&self, a: u16) -> Option<usize> {
        (self.kmask.contains(a as usize)).then(|| self.kcell[&a])
    }

    /// Whether byte `a` holds its run's value on every call.
    fn constant(&self, a: u16) -> bool {
        !self.volatile(a) && !self.w.contains(a as usize) && !self.kmask.contains(a as usize)
    }

    /// The values a volatile read of `a` answers in `cf`.
    fn answers(&self, cf: &Cf, a: u16) -> Vec<u8> {
        let known = |c: u16| cf.mem.get(&c).filter(|v| !v.top()).map(|v| Iv::exact(v.lo));
        match self.dev.read(a, known) {
            Some(b) => b.values().map(|x| x as u8).collect(),
            None => (0..=255).collect(),
        }
    }

    /// The value of byte `a` no access of this call wrote.
    fn base(&self, o: &mut Run, cf: &Cf, a: u16) -> V {
        if self.volatile(a) {
            return byte(o.provs.only(DEV | a as u32));
        }
        if let Some(i) = self.kcell(a) {
            return k(cf.kv[i] as u32).with(cf.kp[i]);
        }
        if !self.w.contains(a as usize) {
            return k(self.mem0[a as usize] as u32);
        }
        let [lo, hi] = self.ranges.get(&a).copied().unwrap_or([0, 0xff]);
        let mut v = V::of(Iv { lo, hi }, o.provs.only(a as u32));
        for (&(lo, hi), &x) in &cf.weak {
            if (lo..=hi).contains(&(a as u32)) {
                v = join(&mut o.provs, v, x, None);
            }
        }
        v
    }

    /// A read of byte `a` (provenance `pa`), the choices `ch` answering volatile reads of the
    /// slice.
    fn read(
        &self,
        o: &mut Run,
        cf: &Cf,
        a: u16,
        pa: (u32, u32),
        ch: &[u8],
        used: &mut usize,
    ) -> Result<V, Fork> {
        let v = match (self.volatile(a), self.kdev.contains(&a)) {
            (true, true) => {
                let Some(&x) = ch.get(*used) else {
                    return Err(Fork(self.answers(cf, a)));
                };
                *used += 1;
                k(x as u32).with(o.provs.only(DEV | a as u32))
            }
            (true, false) => {
                let vals = self.answers(cf, a);
                let (lo, hi) = (vals[0] as u32, vals[vals.len() - 1] as u32);
                V::of(Iv { lo, hi }, o.provs.only(DEV | a as u32))
            }
            _ => match cf.mem.get(&a) {
                Some(&v) => v,
                None => self.base(o, cf, a),
            },
        };
        Ok(v.with(o.provs.both(v.prov(), pa)))
    }

    /// A load of `size` bytes at `a`.
    fn load(
        &self,
        o: &mut Run,
        cf: &Cf,
        a: V,
        size: u32,
        ch: &[u8],
        used: &mut usize,
    ) -> Result<V, Fork> {
        if a.top() {
            let ok = size == 1 && a.hi - a.lo < 0x100 && a.hi <= 0xffff;
            if ok && (a.lo..=a.hi).all(|x| self.constant(x as u16)) {
                let bs = (a.lo..=a.hi).map(|x| self.mem0[x as usize] as u32);
                let (lo, hi) = bs.fold((0xff, 0), |(l, h), b| (l.min(b), h.max(b)));
                return Ok(V::of(Iv { lo, hi }, a.prov()));
            }
            let star = o.provs.one(STAR);
            let hi = (1u64 << (8 * size)) as u32 - 1;
            return Ok(V::of(Iv { lo: 0, hi }, (o.provs.union(a.p, star), a.m)));
        }
        let mut out = k(0);
        for i in 0..size {
            let at = (a.lo as u16).wrapping_add(i as u16);
            let y = self.read(o, cf, at, a.prov(), ch, used)?;
            let p = o.provs.both(out.prov(), y.prov());
            out = V::of(
                Iv {
                    lo: out.lo | y.lo << (8 * i),
                    hi: out.hi | y.hi << (8 * i),
                },
                p,
            );
        }
        Ok(out)
    }

    /// A store of `v` at known address `a`.
    fn write(&self, o: &mut Run, cf: &mut Cf, a: u16, v: V) {
        if self.volatile(a) {
            if let Some(c) = self.dev.canon(a) {
                cf.mem.insert(c, v);
            }
            return;
        }
        match self.kcell(a) {
            Some(_) if v.top() => {
                o.need.insert(v.p);
            }
            Some(i) => (cf.kv[i], cf.kp[i]) = (v.lo as u8, v.prov()),
            None => {
                cf.mem.insert(a, v);
            }
        }
    }

    /// A store of byte `v` at unknown address `a` by access `key`: it may write the bytes of
    /// the address interval the lift gives it.
    fn weak(&self, o: &mut Run, cf: &mut Cf, (pc, op, ai): Key, a: V, v: V) {
        let p = o.provs.both(a.prov(), v.prov());
        let mut runs: Vec<(u32, u32)> = match self.g.addr_sets.get(&(pc, true)) {
            Some(s) => s.iter().map(|&x| (x as u32, x as u32)).collect(),
            None => {
                let acc = (self.g.vars.get(&pc).into_iter().flatten())
                    .find(|x| x.op == op)
                    .and_then(|x| x.acc.get(ai));
                let iv = acc.map_or(Iv::full(2), |x| x.addr);
                match iv.hi - iv.lo >= 0xffff {
                    true => vec![(0, 0xffff)],
                    false if iv.hi > 0xffff => vec![(iv.lo, 0xffff), (0, iv.hi & 0xffff)],
                    false => vec![(iv.lo, iv.hi)],
                }
            }
        };
        if a.hi <= 0xffff {
            runs = (runs.into_iter())
                .map(|(lo, hi)| (lo.max(a.lo), hi.min(a.hi)))
                .filter(|&(lo, hi)| lo <= hi)
                .collect();
        }
        for &(lo, hi) in &runs {
            if self.cells.iter().any(|&c| (lo..=hi).contains(&(c as u32))) {
                o.need.insert(p.0);
            }
            let at: Vec<u16> = cf.mem.within(lo as u16, hi as u16).collect();
            for x in at {
                let j = join(
                    &mut o.provs,
                    cf.mem.get(&x).copied().unwrap(),
                    v.with(p),
                    Some(x),
                );
                cf.mem.insert(x, j);
            }
            let e = cf.weak.entry((lo, hi)).or_insert(v.with(p));
            *e = join(&mut o.provs, *e, v.with(p), None);
        }
    }

    /// The targets the lift gives the transfer of variant `op` at `pc`.
    fn flow(&self, pc: u16, op: u8) -> Option<Vec<u16>> {
        let v = self.g.vars.get(&pc)?.iter().find(|v| v.op == op)?;
        let t = match &v.flow {
            Flow::Next(t) => vec![*t],
            Flow::Br { t, f } => t.all().iter().copied().chain([*f]).collect(),
            Flow::Jmp(t) | Flow::Jsr { t, .. } => t.all().to_vec(),
            Flow::JmpInd(t) | Flow::Rts(t) | Flow::Rti(t) | Flow::Brk(t) => t.clone(),
            Flow::Jam => Vec::new(),
        };
        (!t.is_empty()).then_some(t)
    }

    fn push(&self, o: &mut Run, cf: &mut Cf, v: V) -> Option<()> {
        let sp = cf.r[R::SP];
        if sp.top() {
            o.fail = true;
            return None;
        }
        self.write(o, cf, 0x100 | sp.lo as u16, v);
        cf.r[R::SP] = k((sp.lo as u8).wrapping_sub(1) as u32).with(sp.prov());
        Some(())
    }

    fn pull(&self, o: &mut Run, cf: &mut Cf) -> Option<V> {
        let sp = cf.r[R::SP];
        if sp.top() {
            o.fail = true;
            return None;
        }
        let s = (sp.lo as u8).wrapping_add(1);
        cf.r[R::SP] = k(s as u32).with(sp.prov());
        self.read(o, cf, 0x100 | s as u16, (0, 0), &[], &mut 0).ok()
    }

    /// The status byte (`B` as given).
    fn status(&self, o: &mut Run, cf: &Cf, brk: bool) -> V {
        let mut s = k(0x20 | (brk as u32) << 4);
        for &(i, b) in R::STATUS_BITS.iter() {
            let f = cf.r[i];
            let p = o.provs.both(s.prov(), f.prov());
            s = V::of(
                Iv {
                    lo: s.lo | f.lo << b,
                    hi: s.hi | f.hi << b,
                },
                p,
            );
        }
        s
    }

    /// Execute variant `op` at `cf.pc`, the choices `ch` answering the slice's volatile reads:
    /// the successors, or the values the next such read may answer.
    fn insn(&self, o: &mut Run, cf: &mut Cf, op: u8, ch: &[u8]) -> Result<Vec<u16>, Fork> {
        let pc = cf.pc;
        let t = Template::of(op);
        let mut used = 0;
        let mut b = [k(0); 2];
        for (i, bi) in b.iter_mut().enumerate().take(t.len as usize - 1) {
            *bi = self.read(o, cf, pc.wrapping_add(1 + i as u16), (0, 0), ch, &mut used)?;
        }
        let inc = |v: V| match v.hi < 0xff {
            true => V {
                lo: v.lo + 1,
                hi: v.hi + 1,
                ..v
            },
            false => byte(v.prov()),
        };
        let mut f = [k(0); FILE];
        f[..16].copy_from_slice(&cf.r);
        f[SLOT_BASE + 1] = b[0];
        let lo = b[0].lo | b[1].lo << 8;
        let hi = b[0].hi | b[1].hi << 8;
        f[SLOT_BASE + 2] = V::of(Iv { lo, hi }, o.provs.both(b[0].prov(), b[1].prov()));
        f[SLOT_BASE + 3] = inc(b[0]);
        f[SLOT_BASE + 4] = inc(b[1]);
        let mut ai = 0;
        for x in t.ops() {
            let get = |o: &mut Run, f: &[V; FILE], cf: &Cf, v: Vn, used: &mut usize| {
                Ok(match v.index() {
                    i if i < FILE => f[i],
                    IMM => k(v.off()),
                    _ => self.load(o, cf, k(v.off()), v.size(), ch, used)?,
                })
            };
            let a = get(o, &f, cf, x.ins[0], &mut used)?;
            let val = match x.opc {
                Opc::Load if x.out.is_none() => {
                    ai += 1;
                    continue;
                }
                Opc::Load => {
                    let key = (pc, op, ai);
                    ai += 1;
                    if self.targets.contains(&key) {
                        self.visit(o, &f, t, key, a);
                    }
                    self.load(o, cf, a, x.out.size(), ch, &mut used)?
                }
                Opc::Store => {
                    let v = get(o, &f, cf, x.ins[1], &mut used)?;
                    let key = (pc, op, ai);
                    ai += 1;
                    for i in 0..x.ins[1].size() {
                        let y = v.byte(i);
                        match a.top() {
                            true => self.weak(o, cf, key, a, y),
                            false => {
                                let at = (a.lo as u16).wrapping_add(i as u16);
                                let y = y.with(o.provs.both(y.prov(), a.prov()));
                                self.write(o, cf, at, y)
                            }
                        }
                    }
                    continue;
                }
                Opc::IntZext => a,
                opc => {
                    let bv = get(o, &f, cf, x.ins[1], &mut used)?;
                    let iv = iv_op(opc, a.iv(), bv.iv(), x.out.size(), x.ins[0].size());
                    V::of(iv, o.provs.both(a.prov(), bv.prov()))
                }
            };
            match x.out.index() {
                i if i < FILE => f[i] = val,
                _ => {
                    for i in 0..x.out.size() {
                        let at = (x.out.off() as u16).wrapping_add(i as u16);
                        self.write(o, cf, at, val.byte(i));
                    }
                }
            }
        }
        cf.r.copy_from_slice(&f[..16]);
        let next = pc.wrapping_add(t.len as u16);
        let w = f[SLOT_BASE + 2];
        let at = |v: V| (!v.top()).then_some(v.lo as u16);
        let succ = match t.ctrl {
            CtrlKind::Next => Some(vec![next]),
            CtrlKind::Br { flag, pol } => {
                let tgt = match b[0].top() {
                    false => Some(vec![next.wrapping_add(b[0].lo as u8 as i8 as u16)]),
                    true => self.flow(pc, op),
                };
                let c = cf.r[flag as usize];
                match (c.top(), tgt) {
                    (_, None) => None,
                    (true, Some(mut t)) => {
                        t.push(next);
                        Some(t)
                    }
                    (false, Some(t)) if c.lo == pol as u32 => Some(t),
                    (false, Some(_)) => Some(vec![next]),
                }
            }
            CtrlKind::Jmp => at(w).map(|t| vec![t]).or_else(|| self.flow(pc, op)),
            CtrlKind::JmpInd => match at(w) {
                Some(p) => {
                    let lo = self.read(o, cf, p, (0, 0), ch, &mut used)?;
                    let h = (p & 0xff00) | (p.wrapping_add(1) & 0xff);
                    let hi = self.read(o, cf, h, (0, 0), ch, &mut used)?;
                    match lo.top() || hi.top() {
                        false => Some(vec![(lo.lo | hi.lo << 8) as u16]),
                        true => self.flow(pc, op),
                    }
                }
                None => self.flow(pc, op),
            },
            CtrlKind::Jsr => {
                let ret = next.wrapping_sub(1);
                let ok = self.push(o, cf, k((ret >> 8) as u32));
                let ok = ok.and_then(|_| self.push(o, cf, k(ret as u32 & 0xff)));
                ok.and_then(|_| at(w).map(|t| vec![t]).or_else(|| self.flow(pc, op)))
            }
            CtrlKind::Rts | CtrlKind::Rti => {
                let rti = t.ctrl == CtrlKind::Rti;
                let p = if rti { self.pull(o, cf) } else { Some(k(0)) };
                let lo = p.and_then(|_| self.pull(o, cf));
                let hi = lo.and_then(|_| self.pull(o, cf));
                if let (true, Some(p)) = (rti, p) {
                    for (i, s) in R::STATUS_BITS {
                        let (lo, hi) = match p.top() {
                            false => (p.lo >> s & 1, p.lo >> s & 1),
                            true => (0, 1),
                        };
                        cf.r[i] = V::of(Iv { lo, hi }, p.prov());
                    }
                }
                match (lo, hi) {
                    (Some(lo), Some(hi)) if !lo.top() && !hi.top() => {
                        let ra = (lo.lo | hi.lo << 8) as u16;
                        Some(vec![if rti { ra } else { ra.wrapping_add(1) }])
                    }
                    (Some(_), Some(_)) => self.flow(pc, op),
                    _ => None,
                }
            }
            CtrlKind::Brk => {
                let ret = pc.wrapping_add(2);
                let s = self.status(o, cf, true);
                let ok = self.push(o, cf, k((ret >> 8) as u32));
                let ok = ok.and_then(|_| self.push(o, cf, k(ret as u32 & 0xff)));
                let ok = ok.and_then(|_| self.push(o, cf, s));
                cf.r[R::I] = k(1);
                let lo = self.read(o, cf, 0xfffe, (0, 0), ch, &mut used)?;
                let hi = self.read(o, cf, 0xffff, (0, 0), ch, &mut used)?;
                match (ok, lo.top() || hi.top()) {
                    (None, _) => None,
                    (_, false) => Some(vec![(lo.lo | hi.lo << 8) as u16]),
                    (_, true) => self.flow(pc, op),
                }
            }
            CtrlKind::Jam => Some(Vec::new()),
        };
        Ok(succ.unwrap_or_else(|| {
            o.fail = true;
            Vec::new()
        }))
    }

    /// Record a visit of load `key` at address `a`.
    fn visit(&self, o: &mut Run, f: &[V; FILE], t: &Template, key: Key, a: V) {
        let base = match t.pen {
            PenKind::Iy => f[UNIQ_BASE + t.iy_base as usize],
            PenKind::Ax | PenKind::Ay => f[SLOT_BASE + 2],
            _ => a,
        };
        let b = if base.hi > 0xffff { a } else { base };
        let h = [a.lo, a.hi.min(0xffff), b.lo, b.hi];
        let at = match a.top() {
            true => o.tops.entry(key).or_default().entry(a.prov()).or_insert(h),
            false => o.addrs.entry(key).or_insert(h),
        };
        *at = hull(*at, h);
    }

    /// The configurations after variant `op` at `cf.pc`, forking at volatile reads of the
    /// slice.
    fn exec(&self, o: &mut Run, cf: &Cf, op: u8, ch: &mut Vec<u8>, out: &mut Vec<Cf>) {
        let mut c = cf.clone();
        match self.insn(o, &mut c, op, ch) {
            Ok(succ) => spread(c, succ, out),
            Err(Fork(vals)) => {
                for v in vals {
                    ch.push(v);
                    self.exec(o, cf, op, ch, out);
                    ch.pop();
                }
            }
        }
    }

    /// The key of `cf`: its PC, `A`, `X`, `Y`, `SP` and `D` where known, the stack above its
    /// `SP`, what the call wrote into other slices' bytes and its slice bytes. Configurations of
    /// one key join.
    fn key(&self, cf: &Cf) -> Box<[u8]> {
        let mut key = Vec::with_capacity(48 + cf.kv.len() + 2 * self.keyed.len());
        key.extend(cf.pc.to_be_bytes());
        let put = |v: V| [v.top() as u8, if v.top() { 0 } else { v.lo as u8 }];
        for i in [R::A, R::X, R::Y, R::SP, R::D] {
            key.extend(put(cf.r[i]));
        }
        let sp = cf.r[R::SP];
        if !sp.top() {
            for s in (sp.lo as u16 + 1)..=(self.sp as u16 + 2).min(0xff) {
                key.extend(put(cf
                    .mem
                    .get(&(0x100 | s))
                    .copied()
                    .unwrap_or(byte((0, 0)))));
            }
        }
        for c in &self.keyed {
            key.extend(cf.mem.get(c).map_or([2, 0], |&v| put(v)));
        }
        key.extend(&cf.kv);
        key.into_boxed_slice()
    }

    /// Join `new` into `old` (of one key): whether `old` changed.
    fn join_into(&self, o: &mut Run, old: &mut Cf, new: &Cf) -> bool {
        let mut changed = false;
        for i in 0..16 {
            let j = join(&mut o.provs, old.r[i], new.r[i], None);
            changed |= j != old.r[i];
            old.r[i] = j;
        }
        for i in 0..old.kp.len() {
            let (a, b) = (old.kp[i], new.kp[i]);
            let p = (o.provs.union(a.0, b.0), o.provs.inter(a.1, b.1));
            changed |= p != a;
            old.kp[i] = p;
        }
        for (&r, &v) in &new.weak {
            let e = old.weak.entry(r).or_insert(v);
            let j = join(&mut o.provs, *e, v, None);
            changed |= j != *e;
            *e = j;
        }
        // The written bytes of both, merged by address: a byte one did not write holds its base.
        let (a, b) = (&old.mem.0, &new.mem.0);
        let mut out = Vec::with_capacity(a.len().max(b.len()));
        let (mut i, mut j) = (0, 0);
        while i < a.len() || j < b.len() {
            let (x, y) = (a.get(i), b.get(j));
            let (at, v) = match (x, y) {
                (Some(x), Some(y)) if x.0 == y.0 => {
                    (i, j) = (i + 1, j + 1);
                    (
                        x.0,
                        (x.1 != y.1).then(|| join(&mut o.provs, x.1, y.1, Some(x.0))),
                    )
                }
                (Some(x), y) if y.is_none_or(|y| x.0 < y.0) => {
                    i += 1;
                    let n = self.base(o, new, x.0);
                    (x.0, Some(join(&mut o.provs, x.1, n, Some(x.0))))
                }
                (_, Some(y)) => {
                    j += 1;
                    let n = self.base(o, old, y.0);
                    (y.0, Some(join(&mut o.provs, n, y.1, Some(y.0))))
                }
                (_, None) => unreachable!("one side holds a byte while the loop runs"),
            };
            let kept = x.filter(|x| x.0 == at).map(|x| x.1);
            let v = v.or(kept).unwrap();
            changed |= kept != Some(v);
            out.push((at, v));
        }
        old.mem.0 = out;
        changed
    }

    /// Admit `c` to the call's `table`, queueing its key in `work` where it is new or grew.
    fn admit(&self, o: &mut Run, t: &mut Table, c: Cf) {
        let key = self.key(&c);
        let (fresh, back) = match t.cfs.get_mut(&key) {
            Some(old) => (self.join_into(o, old, &c), old.back),
            None => {
                let back = c.back;
                t.cfs.insert(key.clone(), c);
                o.fail |= t.cfs.len() > self.caps.configs;
                (true, back)
            }
        };
        if fresh && t.queued.insert(key.clone()) {
            t.work.push(Reverse((back, key)));
        }
    }

    /// The slice's states after one call of entry `e` from `kv`.
    fn call(&self, o: &mut Run, kv: &[u8], e: &Entry) -> FxHashSet<Vec<u8>> {
        let mut out = FxHashSet::default();
        let u = (o.provs.one(U), 0);
        let mut r = [k(0); 16];
        for i in [R::A, R::X, R::Y] {
            r[i] = byte(u);
        }
        for (i, _) in R::STATUS_BITS {
            r[i] = V::of(Iv { lo: 0, hi: 1 }, u);
        }
        r[R::SP] = k(self.sp as u32);
        let kp: Vec<(u32, u32)> = self.cells.iter().map(|&c| o.provs.only(c as u32)).collect();
        let s = 0x100 | self.sp as u16;
        let mut mem = Mem::default();
        mem.insert(s.wrapping_add(1), k(RET as u32 & 0xff));
        mem.insert(s.wrapping_add(2), k((RET >> 8) as u32));
        let mut starts = vec![Cf {
            pc: e.pc,
            back: 0,
            r,
            kv: kv.to_vec(),
            kp,
            mem,
            weak: BTreeMap::new(),
        }];
        for (reg, vals) in e.regs.iter().filter(|r| r.0 < 16) {
            starts = (starts.iter())
                .flat_map(|c| {
                    vals.iter().map(move |&v| {
                        let mut c = c.clone();
                        c.r[*reg] = k(v as u32);
                        c
                    })
                })
                .collect();
        }
        let mut t = Table::default();
        for c in starts {
            self.admit(o, &mut t, c);
        }
        let (mut next, mut ch) = (Vec::new(), Vec::new());
        while let Some(Reverse((_, key))) = t.work.pop() {
            t.queued.remove(&key);
            let mut lane = vec![t.cfs[&key].clone()];
            while let Some(mut cf) = lane.pop() {
                loop {
                    if cf.pc == RET.wrapping_add(1) {
                        out.insert(cf.kv.clone());
                        break;
                    }
                    o.steps += 1;
                    if o.steps.is_multiple_of(SPEND)
                        && self.budget.spend(SPEND, self.budget.cap, self.caps.total)
                    {
                        o.fail = true;
                    }
                    if o.fail {
                        return out;
                    }
                    let Some(vs) = self.g.vars.get(&cf.pc) else {
                        break;
                    };
                    let Ok(opv) = self.read(o, &cf, cf.pc, (0, 0), &[], &mut 0) else {
                        o.fail = true;
                        return out;
                    };
                    let ops: Vec<u8> = (vs.iter().map(|v| v.op))
                        .filter(|&x| opv.top() || x as u32 == opv.lo)
                        .collect();
                    if let (&[op], true) = (&ops[..], self.kdev.is_empty()) {
                        let Ok(succ) = self.insn(o, &mut cf, op, &[]) else {
                            unreachable!("a fork needs a volatile read of the slice")
                        };
                        if let ([pc], CtrlKind::Next) = (&succ[..], Template::of(op).ctrl) {
                            cf.pc = *pc;
                            continue;
                        }
                        next.clear();
                        spread(cf, succ, &mut next);
                        for c in next.drain(..) {
                            self.admit(o, &mut t, c);
                        }
                        break;
                    }
                    // A fork at a volatile read or an opcode cell: each path goes on to its next
                    // transfer before joining.
                    for &op in &ops {
                        next.clear();
                        self.exec(o, &cf, op, &mut ch, &mut next);
                        match Template::of(op).ctrl {
                            CtrlKind::Next => lane.append(&mut next),
                            _ => {
                                for c in next.drain(..) {
                                    self.admit(o, &mut t, c);
                                }
                            }
                        }
                    }
                    break;
                }
            }
        }
        out
    }

    /// The states after one call of each entry from each of `kvs`, and what the calls found.
    fn calls(&self, kvs: &[Vec<u8>]) -> (Found, Vec<Vec<u8>>) {
        let mut o = Run {
            provs: Provs::new(),
            addrs: FxHashMap::default(),
            tops: FxHashMap::default(),
            need: FxHashSet::default(),
            fail: false,
            steps: 0,
        };
        let mut out = Vec::new();
        'all: for kv in kvs {
            for e in self.entries {
                out.extend(self.call(&mut o, kv, e));
                if o.fail || !o.need.is_empty() {
                    break 'all;
                }
            }
        }
        o.fail |= self
            .budget
            .spend(o.steps % SPEND, self.budget.cap, self.caps.total);
        let set = |p: &u32| o.provs.sets[*p as usize].clone();
        let found = Found {
            addrs: std::mem::take(&mut o.addrs),
            tops: (o.tops.iter())
                .map(|(&key, ps)| {
                    let ps = ps.iter().map(|((p, m), &h)| ((set(p), set(m)), h));
                    (key, ps.collect())
                })
                .collect(),
            need: o.need.iter().map(set).collect(),
            fail: o.fail,
            steps: o.steps,
            ..Found::default()
        };
        (found, out)
    }

    /// The least fixpoint of the slice's states from `mem0`'s under every entry, a layer of
    /// states at a time, starting from the states `prior` an earlier evaluation reached too
    /// (states it does not reach only add to what it finds): what it found.
    pub fn fixpoint(&self, prior: &[Vec<u8>]) -> Found {
        let kv0: Vec<u8> = self.cells.iter().map(|&c| self.mem0[c as usize]).collect();
        let mut seen: FxHashSet<Vec<u8>> = prior.iter().cloned().collect();
        seen.insert(kv0);
        let mut layer: Vec<Vec<u8>> = seen.iter().cloned().collect();
        let mut f = Found::default();
        while !layer.is_empty() && f.ok() {
            let parts: Vec<(Found, Vec<Vec<u8>>)> =
                layer.par_chunks(CHUNK).map(|c| self.calls(c)).collect();
            layer.clear();
            for (g, succ) in parts {
                f.absorb(g);
                layer.extend(succ.into_iter().filter(|s| seen.insert(s.clone())));
            }
            f.fail |= seen.len() > self.caps.states
                || self.budget.spend(0, self.budget.cap, self.caps.total);
            if f.fail {
                self.budget.stop.store(true, Ordering::Relaxed);
            }
        }
        for (i, &c) in self.cells.iter().enumerate() {
            let vs = seen.iter().map(|s| s[i] as u32);
            let (lo, hi) = vs.fold((0xff, 0), |(l, h), x| (l.min(x), h.max(x)));
            f.ranges.insert(c, [lo, hi]);
        }
        f.states = seen.len();
        f.seen = seen.into_iter().collect();
        f
    }
}

/// `c` at each of the PCs `succ`, onto `out`.
fn spread(mut c: Cf, mut succ: Vec<u16>, out: &mut Vec<Cf>) {
    let Some(last) = succ.pop() else {
        return;
    };
    let back = |c: &Cf, pc: u16| c.back + (pc < c.pc) as u16;
    for pc in succ {
        out.push(Cf {
            pc,
            back: back(&c, pc),
            ..c.clone()
        });
    }
    (c.back, c.pc) = (back(&c, last), last);
    out.push(c);
}

/// The configurations of one call by key and the keys to evaluate, by the transfers to a lower
/// PC taken and then the PC, least first: paths that part reach the instructions after them
/// about together and join there.
#[derive(Default)]
struct Table {
    cfs: FxHashMap<Box<[u8]>, Cf>,
    work: BinaryHeap<Reverse<(u16, Box<[u8]>)>>,
    queued: FxHashSet<Box<[u8]>>,
}

/// The bytes and volatile reads of a provenance set, without its flags.
fn cells(p: &[u32]) -> impl Iterator<Item = u32> + '_ {
    p.iter().copied().filter(|&e| e < J)
}

/// The bytes whose values differ on paths a provenance set joins.
fn joined(p: &[u32]) -> impl Iterator<Item = u32> + '_ {
    p.iter().copied().filter(|&e| e >= JC).map(|e| e & 0xffff)
}

/// Join into slice `s` (none: a new one) the bytes and volatile reads of `p`, or where it
/// holds them all already, the bytes whose joins `p` passed: whether a slice changed.
fn grow(sl: &mut Slices, p: &[u32], s: Option<&[u32]>) -> bool {
    let s = s.unwrap_or_default();
    sl.join(cells(p).chain(s.iter().copied()))
        || sl.join(joined(p).chain(s.iter().copied()).chain(cells(p)))
}

/// Slices, grown by the sets joined into them: disjoint in their bytes (a volatile read has
/// no state, so slices share them).
#[derive(Default)]
struct Slices {
    of: FxHashMap<u32, usize>,
    sets: Vec<Vec<u32>>,
}

impl Slices {
    /// Join `items` into one slice: whether any slice changed.
    fn join(&mut self, items: impl IntoIterator<Item = u32>) -> bool {
        let mut items: Vec<u32> = items.into_iter().collect();
        items.sort_unstable();
        items.dedup();
        let mut ids: Vec<usize> = items
            .iter()
            .filter_map(|e| self.of.get(e).copied())
            .collect();
        ids.sort_unstable();
        ids.dedup();
        let byte = |e: &u32| *e < DEV;
        let new = match &ids[..] {
            [i] => items
                .iter()
                .any(|e| self.sets[*i].binary_search(e).is_err()),
            [] => items.iter().any(byte) || !self.sets.contains(&items),
            _ => true,
        };
        if items.is_empty() || !new {
            return false;
        }
        let mut set: Vec<u32> = (ids.iter())
            .flat_map(|&i| std::mem::take(&mut self.sets[i]))
            .chain(items)
            .collect();
        set.sort_unstable();
        set.dedup();
        let id = self.sets.len();
        for &e in set.iter().filter(|e| byte(e)) {
            self.of.insert(e, id);
        }
        self.sets.push(set);
        true
    }
    fn live(&self) -> Vec<Vec<u32>> {
        self.sets
            .iter()
            .filter(|s| !s.is_empty())
            .cloned()
            .collect()
    }
    /// The slice holding every byte and volatile read of `p`, if one does (one byte at least).
    fn owner(&self, p: &[u32]) -> Option<&[u32]> {
        let first = *self.of.get(&cells(p).next()?)?;
        let s = &self.sets[first][..];
        cells(p).all(|e| s.binary_search(&e).is_ok()).then_some(s)
    }
}

/// The loads `targets` of `g` whose every address on every run lies in bytes `held`
/// (`[lo, hi)`, meeting no volatile range) ([`Bounds`]).
/// The program's entries `entries` are called at `sp`, from the runs `mems` (init's per
/// argument), in any order; `w` the bytes its stores may write ([`crate::analysis::stored`]);
/// the search within `caps`.
#[allow(clippy::too_many_arguments)]
pub fn bounds(
    g: &Graph,
    entries: &[Entry],
    sp: u8,
    vol: &[(u32, u32)],
    dev: &Devices,
    mems: &[Box<[u8; 0x10000]>],
    w: &FixedBitSet,
    targets: &FxHashSet<Key>,
    held: (u32, u32),
    caps: Caps,
) -> Bounds {
    // A pool of its own: a process forked after a search finds no threads of the global pool.
    let go = || search(g, entries, sp, vol, dev, mems, w, targets, held, caps);
    match rayon::ThreadPoolBuilder::new().build() {
        Ok(pool) => pool.install(go),
        Err(_) => go(),
    }
}

#[allow(clippy::too_many_arguments)]
fn search(
    g: &Graph,
    entries: &[Entry],
    sp: u8,
    vol: &[(u32, u32)],
    dev: &Devices,
    mems: &[Box<[u8; 0x10000]>],
    w: &FixedBitSet,
    targets: &FxHashSet<Key>,
    held: (u32, u32),
    caps: Caps,
) -> Bounds {
    if targets.is_empty() || mems.is_empty() {
        return Bounds::default();
    }
    let inside = |h: &Hull| {
        held.0 <= h[0] && h[1] < held.1 && !vol.iter().any(|&(a, b)| h[0] < b && a <= h[1])
    };
    let mut ranges: Vec<FxHashMap<u16, [u32; 2]>> = vec![FxHashMap::default(); mems.len()];
    let mut vbits = FixedBitSet::with_capacity(0x10000);
    for &(a, b) in vol {
        vbits.insert_range(a as usize..(b as usize).min(0x10000));
    }
    let total = AtomicU64::new(0);
    let none: Vec<Found> = Vec::new();
    let eval =
        |slice: &[u32], ranges: &[FxHashMap<u16, [u32; 2]>], keyed: &[u16], prior: &[Found]| {
            let budget = Budget {
                cap: caps.steps,
                spent: AtomicU64::new(0),
                stop: AtomicBool::new(false),
                total: &total,
            };
            let (b, s) = ((caps, &budget), (slice, keyed));
            (mems.par_iter().zip(ranges))
                .enumerate()
                .map(|(i, (m, r))| {
                    let seen = prior.get(i).map_or(&[][..], |f| &f.seen[..]);
                    Cx::new(g, w, &vbits, dev, entries, sp, targets, m, r, b, s).fixpoint(seen)
                })
                .collect::<Vec<Found>>()
        };
    let base = eval(&[], &ranges, &[], &none);
    if base.iter().any(|f| f.fail) {
        return Bounds::default();
    }
    // The loads the program reaches: those the evaluation with no slice visits.
    let reached = (targets.iter())
        .filter(|k| {
            base.iter()
                .any(|f| f.addrs.contains_key(k) || f.tops.contains_key(k))
        })
        .count();
    let mut sl = Slices::default();
    for ((p, _), h) in base.iter().flat_map(|f| f.tops.values().flatten()) {
        if !inside(h) {
            sl.join(cells(p));
        }
    }
    let mut found: FxHashMap<Vec<u32>, (usize, Vec<Found>)> = FxHashMap::default();
    // Other slices' bytes whose joins left a load's address unknown: they key configurations.
    let mut keyed: BTreeSet<u16> = BTreeSet::new();
    for round in 0..caps.rounds {
        let todo: Vec<Vec<u32>> = (sl.live().into_iter())
            .filter(|s| s.len() <= caps.cells)
            .filter(|s| {
                found
                    .get(s)
                    .is_none_or(|f| f.0 < round && f.1.iter().all(|f| !f.fail))
            })
            .collect();
        let ks: Vec<u16> = keyed.iter().copied().collect();
        let fs: Vec<Vec<Found>> = (todo.par_iter())
            .map(|s| {
                let prior = found.get(s).map_or(&none[..], |f| &f.1[..]);
                eval(s, &ranges, &ks, prior)
            })
            .collect();
        found.extend(todo.into_iter().zip(fs.into_iter().map(|f| (round, f))));
        let (mut grew, mut narrowed, mut rekeyed) = (false, false, false);
        for s in sl.live() {
            let Some((_, fs)) = found.get(&s).filter(|f| f.1.iter().all(|f| !f.fail)) else {
                continue;
            };
            for f in fs {
                for p in &f.need {
                    grew |= grow(&mut sl, p, Some(&s));
                }
                for ((p, _), _) in f.tops.values().flatten().filter(|t| !inside(t.1)) {
                    if cells(p).any(|e| s.binary_search(&e).is_ok()) {
                        grew |= grow(&mut sl, p, Some(&s));
                    } else if sl.owner(p).is_none() {
                        grew |= sl.join(cells(p));
                    } else {
                        let other = joined(p).filter(|&c| sl.of.contains_key(&c));
                        rekeyed |= other.fold(false, |n, c| keyed.insert(c as u16) | n);
                    }
                }
            }
            if fs.iter().all(Found::ok) {
                for (r, f) in ranges.iter_mut().zip(fs) {
                    for (&c, &[lo, hi]) in &f.ranges {
                        let e = r.entry(c).or_insert([0, 0xff]);
                        let m = [e[0].max(lo), e[1].min(hi)];
                        narrowed |= m != *e;
                        *e = m;
                    }
                }
            }
        }
        // Done when no slice changes, or every load the program reaches is proven.
        let proven = judge(&sl, &found, &base, targets, &inside).loads.len();
        if (!grew && !narrowed && !rekeyed) || proven == reached {
            break;
        }
        if !narrowed && !rekeyed {
            found.values_mut().for_each(|f| f.0 = round + 1);
        }
    }
    judge(&sl, &found, &base, targets, &inside)
}

/// What the completed evaluations `found` of slices `sl` (none: `base`, the evaluation with
/// none) prove of the loads `targets`, inside the bytes `inside` admits ([`Bounds`]).
fn judge(
    sl: &Slices,
    found: &FxHashMap<Vec<u32>, (usize, Vec<Found>)>,
    base: &[Found],
    targets: &FxHashSet<Key>,
    inside: &dyn Fn(&Hull) -> bool,
) -> Bounds {
    let mut out = Bounds::default();
    let live = sl.live();
    let runs: Vec<(&[u32], &[Found])> = match live.is_empty() {
        true => vec![(&[][..], base)],
        false => (live.iter())
            .filter_map(|s| {
                let fs = found.get(s).filter(|f| f.1.iter().all(Found::ok))?;
                Some((&s[..], &fs.1[..]))
            })
            .collect(),
    };
    let good = |s: &[u32]| runs.iter().any(|r| r.0 == s);
    out.slices = runs.len();
    out.states = runs.iter().flat_map(|r| r.1.iter().map(|f| f.states)).sum();
    let joined = |p: &[u32]| p.iter().any(|&e| e == J || e >= JC);
    let meets = |p: &[u32], s: &[u32]| cells(p).any(|e| s.binary_search(&e).is_ok());
    for &key in targets {
        // Whether slice `s`'s evaluations know every address of the load whose provenance
        // meets `s` outside the held bytes.
        let own = |s: &[u32]| {
            (runs.iter().filter(|r| r.0 == s))
                .flat_map(|r| {
                    r.1.iter()
                        .flat_map(|f| f.tops.get(&key).into_iter().flatten())
                })
                .all(|((p, _), x)| inside(x) || !meets(p, s))
        };
        let mut h: Option<Hull> = None;
        let mut fine = !runs.is_empty();
        for (s, fs) in &runs {
            for f in fs.iter() {
                if let Some(&x) = f.addrs.get(&key) {
                    h = Some(h.map_or(x, |y| hull(x, y)));
                }
                for ((p, m), x) in f.tops.get(&key).into_iter().flatten() {
                    if inside(x) {
                        h = Some(h.map_or(*x, |y| hull(*x, y)));
                        continue;
                    }
                    let owner = sl.owner(p).filter(|o| *o != *s && good(o));
                    let ok = !p.contains(&U)
                        && owner.is_some_and(|o| {
                            !joined(p) || (!p.contains(&STAR) && meets(m, o) && own(o))
                        });
                    fine &= ok;
                }
            }
        }
        // From the least base on: the base an indexed operand names is the program's, so the
        // array the load reads starts where the code says, whatever the data.
        let h = h.map(|h| [h[0].min(h[2]), h[1], h[2], h[3]]);
        if let (true, Some(h)) = (fine, h.filter(|h| inside(h))) {
            out.loads.insert(key, h);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn provenance_sets_intern_their_unions() {
        let mut p = Provs::new();
        let (a, b) = (p.one(3), p.one(1));
        let ab = p.union(a, b);
        assert_eq!(&*p.sets[ab as usize], &[1, 3]);
        assert_eq!(p.union(b, a), ab);
        assert_eq!(p.union(ab, a), ab);
        assert_eq!(p.union(0, a), a);
    }

    #[test]
    fn joins_keep_equal_values_and_mark_differing_ones() {
        let mut p = Provs::new();
        let (x, y) = (p.only(7), p.only(9));
        let xy = p.both(x, y);
        let e = join(&mut p, k(4).with(x), k(4).with(xy), Some(0x10));
        assert_eq!((e.lo, e.hi, e.p, e.m), (4, 4, xy.0, x.1));
        let j = join(&mut p, k(4).with(x), k(6), Some(0x10));
        assert_eq!((j.lo, j.hi, j.m), (4, 6, 0));
        assert_eq!(&*p.sets[j.p as usize], &[7, JC | 0x10]);
        let r = join(&mut p, k(4).with(x), k(6), None);
        assert_eq!(&*p.sets[r.p as usize], &[7, J]);
        let t = join(&mut p, byte(x), byte(y), Some(0x10));
        assert_eq!((&*p.sets[t.p as usize], t.m), (&[7, 9][..], 0));
        assert_eq!(p.inter(xy.0, x.0), x.0);
    }

    #[test]
    fn slices_join_what_they_share() {
        let mut s = Slices::default();
        assert!(s.join([1, 2]) && s.join([5]) && !s.join([2]));
        assert_eq!(s.owner(&[1, 2, J]), Some(&[1, 2][..]));
        assert_eq!(s.owner(&[1, 5]), None);
        assert!(s.join([2, DEV | 3]) && s.join([5, DEV | 3]) && !s.join([5, DEV | 3]));
        assert_eq!(s.owner(&[1, DEV | 3]), Some(&[1, 2, DEV | 3][..]));
        assert_eq!(s.owner(&[DEV | 3]), None);
        assert!(s.join([DEV | 4]) && !s.join([DEV | 4]));
        assert!(s.join([2, 5]));
        assert_eq!(s.live(), vec![vec![DEV | 4], vec![1, 2, 5, DEV | 3]]);
        assert!(!s.join([]));
    }

    #[test]
    fn bytes_of_values() {
        let v = V {
            lo: 0x10,
            hi: 0x20,
            p: 3,
            m: 2,
        };
        assert_eq!(v.byte(0), v);
        assert_eq!(v.byte(1), byte((3, 2)));
        assert_eq!(k(0x1234).byte(1), k(0x12));
    }
}
