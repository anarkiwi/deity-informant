//! Functions and stack heights: which `JSR`s become `CALL`s (callees that return at the
//! height they were entered, never touching the return address), the stack heights each
//! instruction is planned at relative to its function's entry (several in an outermost plan,
//! from the heights the facts saw), and the control each instruction lifts to.

use crate::analysis::{
    absint, operand_bytes, page1_check, stack_delta, Access, Check, Env, Facts, Flow, Graph, Iv,
    Tg, Var, TOP_ENV,
};
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
    /// `JSR` to a function lifted separately.
    Call {
        f: u16,
        ret: u16,
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

/// One instruction variant: its lifted control, exit, the `(pc, height)` nodes it
/// transfers to, and its template accesses at this node.
#[derive(Clone, Debug)]
pub struct Arm {
    pub ctl: Ctl,
    pub exit: Exit,
    pub succ: Vec<(u16, H)>,
    pub acc: Vec<Access>,
}

#[derive(Clone, Debug)]
pub struct Node {
    pub arms: Vec<Arm>,
}

#[derive(Clone, Debug)]
pub struct Plan {
    pub pc: u16,
    pub top: bool,
    /// Nodes by instruction start and height: several heights per start only in an
    /// outermost plan.
    pub nodes: BTreeMap<(u16, H), Node>,
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
        for (&(pc, h), n) in &self.nodes {
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
        let mut w: FxHashMap<(u16, H), FixedBitSet> = FxHashMap::default();
        let entry = (self.pc, H::Known(0));
        w.insert(entry, FixedBitSet::with_capacity(0x100));
        let mut work = vec![entry];
        while let Some(k) = work.pop() {
            let (pc, H::Known(h)) = k else { continue };
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
        let keys: Vec<(u16, H)> = self.nodes.keys().copied().collect();
        let idx: FxHashMap<(u16, H), usize> =
            keys.iter().enumerate().map(|(i, &k)| (k, i)).collect();
        let succ = |i: usize| -> Vec<usize> {
            self.nodes[&keys[i]]
                .arms
                .iter()
                .flat_map(|a| &a.succ)
                .filter_map(|k| idx.get(k).copied())
                .collect()
        };
        let entry = idx[&(self.pc, H::Known(0))];
        let mut rank = vec![usize::MAX; keys.len()];
        let mut post = Vec::with_capacity(keys.len());
        let mut stack = vec![(entry, succ(entry))];
        rank[entry] = 0;
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
        env[entry] = Some(TOP_ENV);
        let mut work = BTreeSet::from([(0, entry)]);
        while let Some((_, i)) = work.pop_first() {
            done[i] = true;
            let (pc, h) = keys[i];
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
    /// Functions `CALL`ed, once per call site and height.
    pub fn callees(&self) -> impl Iterator<Item = u16> + '_ {
        self.nodes
            .values()
            .flat_map(|n| &n.arms)
            .filter_map(|a| match a.ctl {
                Ctl::Call { f, .. } => Some(f),
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

fn exit_of(top: bool, h: H, raises: bool) -> Exit {
    match (top, h) {
        (true, H::Known(x)) if x >= 2 => Exit::Static,
        (true, H::Unknown) if raises => Exit::Dynamic,
        _ => Exit::No,
    }
}

fn step(
    v: &Var,
    h: H,
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
            s.ctl = Ctl::Call { f: *f, ret: *ret };
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
/// its outermost entry and which were executed, and the starts not to plan.
pub struct Seen {
    heights: FxHashMap<u16, Vec<i32>>,
    starts: FxHashSet<u16>,
    /// Starts that are not planned: edges to them deopt.
    banned: FxHashSet<u16>,
}

impl Seen {
    pub fn new(f: &Facts, banned: FxHashSet<u16>) -> Self {
        Seen {
            heights: f
                .heights
                .iter()
                .map(|(p, h)| (*p, h.iter().map(|&x| x as i32).collect()))
                .collect(),
            starts: f.starts.iter().copied().collect(),
            banned,
        }
    }
}

struct Planner<'a> {
    top: bool,
    seen: &'a Seen,
    nodes: BTreeSet<(u16, H)>,
    /// Per instruction start: its node height in a callee, the first static height reaching
    /// it in an outermost plan.
    first: FxHashMap<u16, H>,
}

impl Planner<'_> {
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
    /// Record the edge to `t` at `h`; the nodes it creates. An outermost plan admits the
    /// heights `t` was executed at (or the first static one if it never was); a callee merges
    /// per start, a conflict making an executed start unknown and leaving any other to deopt.
    fn admit(&mut self, t: u16, h: H) -> Vec<(u16, H)> {
        let mut out = Vec::new();
        if self.top {
            for x in self.resolve(t, h) {
                let ok = match self.seen.heights.get(&t) {
                    Some(xs) => matches!(x, H::Known(v) if xs.contains(&v)),
                    None => *self.first.entry(t).or_insert(x) == x,
                };
                if ok && self.nodes.insert((t, x)) {
                    out.push((t, x));
                }
            }
            return out;
        }
        let m = match self.first.get(&t) {
            None => h,
            Some(&o) if H::merge(Some(o), h) == o => return out,
            Some(&o) if self.seen.starts.contains(&t) => {
                self.nodes.remove(&(t, o));
                H::Unknown
            }
            Some(_) => return out,
        };
        self.first.insert(t, m);
        self.nodes.insert((t, m));
        out.push((t, m));
        out
    }
}

/// Plan the function entered at `pc` given the `JSR` targets lifted as calls; the worklist
/// ends as nodes are only added (finitely many heights per start: the facts' or the first
/// static one outermost, one known then at most unknown in a callee) and return sites only
/// grow.
pub fn plan(g: &Graph, pc: u16, top: bool, calls: &FxHashSet<u16>, seen: &Seen) -> Plan {
    let mut pl = Planner {
        top,
        seen,
        nodes: BTreeSet::new(),
        first: FxHashMap::default(),
    };
    let mut rets: FxHashMap<i32, BTreeSet<u16>> = FxHashMap::default();
    let mut all_rets: BTreeSet<u16> = BTreeSet::new();
    let mut pulls: FxHashSet<(u16, H)> = FxHashSet::default();
    pl.first.insert(pc, H::Known(0));
    pl.nodes.insert((pc, H::Known(0)));
    let mut work = vec![(pc, H::Known(0))];
    while let Some((p, h)) = work.pop() {
        if !pl.nodes.contains(&(p, h)) {
            continue;
        }
        for v in &g.vars[&p] {
            if matches!(v.flow, Flow::Rts(_) | Flow::Rti(_)) {
                pulls.insert((p, h));
            }
            let s = step(v, h, top, calls, &rets, &all_rets);
            if let Some((hr, r)) = s.ret {
                let new = match hr {
                    H::Known(x) => rets.entry(x).or_default().insert(r),
                    H::Unknown => false,
                } | all_rets.insert(r);
                if new {
                    work.extend(pulls.iter().copied());
                }
            }
            for (t, ht) in s.succ {
                if g.vars.contains_key(&t) && !pl.seen.banned.contains(&t) {
                    work.extend(pl.admit(t, ht));
                }
            }
        }
    }
    let mut balanced = true;
    let nodes = pl
        .nodes
        .iter()
        .map(|&(p, h)| {
            let arms = g.vars[&p]
                .iter()
                .map(|v| {
                    let s = step(v, h, top, calls, &rets, &all_rets);
                    balanced &= !s.unbalanced;
                    let succ = s
                        .succ
                        .iter()
                        .flat_map(|&(t, ht)| pl.resolve(t, ht).into_iter().map(move |x| (t, x)))
                        .collect();
                    Arm {
                        ctl: s.ctl,
                        exit: s.exit,
                        succ,
                        acc: v.acc.clone(),
                    }
                })
                .collect();
            ((p, h), Node { arms })
        })
        .collect();
    Plan {
        pc,
        top,
        nodes,
        balanced,
        sp: None,
        sets_sp: pl.nodes.iter().any(|(p, _)| {
            g.vars[p]
                .iter()
                .any(|v| matches!(v.flow, Flow::Next(_)) && stack_delta(v.op).is_none())
        }),
    }
}

/// Plans for `entries` (outermost) and every callee they reach, entries first; the call
/// targets are the static `JSR` targets whose plans are balanced, by a decreasing fixpoint.
pub fn plan_all(g: &Graph, entries: &[u16], seen: &Seen) -> Vec<Plan> {
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
            .filter(|&c| !plan(g, c, false, &calls, seen).balanced)
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
        .map(|&e| plan(g, e, true, &calls, seen))
        .collect();
    let mut done: FxHashSet<u16> = FxHashSet::default();
    let mut i = 0;
    while i < plans.len() {
        let callees: Vec<u16> = plans[i].callees().collect();
        for f in callees {
            if done.insert(f) {
                plans.push(plan(g, f, false, &calls, seen));
            }
        }
        i += 1;
    }
    plans
}
