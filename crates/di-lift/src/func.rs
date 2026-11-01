//! Functions and stack heights: which `JSR`s become `CALL`s (callees that return at the
//! height they were entered, never touching the return address), the stack heights each
//! instruction is planned at relative to its function's entry (several in an outermost plan,
//! from the heights the facts saw), the bank configuration each runs in, and the control
//! each instruction lifts to.

use crate::analysis::{
    absint, operand_bytes, page1_check, stack_delta, Access, Check, Env, Facts, Flow, Graph, Iv,
    Tg, Var, TOP_ENV,
};
use crate::bank::{Back, BankMap};
use di_pcode::{reg as R, Template};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::{BTreeMap, BTreeSet};

/// Stack height relative to the function entry (pushes are negative).
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Debug)]
pub enum H {
    Known(i32),
    Unknown,
}

impl H {
    fn add(self, d: i32) -> H {
        match self {
            H::Known(x) => H::Known(x + d),
            H::Unknown => H::Unknown,
        }
    }
    fn merge(old: Option<H>, new: H) -> H {
        match old {
            None => new,
            Some(o) if o == new => o,
            Some(_) => H::Unknown,
        }
    }
}

/// Whether an entry's instruction leaves the program: never, always (its height reaches the
/// entry's return), or when SP has reached the exit threshold at run time.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Exit {
    No,
    Static,
    Dynamic,
}

/// Lifted control of one instruction variant.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Ctl {
    Next(u16),
    Br {
        t: Tg,
        f: u16,
    },
    Jmp(Tg),
    JmpInd(Vec<u16>),
    /// `JSR` to a function lifted separately (entered in configuration `c`).
    Call {
        f: u16,
        ret: u16,
        c: u8,
    },
    /// `JSR` whose callee is lifted inline: push the return address and jump.
    PushJmp {
        t: Tg,
        ret: u16,
    },
    /// `RTS` at the callee's entry height.
    Return,
    /// `RTS` pulling an address this function pushed (or an exit).
    Rts(Vec<u16>),
    Rti(Vec<u16>),
    Brk(Vec<u16>),
    Jam,
}

/// A plan node: instruction start, stack height and bank configuration.
pub type Key = (u16, H, u8);

/// One instruction variant: its lifted control, exit, the nodes it transfers to, its
/// template accesses at this node, and whether it may write the bank port (its successors
/// then are each configuration the facts saw them in, dispatched at run time).
#[derive(Clone, Debug)]
pub struct Arm {
    pub ctl: Ctl,
    pub exit: Exit,
    pub succ: Vec<Key>,
    pub acc: Vec<Access>,
    pub port: bool,
}

#[derive(Clone, Debug)]
pub struct Node {
    pub arms: Vec<Arm>,
}

#[derive(Clone, Debug)]
pub struct Plan {
    pub pc: u16,
    pub top: bool,
    /// Nodes by instruction start, height and configuration: several heights per start only
    /// in an outermost plan.
    pub nodes: BTreeMap<Key, Node>,
    /// The configurations the function is entered in (one for a callee).
    pub entry: Vec<u8>,
    /// A callee returning at its entry height with no access above it.
    pub balanced: bool,
    /// The SP an outermost plan is specialized to at entry (after its return address).
    pub sp: Option<u8>,
    /// Some node sets SP (`TXS`, `TAS`, `LAS`).
    pub sets_sp: bool,
}

impl Plan {
    /// Distinct instruction starts, ascending.
    pub fn pcs(&self) -> impl Iterator<Item = u16> + '_ {
        let mut last = None;
        self.nodes
            .keys()
            .map(|k| k.0)
            .filter(move |&p| last.replace(p) != Some(p))
    }
    /// Slot indices (offset `-i` from the entry SP) the plan pushes to.
    pub fn pushed(&self, g: &Graph) -> FixedBitSet {
        let mut p = FixedBitSet::with_capacity(0x100);
        for (&(pc, h, _), n) in &self.nodes {
            let H::Known(h) = h else { continue };
            for (v, a) in g.vars[&pc].iter().zip(&n.arms) {
                for i in -h..-h + stack_bytes(v.op, &a.ctl).0 {
                    if (0..0x100).contains(&i) {
                        p.insert(i as usize);
                    }
                }
            }
        }
        p
    }
    /// The page-1 range an access `i` of `op` at `pc` (interval `addr`) is guarded to in an
    /// outermost plan specialized to its entry SP: the `[lo, hi]` the facts (`seen`) saw it
    /// reach, when a check before the instruction covers it and its interval is wider.
    pub fn narrowed(
        &self,
        seen: &FxHashMap<u16, (u16, u16)>,
        pc: u16,
        op: u8,
        i: usize,
        addr: Iv,
    ) -> Option<Iv> {
        let &(lo, hi) = seen.get(&pc)?;
        let r = Iv {
            lo: lo as u32,
            hi: hi as u32,
        };
        let wider = addr.lo.max(0x100) < r.lo || addr.hi.min(0x1ff) > r.hi;
        (self.top && self.sp.is_some() && wider && page1_check(op, i) != Check::No).then_some(r)
    }
    /// Slot indices an access at `addr` may hit: located from the entry SP when the plan is
    /// specialized to it, else any.
    pub fn offsets(&self, addr: Iv) -> std::ops::Range<usize> {
        let (lo, hi) = (addr.lo.max(0x100) as i64, addr.hi.min(0x1ff) as i64);
        match self.sp {
            _ if lo > hi => 0..0,
            Some(s) => {
                let i = |a: i64| (s as i64 + 0x100 - a).max(0) as usize;
                i(hi)..i(lo - 1)
            }
            None => 0..0x100,
        }
    }
    /// Whether an access at `addr` from a node at height `h` may hit a stack byte kept out of
    /// memory: in a callee any page-1 byte; in an outermost plan one of `slots` above SP, or
    /// anywhere below the entry once SP may be set (a later pull may read it).
    pub fn kept(&self, slots: &FixedBitSet, h: H, addr: Iv) -> bool {
        if !addr.meets(0x100, 0x200) {
            return false;
        }
        let (true, H::Known(h)) = (self.top, h) else {
            return true;
        };
        self.offsets(addr)
            .any(|i| slots.contains(i) && (self.sets_sp || (i as i32) < -h))
    }
    /// Slot indices a pull may read before this call pushed them (only after SP is set): a
    /// forward must-analysis of the pushed slots over the nodes, whose sets only shrink.
    pub fn pulled_unpushed(&self, g: &Graph) -> FixedBitSet {
        let mut out = FixedBitSet::with_capacity(0x100);
        if !self.sets_sp {
            return out;
        }
        let mut w: FxHashMap<Key, FixedBitSet> = FxHashMap::default();
        let mut work: Vec<Key> = self.roots().collect();
        for &k in &work {
            w.insert(k, FixedBitSet::with_capacity(0x100));
        }
        while let Some(k) = work.pop() {
            let (pc, H::Known(h), _) = k else { continue };
            let cur = w[&k].clone();
            for (v, a) in g.vars[&pc].iter().zip(&self.nodes[&k].arms) {
                let (push, pull) = stack_bytes(v.op, &a.ctl);
                let mut next = cur.clone();
                for i in (-h - pull..-h).filter(|i| (0..0x100).contains(i)) {
                    if !cur.contains(i as usize) {
                        out.insert(i as usize);
                    }
                }
                for i in (-h..-h + push).filter(|i| (0..0x100).contains(i)) {
                    next.insert(i as usize);
                }
                for s in a.succ.iter().filter(|s| self.nodes.contains_key(s)) {
                    let changed = match w.get_mut(s) {
                        None => {
                            w.insert(*s, next.clone());
                            true
                        }
                        Some(o) => {
                            let before = o.count_ones(..);
                            o.intersect_with(&next);
                            o.count_ones(..) != before
                        }
                    };
                    if changed {
                        work.push(*s);
                    }
                }
            }
        }
        out
    }
    /// Refine every node's access intervals for entry SP `s` by a forward interval dataflow
    /// over the nodes, SP being `s` plus the node height at each; a register that grows at
    /// a node already processed in reverse postorder (a loop header) widens to unknown, so
    /// after its first visit each register of a node changes at most once more.
    pub fn refine(&mut self, g: &Graph, img: &[u8; 0x10000], s: u8) {
        self.sp = Some(s);
        let keys: Vec<Key> = self.nodes.keys().copied().collect();
        let idx: FxHashMap<Key, usize> = keys.iter().enumerate().map(|(i, &k)| (k, i)).collect();
        let succ = |i: usize| -> Vec<usize> {
            self.nodes[&keys[i]]
                .arms
                .iter()
                .flat_map(|a| &a.succ)
                .filter_map(|k| idx.get(k).copied())
                .collect()
        };
        let roots: Vec<usize> = self.roots().map(|k| idx[&k]).collect();
        let mut rank = vec![usize::MAX; keys.len()];
        let mut post = Vec::with_capacity(keys.len());
        let mut stack: Vec<(usize, Vec<usize>)> = roots.iter().map(|&r| (r, succ(r))).collect();
        for &r in &roots {
            rank[r] = 0;
        }
        while let Some((i, ss)) = stack.last_mut() {
            match ss.pop() {
                Some(j) if rank[j] == usize::MAX => {
                    rank[j] = 0;
                    stack.push((j, succ(j)));
                }
                Some(_) => {}
                None => {
                    post.push(*i);
                    stack.pop();
                }
            }
        }
        for (r, &i) in post.iter().rev().enumerate() {
            rank[i] = r;
        }
        let mut env: Vec<Option<Env>> = vec![None; keys.len()];
        let mut done = vec![false; keys.len()];
        let mut work = BTreeSet::new();
        for &r in &roots {
            env[r] = Some(TOP_ENV);
            work.insert((rank[r], r));
        }
        while let Some((_, i)) = work.pop_first() {
            done[i] = true;
            let (pc, h, _) = keys[i];
            let mut e = env[i].unwrap();
            e[R::SP] = match h {
                H::Known(x) if (0..0x100).contains(&(s as i32 + x)) => {
                    Iv::exact((s as i32 + x) as u32)
                }
                _ => TOP_ENV[R::SP],
            };
            let node = self.nodes.get_mut(&keys[i]).unwrap();
            for (v, arm) in g.vars[&pc].iter().zip(&mut node.arms) {
                let t = Template::of(v.op);
                let mut x = e;
                arm.acc.clear();
                absint(
                    t.ops(),
                    operand_bytes(img, &g.cells, pc, t.len),
                    &mut x,
                    &mut arm.acc,
                );
                if matches!(arm.ctl, Ctl::Call { .. }) {
                    x = TOP_ENV;
                }
                for j in arm.succ.iter().filter_map(|k| idx.get(k).copied()) {
                    let new = match env[j] {
                        None => x,
                        Some(o) => std::array::from_fn(|r| {
                            let m = Iv {
                                lo: o[r].lo.min(x[r].lo),
                                hi: o[r].hi.max(x[r].hi),
                            };
                            if m != o[r] && done[j] {
                                TOP_ENV[r]
                            } else {
                                m
                            }
                        }),
                    };
                    if env[j] != Some(new) {
                        env[j] = Some(new);
                        work.insert((rank[j], j));
                    }
                }
            }
        }
    }
    /// The entry nodes, one per entry configuration.
    pub fn roots(&self) -> impl Iterator<Item = Key> + '_ {
        self.entry.iter().map(|&c| (self.pc, H::Known(0), c))
    }
    /// Functions `CALL`ed with their entry configuration, once per call site and node.
    pub fn callees(&self) -> impl Iterator<Item = (u16, u8)> + '_ {
        self.nodes
            .values()
            .flat_map(|n| &n.arms)
            .filter_map(|a| match a.ctl {
                Ctl::Call { f, c, .. } => Some((f, c)),
                _ => None,
            })
    }
}

/// Bytes instruction `op` lifted to `ctl` pushes and pulls (a `CALL` pushes none).
fn stack_bytes(op: u8, ctl: &Ctl) -> (i32, i32) {
    match (ctl, stack_delta(op)) {
        (Ctl::PushJmp { .. }, _) => (2, 0),
        (Ctl::Brk(_), _) => (3, 0),
        (Ctl::Rts(_), _) => (0, 2),
        (Ctl::Rti(_), _) => (0, 3),
        (Ctl::Next(_), Some(d)) => ((d < 0) as i32, (d > 0) as i32),
        _ => (0, 0),
    }
}

struct Step {
    ctl: Ctl,
    exit: Exit,
    succ: Vec<(u16, H)>,
    /// `(height, return site)` of an inline `JSR`.
    ret: Option<(H, u16)>,
    unbalanced: bool,
}

/// Whether variant `v` may store to a cell of the bank port.
fn writes_port(v: &Var, bank: Option<&BankMap>) -> bool {
    bank.is_some_and(|m| {
        v.acc.iter().any(|a| {
            a.store
                && (a.addr.meets(m.dir as u32, m.dir as u32 + 1)
                    || a.addr.meets(m.data as u32, m.data as u32 + 1))
        })
    })
}

fn exit_of(top: bool, h: H, raises: bool) -> Exit {
    match (top, h) {
        (true, H::Known(x)) if x >= 2 => Exit::Static,
        (true, H::Unknown) if raises => Exit::Dynamic,
        _ => Exit::No,
    }
}

#[allow(clippy::too_many_arguments)]
fn step(
    v: &Var,
    h: H,
    c: u8,
    top: bool,
    calls: &FxHashSet<u16>,
    rets: &FxHashMap<i32, BTreeSet<u16>>,
    all_rets: &BTreeSet<u16>,
) -> Step {
    let at = |ts: &[u16], h: H| ts.iter().map(|&t| (t, h)).collect::<Vec<_>>();
    let mut s = Step {
        ctl: Ctl::Jam,
        exit: Exit::No,
        succ: Vec::new(),
        ret: None,
        unbalanced: false,
    };
    // An `RTS`/`RTI` pulling `n` bytes at `h`: an exit, a return, or a jump to its facts plus
    // the return sites inline `JSR`s pushed at the height it lands on.
    let pull = |s: &mut Step, n: i32, facts: &[u16]| -> Vec<u16> {
        let h2 = h.add(n);
        s.exit = exit_of(top, h2, true);
        s.unbalanced = !top && !matches!(h2, H::Known(x) if x <= 0);
        if s.exit == Exit::Static {
            return Vec::new();
        }
        let mut t: BTreeSet<u16> = facts.iter().copied().collect();
        match h2 {
            H::Known(x) => t.extend(rets.get(&x).into_iter().flatten()),
            H::Unknown => t.extend(all_rets),
        }
        let t: Vec<u16> = t.into_iter().collect();
        s.succ = at(&t, h2);
        t
    };
    match &v.flow {
        Flow::Next(n) => {
            let d = stack_delta(v.op);
            let h2 = d.map_or(H::Unknown, |d| h.add(d));
            s.ctl = Ctl::Next(*n);
            s.exit = exit_of(top, h2, d != Some(0) && d != Some(-1));
            s.unbalanced = !top && matches!(h2, H::Unknown | H::Known(1..));
            if s.exit != Exit::Static {
                s.succ = vec![(*n, h2)];
            }
        }
        Flow::Br { t, f } => {
            s.succ = at(t.all(), h);
            s.succ.push((*f, h));
            s.ctl = Ctl::Br {
                t: t.clone(),
                f: *f,
            };
        }
        Flow::Jmp(t) => {
            s.succ = at(t.all(), h);
            s.ctl = Ctl::Jmp(t.clone());
        }
        Flow::JmpInd(ts) => {
            s.succ = at(ts, h);
            s.ctl = Ctl::JmpInd(ts.clone());
        }
        Flow::Jsr { t: Tg::At(f), ret } if calls.contains(f) => {
            s.succ = vec![(*ret, h)];
            s.ctl = Ctl::Call {
                f: *f,
                ret: *ret,
                c,
            };
        }
        Flow::Jsr { t, ret } => {
            s.succ = at(t.all(), h.add(-2));
            s.ret = Some((h, *ret));
            s.ctl = Ctl::PushJmp {
                t: t.clone(),
                ret: *ret,
            };
        }
        Flow::Rts(f) => {
            if !top && h == H::Known(0) {
                s.ctl = Ctl::Return;
            } else {
                let t = pull(&mut s, 2, f);
                s.ctl = Ctl::Rts(t);
            }
        }
        Flow::Rti(f) => {
            let t = pull(&mut s, 3, f);
            s.ctl = Ctl::Rti(t);
        }
        Flow::Brk(ts) => {
            s.succ = at(ts, h.add(-3));
            s.ctl = Ctl::Brk(ts.clone());
        }
        Flow::Jam => {}
    }
    s
}

/// What the facts say about instruction starts: the heights each was executed at relative to
/// its outermost entry and which were executed, the configurations each ran in, the starts
/// not to plan, and the bank map.
pub struct Seen<'a> {
    heights: FxHashMap<u16, Vec<i32>>,
    starts: FxHashSet<u16>,
    banks: FxHashMap<u16, Vec<u8>>,
    /// Starts that are not planned: edges to them deopt.
    banned: FxHashSet<u16>,
    bank: Option<&'a BankMap>,
}

impl<'a> Seen<'a> {
    pub fn new(f: &Facts, banned: FxHashSet<u16>, bank: Option<&'a BankMap>) -> Self {
        Seen {
            heights: f
                .heights
                .iter()
                .map(|(p, h)| (*p, h.iter().map(|&x| x as i32).collect()))
                .collect(),
            starts: f.starts.iter().copied().collect(),
            banks: f.banks.iter().cloned().collect(),
            banned,
            bank,
        }
    }
}

struct Planner<'a, 'b> {
    g: &'a Graph,
    top: bool,
    seen: &'a Seen<'b>,
    nodes: BTreeSet<Key>,
    /// Per instruction start: its node height in a callee, the first static height reaching
    /// it in an outermost plan.
    first: FxHashMap<u16, H>,
    /// Per instruction start never executed: the first configuration reaching it.
    first_c: FxHashMap<u16, u8>,
}

impl Planner<'_, '_> {
    /// The nodes an edge to `t` at height `h` enters: in an outermost plan each height `t`
    /// was executed at when `h` is unknown; in a callee `t`'s node when its height covers `h`.
    fn resolve(&self, t: u16, h: H) -> Vec<H> {
        match (self.top, h, self.first.get(&t)) {
            (true, H::Unknown, _) => self
                .seen
                .heights
                .get(&t)
                .into_iter()
                .flatten()
                .map(|&x| H::Known(x))
                .collect(),
            (false, _, Some(&o)) if H::merge(Some(o), h) == o => vec![o],
            _ => vec![h],
        }
    }
    /// The configurations an edge to `t` in `c` enters (each `t` ran in when `c` is unknown,
    /// after a port write).
    fn configs(&self, t: u16, c: Option<u8>) -> Vec<u8> {
        match c {
            Some(c) => vec![c],
            None => self
                .seen
                .banks
                .get(&t)
                .cloned()
                .unwrap_or(vec![self.g.cfg[&t]]),
        }
    }
    /// Whether `t` is fetched in configuration `c` from the backing it was decoded from.
    fn fetchable(&self, t: u16, c: u8) -> bool {
        self.seen.bank.is_none_or(|m| {
            let len = Template::of(self.g.img[t as usize]).len as u16;
            (0..len).all(|i| {
                let a = t.wrapping_add(i);
                match m.back(c, a) {
                    Back::Rom => self.g.rom_code.contains(a as usize),
                    Back::Ram => !self.g.rom_code.contains(a as usize),
                    Back::Io => false,
                }
            })
        })
    }
    /// Record the edge to `t` at `h` in `c`; the nodes it creates. An outermost plan admits
    /// the heights `t` was executed at (or the first static one if it never was); a callee
    /// merges per start, a conflict making an executed start unknown and leaving any other to
    /// deopt. Configurations are those `t` ran in, or the first reaching it if it never ran,
    /// from which its bytes are fetched as decoded.
    fn admit(&mut self, t: u16, h: H, c: Option<u8>) -> Vec<Key> {
        let mut cs = Vec::new();
        for c in self.configs(t, c) {
            let ok = match self.seen.banks.get(&t) {
                Some(xs) => xs.contains(&c),
                None => *self.first_c.entry(t).or_insert(c) == c,
            };
            if ok && self.fetchable(t, c) {
                cs.push(c);
            }
        }
        let mut out = Vec::new();
        let hs = if self.top {
            let mut hs = Vec::new();
            for x in self.resolve(t, h) {
                let ok = match self.seen.heights.get(&t) {
                    Some(xs) => matches!(x, H::Known(v) if xs.contains(&v)),
                    None => *self.first.entry(t).or_insert(x) == x,
                };
                if ok {
                    hs.push(x);
                }
            }
            hs
        } else {
            match self.first.get(&t) {
                None => {
                    self.first.insert(t, h);
                    vec![h]
                }
                Some(&o) if H::merge(Some(o), h) == o => vec![o],
                Some(&o) if self.seen.starts.contains(&t) => {
                    let old: Vec<Key> = self
                        .nodes
                        .range((t, o, 0)..=(t, o, u8::MAX))
                        .copied()
                        .collect();
                    for k in old {
                        self.nodes.remove(&k);
                        if self.nodes.insert((t, H::Unknown, k.2)) {
                            out.push((t, H::Unknown, k.2));
                        }
                    }
                    self.first.insert(t, H::Unknown);
                    vec![H::Unknown]
                }
                Some(_) => vec![],
            }
        };
        for &x in &hs {
            for &c in &cs {
                if self.nodes.insert((t, x, c)) {
                    out.push((t, x, c));
                }
            }
        }
        out
    }
}

/// Plan the function entered at `pc` in configurations `entry` given the `JSR` targets lifted
/// as calls; the worklist ends as nodes are only added (finitely many heights per start: the
/// facts' or the first static one outermost, one known then at most unknown in a callee;
/// configurations from the facts or the first static one) and return sites only grow.
pub fn plan(
    g: &Graph,
    pc: u16,
    entry: Vec<u8>,
    top: bool,
    calls: &FxHashSet<u16>,
    seen: &Seen,
) -> Plan {
    let mut pl = Planner {
        g,
        top,
        seen,
        nodes: BTreeSet::new(),
        first: FxHashMap::default(),
        first_c: FxHashMap::default(),
    };
    let mut rets: FxHashMap<i32, BTreeSet<u16>> = FxHashMap::default();
    let mut all_rets: BTreeSet<u16> = BTreeSet::new();
    let mut pulls: FxHashSet<Key> = FxHashSet::default();
    pl.first.insert(pc, H::Known(0));
    let mut work: Vec<Key> = entry.iter().map(|&c| (pc, H::Known(0), c)).collect();
    pl.nodes.extend(work.iter().copied());
    let bank = seen.bank;
    while let Some((p, h, c)) = work.pop() {
        if !pl.nodes.contains(&(p, h, c)) {
            continue;
        }
        for v in &g.vars[&p] {
            if matches!(v.flow, Flow::Rts(_) | Flow::Rti(_)) {
                pulls.insert((p, h, c));
            }
            let s = step(v, h, c, top, calls, &rets, &all_rets);
            if let Some((hr, r)) = s.ret {
                let new = match hr {
                    H::Known(x) => rets.entry(x).or_default().insert(r),
                    H::Unknown => false,
                } | all_rets.insert(r);
                if new {
                    work.extend(pulls.iter().copied());
                }
            }
            let tc = (!port_arm(v, &s.ctl, bank)).then_some(c);
            for (t, ht) in s.succ {
                if g.vars.contains_key(&t) && !pl.seen.banned.contains(&t) {
                    work.extend(pl.admit(t, ht, tc));
                }
            }
        }
    }
    let mut balanced = true;
    let nodes = pl
        .nodes
        .iter()
        .map(|&(p, h, c)| {
            let arms = g.vars[&p]
                .iter()
                .map(|v| {
                    let s = step(v, h, c, top, calls, &rets, &all_rets);
                    balanced &= !s.unbalanced;
                    let port = port_arm(v, &s.ctl, bank);
                    let tc = (!port).then_some(c);
                    let succ = s
                        .succ
                        .iter()
                        .flat_map(|&(t, ht)| {
                            let cs = pl.configs(t, tc);
                            pl.resolve(t, ht)
                                .into_iter()
                                .flat_map(move |x| cs.clone().into_iter().map(move |c| (t, x, c)))
                        })
                        .collect();
                    Arm {
                        ctl: s.ctl,
                        exit: s.exit,
                        succ,
                        acc: v.acc.clone(),
                        port,
                    }
                })
                .collect();
            ((p, h, c), Node { arms })
        })
        .collect();
    Plan {
        pc,
        top,
        nodes,
        entry,
        balanced,
        sp: None,
        sets_sp: pl.nodes.iter().any(|(p, _, _)| {
            g.vars[p]
                .iter()
                .any(|v| matches!(v.flow, Flow::Next(_)) && stack_delta(v.op).is_none())
        }),
    }
}

/// Whether an arm may change the configuration: it stores to the port, or `CALL`s with a
/// bank map (cleared by [`plan_all`] for callees that cannot).
fn port_arm(v: &Var, ctl: &Ctl, bank: Option<&BankMap>) -> bool {
    writes_port(v, bank) || bank.is_some() && matches!(ctl, Ctl::Call { .. })
}

/// Plans for `entries` (outermost, entered in the configurations given per entry) and every
/// callee they reach per entry configuration, entries first; the call targets are the static
/// `JSR` targets whose plans are balanced, by a decreasing fixpoint. A `CALL` keeps its
/// configuration unless its callee may write the port (a least fixpoint over the plans).
pub fn plan_all(g: &Graph, entries: &[(u16, Vec<u8>)], seen: &Seen) -> Vec<Plan> {
    let mut calls: FxHashSet<u16> = g
        .vars
        .values()
        .flatten()
        .filter_map(|v| match v.flow {
            Flow::Jsr { t: Tg::At(f), .. } if g.vars.contains_key(&f) => Some(f),
            _ => None,
        })
        .collect();
    loop {
        let bad: Vec<u16> = calls
            .iter()
            .copied()
            .filter(|&f| !plan(g, f, vec![g.cfg[&f]], false, &calls, seen).balanced)
            .collect();
        if bad.is_empty() {
            break;
        }
        for b in bad {
            calls.remove(&b);
        }
    }
    let mut plans: Vec<Plan> = entries
        .iter()
        .map(|(e, cs)| plan(g, *e, cs.clone(), true, &calls, seen))
        .collect();
    let mut idx: FxHashMap<(u16, u8), usize> = FxHashMap::default();
    let mut i = 0;
    while i < plans.len() {
        let callees: Vec<(u16, u8)> = plans[i].callees().collect();
        for (f, c) in callees {
            if let std::collections::hash_map::Entry::Vacant(e) = idx.entry((f, c)) {
                e.insert(plans.len());
                plans.push(plan(g, f, vec![c], false, &calls, seen));
            }
        }
        i += 1;
    }
    if seen.bank.is_some() {
        let own: Vec<bool> = plans
            .iter()
            .map(|p| {
                p.nodes.iter().any(|(k, n)| {
                    g.vars[&k.0].iter().any(|v| writes_port(v, seen.bank)) && !n.arms.is_empty()
                })
            })
            .collect();
        let mut porty = own;
        loop {
            let next: Vec<bool> = plans
                .iter()
                .enumerate()
                .map(|(i, p)| porty[i] || p.callees().any(|k| porty[idx[&k]]))
                .collect();
            if next == porty {
                break;
            }
            porty = next;
        }
        for p in &mut plans {
            for (k, n) in p.nodes.iter_mut() {
                for (v, a) in g.vars[&k.0].iter().zip(&mut n.arms) {
                    if let Ctl::Call { f, c, .. } = a.ctl {
                        if !porty[idx[&(f, c)]] && !writes_port(v, seen.bank) {
                            a.port = false;
                            a.succ.retain(|s| s.2 == c);
                        }
                    }
                }
            }
        }
    }
    plans
}
