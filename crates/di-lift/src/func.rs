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
use crate::inst::{Class, Succ};
use di_pcode::{reg as R, Template};
use fixedbitset::FixedBitSet;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;

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
    /// `JSR` to a function lifted separately (entered in configuration `c`), continuing at
    /// `to`: its return site, or past it when the callee returns past inline arguments.
    Call {
        f: u16,
        ret: u16,
        to: u16,
        c: u8,
    },
    /// `JSR` through cell operands to functions lifted separately: a `CALL` of the target.
    CallInd {
        fs: Vec<u16>,
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
    /// A callee that pulls and pushes its return address (inline arguments): its calls keep
    /// the address in memory and check what it returns through.
    pub above: bool,
    /// Tail calls: an edge at the entry height to a function lifted separately (a fall-through,
    /// jump or branch into its entry, whose `RTS` then returns from this one) is a `CALL` of it
    /// followed by this function's return.
    pub tails: BTreeSet<Key>,
    /// Calls of instance functions ([`crate::inst`]) in place of their copies' entries.
    pub insts: BTreeMap<Key, Icall>,
    /// The class of copies this plan merges.
    pub inst: Option<Box<Inst>>,
}

/// A call of instance function `class` for copy `k`, continuing at `exits` (one per exit).
#[derive(Clone, Debug)]
pub struct Icall {
    pub class: u32,
    pub k: u8,
    pub exits: Vec<Key>,
}

/// An instance function: its class, the merged instruction at each node's start, the exits by
/// key and, per computed jump, each copy's target and the node it enters.
#[derive(Clone, Debug)]
pub struct Inst {
    pub class: u32,
    pub c: Arc<Class>,
    pub node: FxHashMap<u16, u32>,
    pub exits: BTreeMap<Key, u32>,
    pub calc: FxHashMap<u16, Vec<(u16, Key)>>,
}

/// The classes of copies being lifted as instance functions, and the class and copy index of
/// each copy's entry.
#[derive(Default)]
pub struct Insts {
    pub classes: Vec<Arc<Class>>,
    pub entry: FxHashMap<u16, (u32, u8)>,
}

impl Insts {
    pub fn new(classes: Vec<Class>) -> Self {
        let mut entry = FxHashMap::default();
        for (i, c) in classes.iter().enumerate() {
            for k in 0..c.copies() {
                entry.insert(c.entry(k), (i as u32, k as u8));
            }
        }
        Insts {
            classes: classes.into_iter().map(Arc::new).collect(),
            entry,
        }
    }
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
                let prior = std::mem::take(&mut arm.acc);
                absint(
                    t.ops(),
                    operand_bytes(img, &g.cells, pc, t.len),
                    &mut x,
                    &mut arm.acc,
                );
                for (a, p) in arm.acc.iter_mut().zip(&prior) {
                    let (lo, hi) = (a.addr.lo.max(p.addr.lo), a.addr.hi.min(p.addr.hi));
                    if lo <= hi {
                        a.addr = Iv { lo, hi };
                    }
                    (a.defd, a.above, a.ptr) = (p.defd, p.above, p.ptr);
                }
                if matches!(arm.ctl, Ctl::Call { .. } | Ctl::CallInd { .. }) {
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
    /// The entry nodes, one per entry configuration (none when the entry is a copy's call).
    pub fn roots(&self) -> impl Iterator<Item = Key> + '_ {
        self.entry
            .iter()
            .map(|&c| (self.pc, H::Known(0), c))
            .filter(|k| self.nodes.contains_key(k))
    }
    /// Functions `CALL`ed with their entry configuration, once per call site and node, then
    /// the tail calls.
    pub fn callees(&self) -> impl Iterator<Item = (u16, u8)> + '_ {
        self.nodes
            .values()
            .flat_map(|n| &n.arms)
            .flat_map(|a| match &a.ctl {
                Ctl::Call { f, c, .. } => vec![(*f, *c)],
                Ctl::CallInd { fs, c, .. } => fs.iter().map(|&f| (f, *c)).collect(),
                _ => Vec::new(),
            })
            .chain(self.tails.iter().map(|k| (k.0, k.2)))
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

/// The height `TXS` at `pc` sets SP to when it restores a stack pointer saved by `TSX; STX a`:
/// the instruction before it is the only one reaching it and loads `X` from `a`, every store
/// to `a` is an `STX a` right after a `TSX`, and those `TSX`s run at one height in the plan
/// (`nodes`). A non-local exit to that frame; a guard checks it at run time.
fn saved_sp(g: &Graph, pc: u16, nodes: &BTreeSet<Key>) -> Option<H> {
    let only = |q: u16, op: u8| {
        let vs = g.vars.get(&q)?;
        matches!(&vs[..], [v] if v.op == op).then(|| vs[0].clone())
    };
    let ldx = pc.wrapping_sub(3);
    let v = only(ldx, 0xae)?;
    let into: Vec<u16> = g
        .vars
        .iter()
        .filter(|(_, vs)| {
            vs.iter()
                .any(|v| crate::analysis::flow_succs(&v.flow).contains(&pc))
        })
        .map(|(&q, _)| q)
        .collect();
    if into != [ldx] || !matches!(v.flow, Flow::Next(n) if n == pc) {
        return None;
    }
    let a = u16::from_le_bytes([g.img[ldx as usize + 1], g.img[ldx as usize + 2]]) as u32;
    let mut hs = BTreeSet::new();
    for (&q, vs) in &g.vars {
        let hits = vs
            .iter()
            .any(|v| v.acc.iter().any(|x| x.store && x.addr.meets(a, a + 1)));
        if !hits {
            continue;
        }
        let tsx = q.wrapping_sub(1);
        let stx = only(q, 0x8e)?;
        let t = only(tsx, 0xba)?;
        if stx.acc.iter().any(|x| x.store && x.addr.value() != Some(a))
            || !matches!(t.flow, Flow::Next(n) if n == q)
        {
            return None;
        }
        hs.extend(nodes.iter().filter(|k| k.0 == tsx).map(|k| k.1));
    }
    match hs.into_iter().collect::<Vec<_>>()[..] {
        [h @ H::Known(_)] => Some(h),
        _ => None,
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
                to: *ret,
                c,
            };
        }
        Flow::Jsr {
            t: Tg::Calc(fs),
            ret,
        } if !fs.is_empty() && fs.iter().all(|f| calls.contains(f)) => {
            s.succ = vec![(*ret, h)];
            s.ctl = Ctl::CallInd {
                fs: fs.clone(),
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
/// its outermost entry and which were executed, the configurations each ran in (with a bank
/// map), the starts not to plan, and the bank map.
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
            banks: match bank {
                Some(_) => f.banks.iter().cloned().collect(),
                None => FxHashMap::default(),
            },
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
    ins: &Insts,
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
    let mut tails: BTreeSet<Key> = BTreeSet::new();
    let mut icalls: BTreeMap<Key, (u32, u8)> = BTreeMap::new();
    let tail =
        |t: u16, h: H| seen.bank.is_none() && h == H::Known(0) && t != pc && calls.contains(&t);
    // Edges to `t` at `h`: a tail call, a copy's call (continuing at its exits), or nodes.
    let enter = |pl: &mut Planner,
                 edges: &mut Vec<(u16, H, Option<u8>)>,
                 tails: &mut BTreeSet<Key>,
                 icalls: &mut BTreeMap<Key, (u32, u8)>|
     -> Vec<Key> {
        let mut out = Vec::new();
        while let Some((t, ht, tc)) = edges.pop() {
            if !g.vars.contains_key(&t) || pl.seen.banned.contains(&t) {
                continue;
            }
            if tail(t, ht) {
                tails.extend(pl.configs(t, tc).into_iter().map(|c| (t, ht, c)));
            } else if let Some(&(ci, k)) = ins.entry.get(&t) {
                for c in pl.configs(t, tc) {
                    if icalls.insert((t, ht, c), (ci, k)).is_none() {
                        let x = &ins.classes[ci as usize].exits;
                        edges.extend(x.iter().map(|x| (x[k as usize], ht, Some(c))));
                    }
                }
            } else {
                out.extend(pl.admit(t, ht, tc));
            }
        }
        out
    };
    let h_of = |succ: &[(u16, H)]| succ.first().map_or(H::Unknown, |x| x.1);
    let args = !top
        && g.vars.values().flatten().any(|v| {
            matches!(&v.flow, Flow::Jsr { t: Tg::At(t), ret } if *t == pc && g.past.contains_key(ret))
        });
    // A `TXS` restoring a stack pointer saved at a known height continues there; an
    // `RTS`/`RTI` whose targets the code gives goes to them alone, not to the return sites of
    // the inline `JSR`s at its height.
    let restore = |mut s: Step, v: &Var, p: u16, nodes: &BTreeSet<Key>| {
        if let (0x9a, [(n, H::Unknown)]) = (v.op, &s.succ[..]) {
            if let Some(h) = saved_sp(g, p, nodes) {
                s.succ = vec![(*n, h)];
                s.exit = exit_of(top, h, false);
                s.unbalanced = !top && matches!(h, H::Known(1..));
            }
        }
        // A callee returning past inline arguments pulls and pushes back its return address.
        if args && matches!(v.flow, Flow::Next(_)) && matches!(s.succ[..], [(_, H::Known(1..=2))]) {
            s.unbalanced = false;
        }
        // A call returning past inline arguments continues there.
        if let Ctl::Call { ret, to, .. } = &mut s.ctl {
            if let Some([t]) = g.past.get(ret).map(Vec::as_slice) {
                *to = *t;
                s.succ = vec![(*t, h_of(&s.succ))];
            }
        }
        if let (Flow::Rts(ts) | Flow::Rti(ts), true) = (&v.flow, g.stat.contains_key(&p)) {
            s.succ.retain(|x| ts.contains(&x.0));
            if let Ctl::Rts(t) | Ctl::Rti(t) = &mut s.ctl {
                t.retain(|x| ts.contains(x));
            }
        }
        s
    };
    pl.first.insert(pc, H::Known(0));
    let mut work: Vec<Key> = Vec::new();
    if ins.entry.contains_key(&pc) {
        let mut edges: Vec<_> = entry.iter().map(|&c| (pc, H::Known(0), Some(c))).collect();
        work = enter(&mut pl, &mut edges, &mut tails, &mut icalls);
    } else {
        work.extend(entry.iter().map(|&c| (pc, H::Known(0), c)));
        pl.nodes.extend(work.iter().copied());
    }
    let bank = seen.bank;
    while let Some((p, h, c)) = work.pop() {
        if !pl.nodes.contains(&(p, h, c)) {
            continue;
        }
        for v in &g.vars[&p] {
            if matches!(v.flow, Flow::Rts(_) | Flow::Rti(_)) {
                pulls.insert((p, h, c));
            }
            let s = restore(step(v, h, c, top, calls, &rets, &all_rets), v, p, &pl.nodes);
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
            let mut edges: Vec<_> = s.succ.iter().map(|&(t, ht)| (t, ht, tc)).collect();
            work.extend(enter(&mut pl, &mut edges, &mut tails, &mut icalls));
        }
    }
    let insts = icalls
        .into_iter()
        .map(|(key, (class, k))| {
            let exits = ins.classes[class as usize]
                .exits
                .iter()
                .map(|x| {
                    let t = x[k as usize];
                    (
                        t,
                        pl.resolve(t, key.1).first().copied().unwrap_or(key.1),
                        key.2,
                    )
                })
                .collect();
            (key, Icall { class, k, exits })
        })
        .collect();
    let mut balanced = true;
    let nodes = pl
        .nodes
        .iter()
        .map(|&(p, h, c)| {
            let arms = g.vars[&p]
                .iter()
                .map(|v| {
                    let s = restore(step(v, h, c, top, calls, &rets, &all_rets), v, p, &pl.nodes);
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
        .collect::<BTreeMap<Key, Node>>();
    let above = !top && nodes.keys().any(|k| matches!(k.1, H::Known(1..)));
    Plan {
        pc,
        top,
        nodes,
        entry,
        balanced,
        sp: None,
        tails,
        insts,
        inst: None,
        above,
        sets_sp: pl.nodes.iter().any(|(p, _, _)| {
            g.vars[p]
                .iter()
                .any(|v| matches!(v.flow, Flow::Next(_)) && stack_delta(v.op).is_none())
        }),
    }
}

/// Heads of shared tails: instruction starts that two or more plans reach at their entry
/// height, entered there from a node that is not shared (one no other plan reaches at the same
/// height). Code the plans share past such an entry, including where it returns to the entry
/// height after a push and a pull, stays in the function the entry heads. Each becomes a
/// function its callers reach by tail calls, as a `JSR` target that returns at its entry
/// height does by calls.
fn shared_tails(plans: &[Plan]) -> BTreeSet<u16> {
    let mut count: FxHashMap<(u16, H), usize> = FxHashMap::default();
    let plans: Vec<&Plan> = plans.iter().filter(|p| p.inst.is_none()).collect();
    for p in &plans {
        let at: BTreeSet<(u16, H)> = p
            .nodes
            .keys()
            .filter(|k| k.0 != p.pc)
            .map(|k| (k.0, k.1))
            .collect();
        for k in at {
            *count.entry(k).or_default() += 1;
        }
    }
    let shared = |k: &Key| count.get(&(k.0, k.1)).is_some_and(|&n| n > 1);
    plans
        .iter()
        .flat_map(|p| {
            p.nodes.iter().flat_map(move |(k, n)| {
                n.arms
                    .iter()
                    .flat_map(|a| &a.succ)
                    .filter(move |s| s.1 == H::Known(0) && shared(s) && (!shared(k) || k.0 == p.pc))
                    .map(|s| s.0)
            })
        })
        .collect()
}

/// The plan of instance function `ci` merging the copies of `c`: a callee whose nodes are the
/// merged instructions (keyed by the first copy holding each) at their heights, its exits
/// returns, its accesses the hull of the copies'.
fn plan_inst(g: &Graph, ci: u32, c: &Arc<Class>) -> Plan {
    let hs = crate::inst::heights(g, c);
    let cf = g.cfg[&c.entry(0)];
    let rep = |i: u32| {
        c.nodes[i as usize]
            .pcs
            .iter()
            .flatten()
            .copied()
            .next()
            .unwrap()
    };
    let key = |s: Succ| match s {
        Succ::Node(i) => (rep(i), H::Known(hs[i as usize]), cf),
        Succ::Exit(j) => (c.exits[j as usize][0], H::Known(0), cf),
    };
    let mut calc = FxHashMap::default();
    let mut nodes = BTreeMap::new();
    for (i, n) in c.nodes.iter().enumerate() {
        let p = rep(i as u32);
        let v = &g.vars[&p][0];
        let succ: Vec<Key> = n.succ.iter().map(|&s| key(s)).collect();
        let ctl = match &v.flow {
            Flow::Next(_) => Ctl::Next(succ[0].0),
            Flow::Br { .. } => Ctl::Br {
                t: Tg::At(succ[0].0),
                f: succ[1].0,
            },
            Flow::Jmp(Tg::At(_)) => Ctl::Jmp(Tg::At(succ[0].0)),
            Flow::Jmp(Tg::Calc(_)) => {
                let mut ts: Vec<(u16, Key)> = n
                    .pcs
                    .iter()
                    .flatten()
                    .flat_map(|q| g.origins[q].iter().map(|o| o.1).zip(succ.iter().copied()))
                    .collect();
                ts.sort_unstable();
                ts.dedup();
                let all: Vec<u16> = ts.iter().map(|t| t.0).collect();
                calc.insert(p, ts);
                Ctl::Jmp(Tg::Calc(all))
            }
            Flow::Jsr { t: Tg::At(f), .. } => Ctl::Call {
                f: *f,
                ret: succ[0].0,
                to: succ[0].0,
                c: cf,
            },
            Flow::Jam => Ctl::Jam,
            f => unreachable!("not merged: {f:?}"),
        };
        let mut acc = v.acc.clone();
        for q in n.pcs.iter().flatten() {
            for (a, b) in acc.iter_mut().zip(&g.vars[q][0].acc) {
                a.addr = Iv {
                    lo: a.addr.lo.min(b.addr.lo),
                    hi: a.addr.hi.max(b.addr.hi),
                };
                a.val = Iv {
                    lo: a.val.lo.min(b.val.lo),
                    hi: a.val.hi.max(b.val.hi),
                };
                a.back &= b.back;
                a.defd &= b.defd;
                a.above &= b.above;
                if a.ptr != b.ptr {
                    a.ptr = None;
                }
            }
        }
        let arm = Arm {
            ctl,
            exit: Exit::No,
            succ: succ.clone(),
            acc,
            port: false,
        };
        nodes.insert(key(Succ::Node(i as u32)), Node { arms: vec![arm] });
    }
    Plan {
        pc: c.entry(0),
        top: false,
        nodes,
        entry: vec![cf],
        balanced: true,
        sp: None,
        sets_sp: false,
        above: false,
        tails: BTreeSet::new(),
        insts: BTreeMap::new(),
        inst: Some(Box::new(Inst {
            class: ci,
            c: c.clone(),
            node: (0..c.nodes.len() as u32).map(|i| (rep(i), i)).collect(),
            exits: (0..c.exits.len() as u32)
                .map(|j| (key(Succ::Exit(j)), j))
                .collect(),
            calc,
        })),
    }
}

/// Whether an arm may change the configuration: it stores to the port, or `CALL`s with a
/// bank map (cleared by [`plan_all`] for callees that cannot).
fn port_arm(v: &Var, ctl: &Ctl, bank: Option<&BankMap>) -> bool {
    writes_port(v, bank) || bank.is_some() && matches!(ctl, Ctl::Call { .. } | Ctl::CallInd { .. })
}

/// Plans for `entries` (outermost, entered in the configurations given per entry) and every
/// callee they reach per entry configuration, entries first; the call targets are the static
/// `JSR` targets whose plans are balanced, by a decreasing fixpoint. A `CALL` keeps its
/// configuration unless its callee may write the port (a least fixpoint over the plans).
pub fn plan_all(g: &Graph, entries: &[(u16, Vec<u8>)], seen: &Seen, fold: bool) -> Vec<Plan> {
    let mut calls: FxHashSet<u16> = g
        .vars
        .values()
        .flatten()
        .flat_map(|v| match &v.flow {
            Flow::Jsr { t, .. } => t.all().to_vec(),
            _ => Vec::new(),
        })
        .filter(|f| g.vars.contains_key(f))
        .collect();
    let mut sites: FxHashMap<u16, usize> = FxHashMap::default();
    for v in g.vars.values().flatten() {
        if let Flow::Jsr { t: Tg::At(f), .. } = &v.flow {
            *sites.entry(*f).or_default() += 1;
        }
    }
    let mut tried: FxHashSet<u16> = FxHashSet::default();
    let none = Insts::default();
    let mut ins: Option<Insts> = None;
    let (mut plans, idx) = loop {
        loop {
            // A callee that pulls its return address is a function only when it is a
            // procedure: called from two or more sites; else it is inlined.
            let bad: Vec<u16> = calls
                .iter()
                .copied()
                .filter(|&f| {
                    let p = plan(g, f, vec![g.cfg[&f]], false, &calls, seen, &none);
                    !p.balanced || p.above && sites.get(&f).copied().unwrap_or(0) < 2
                })
                .collect();
            if bad.is_empty() {
                break;
            }
            for b in bad {
                calls.remove(&b);
            }
        }
        let ins: &Insts = ins.get_or_insert_with(|| match fold && seen.bank.is_none() {
            true => {
                let roots: Vec<u16> = entries.iter().map(|e| e.0).collect();
                Insts::new(crate::inst::find(g, &roots, &calls))
            }
            false => Insts::default(),
        });
        let mut plans: Vec<Plan> = entries
            .iter()
            .map(|(e, cs)| plan(g, *e, cs.clone(), true, &calls, seen, ins))
            .collect();
        for (ci, c) in ins.classes.iter().enumerate() {
            plans.push(plan_inst(g, ci as u32, c));
        }
        let mut idx: FxHashMap<(u16, u8), usize> = FxHashMap::default();
        let mut i = 0;
        while i < plans.len() {
            let callees: Vec<(u16, u8)> = plans[i].callees().collect();
            for (f, c) in callees {
                if let std::collections::hash_map::Entry::Vacant(e) = idx.entry((f, c)) {
                    e.insert(plans.len());
                    plans.push(plan(g, f, vec![c], false, &calls, seen, ins));
                }
            }
            i += 1;
        }
        let heads: Vec<u16> = shared_tails(&plans)
            .into_iter()
            .filter(|t| !calls.contains(t) && tried.insert(*t))
            .collect();
        if heads.is_empty() || seen.bank.is_some() {
            break (plans, idx);
        }
        calls.extend(heads);
    };
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
                    let fs: Vec<(u16, u8)> = match &a.ctl {
                        Ctl::Call { f, c, .. } => vec![(*f, *c)],
                        Ctl::CallInd { fs, c, .. } => fs.iter().map(|&f| (f, *c)).collect(),
                        _ => continue,
                    };
                    if let Some(&(_, c)) = fs.first() {
                        if fs.iter().all(|k| !porty[idx[k]]) && !writes_port(v, seen.bank) {
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
