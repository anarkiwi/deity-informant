//! Instances: copies of one routine in the machine code that differ only in their operand bytes,
//! as a player unrolls its per-voice code. A lockstep walk over the copies' instructions pairs
//! them up (an instruction only some copies have is kept, run by those copies only); each
//! [`Class`] of copies is lifted as one function taking the copy's index, its operands computed
//! from the index (`base + stride * k` where they are affine in it), and each copy's entry
//! becomes a call of it.

use crate::analysis::{operand_bytes, stack_delta, Flow, Graph, Iv, Tg};
use di_pcode::Template;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::{BTreeSet, VecDeque};

/// The fewest copies a class has: two points fit any line `base + stride * k`, so only a third
/// copy shows the operands are a family indexed by the copy.
const MIN_COPIES: usize = 3;

/// A successor of a merged instruction: another one, or the region's exit `j`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Succ {
    Node(u32),
    Exit(u32),
}

/// A merged instruction: per copy its instruction start (`None`: one the copy lacks) and its
/// successors in flow order (a computed jump's paired targets in table order).
#[derive(Clone, Debug)]
pub struct INode {
    pub pcs: Vec<Option<u16>>,
    pub succ: Vec<Succ>,
}

/// Copies of one single-entry region: merged instructions (the entry first) and per exit each
/// copy's target.
#[derive(Clone, Debug)]
pub struct Class {
    pub nodes: Vec<INode>,
    pub exits: Vec<Vec<u16>>,
    /// Merged instructions whose immediate is a per-copy constant (not affine in the index).
    pub consts: usize,
}

impl Class {
    pub fn copies(&self) -> usize {
        self.nodes[0].pcs.len()
    }
    pub fn entry(&self, k: usize) -> u16 {
        self.nodes[0].pcs[k].unwrap()
    }
    /// Instructions the copies hold, less those of the merged function, one call per copy and
    /// each copy's value of every per-copy constant.
    fn saving(&self) -> isize {
        let held: usize = self
            .nodes
            .iter()
            .map(|n| n.pcs.iter().flatten().count())
            .sum();
        held as isize
            - self.nodes.len() as isize
            - self.copies() as isize
            - (self.consts * self.copies()) as isize
    }
    fn pcs(&self) -> impl Iterator<Item = u16> + '_ {
        self.nodes
            .iter()
            .flat_map(|n| n.pcs.iter().flatten().copied())
    }
}

/// What must agree between paired instructions besides their flow: opcode, which operand bytes
/// are cells, decimal mode, index guard, `JSR` target, and per store whether it may hit
/// constant code and the pointer windows it checks.
#[derive(PartialEq)]
struct Shape {
    op: u8,
    cells: [bool; 2],
    dec: bool,
    guard: Option<(u8, Iv)>,
    call: Option<u16>,
    stores: Vec<(bool, bool)>,
}

struct Cx<'a> {
    g: &'a Graph,
    calls: &'a FxHashSet<u16>,
    preds: FxHashMap<u16, Vec<u16>>,
    /// Starts entered other than by a flow edge: entries and called functions.
    ext: FxHashSet<u16>,
}

/// The successors of every flow, `JSR` return sites included.
fn targets(f: &Flow) -> Vec<u16> {
    match f {
        Flow::Next(n) => vec![*n],
        Flow::Br { t, f } => t.all().iter().copied().chain([*f]).collect(),
        Flow::Jmp(t) => t.all().to_vec(),
        Flow::Jsr { t, ret } => t.all().iter().copied().chain([*ret]).collect(),
        Flow::JmpInd(ts) | Flow::Rts(ts) | Flow::Rti(ts) | Flow::Brk(ts) => ts.clone(),
        Flow::Jam => Vec::new(),
    }
}

impl Cx<'_> {
    fn var(&self, pc: u16) -> Option<&crate::analysis::Var> {
        let vs = self.g.vars.get(&pc)?;
        (vs.len() == 1 && !self.g.cells.contains(pc as usize)).then(|| &vs[0])
    }
    fn shape(&self, pc: u16) -> Option<Shape> {
        let v = self.var(pc)?;
        let t = Template::of(v.op);
        let b = operand_bytes(&self.g.img, &self.g.cells, pc, t.len);
        let call = match &v.flow {
            Flow::Jsr { t: Tg::At(f), .. } => Some(*f),
            _ => None,
        };
        let stores = v
            .acc
            .iter()
            .enumerate()
            .filter(|(_, a)| a.store)
            .map(|(i, _)| {
                (
                    self.g.nocode.contains(&(pc, v.op, i)),
                    self.g.prot.chk.contains_key(&(pc, v.op, i)),
                )
            })
            .collect();
        Some(Shape {
            op: v.op,
            cells: [t.len > 1 && b[0].is_none(), t.len > 2 && b[1].is_none()],
            dec: self.g.dec.contains(pc as usize),
            guard: self.g.guards.get(&pc).copied(),
            call,
            stores,
        })
    }
    /// The successors of a start the walk may pair: straight-line code, branches and jumps to
    /// constant targets, jumps through cells whose every target was read from a table, calls
    /// of functions lifted separately, and `JAM` (a deopt).
    fn succs(&self, pc: u16) -> Option<Vec<u16>> {
        let v = self.var(pc)?;
        match &v.flow {
            Flow::Next(n) => stack_delta(v.op).map(|_| vec![*n]),
            Flow::Br { t: Tg::At(a), f } => Some(vec![*a, *f]),
            Flow::Jmp(Tg::At(a)) => Some(vec![*a]),
            Flow::Jmp(Tg::Calc(_)) => {
                let o = self.g.origins.get(&pc)?;
                let st = self.g.stat.get(&pc)?;
                let ts: BTreeSet<u16> = o.iter().map(|x| x.1).collect();
                (!o.is_empty() && st.iter().all(|t| ts.contains(t)) && ts.len() == st.len())
                    .then(|| o.iter().map(|x| x.1).collect())
            }
            Flow::Jsr { t: Tg::At(f), ret } if self.calls.contains(f) => Some(vec![*ret]),
            Flow::Jam => Some(Vec::new()),
            _ => None,
        }
    }
    /// Successor tuples of present starts `t` slot by slot, when their flows pair: the same
    /// count, and a computed jump's tables the same up to a shift per copy.
    fn slots(&self, t: &[u16]) -> Option<Vec<Vec<u16>>> {
        let ss: Vec<Vec<u16>> = t.iter().map(|&p| self.succs(p)).collect::<Option<_>>()?;
        let n = ss[0].len();
        if ss.iter().any(|s| s.len() != n) {
            return None;
        }
        if matches!(self.var(t[0])?.flow, Flow::Jmp(Tg::Calc(_))) {
            let o: Vec<&Vec<(u16, u16)>> = t.iter().map(|p| &self.g.origins[p]).collect();
            for x in &o[1..] {
                let d = x[0].0.wrapping_sub(o[0][0].0);
                if x.iter().zip(o[0]).any(|(a, b)| a.0.wrapping_sub(b.0) != d) {
                    return None;
                }
            }
        }
        Some((0..n).map(|j| ss.iter().map(|s| s[j]).collect()).collect())
    }
}

/// The walk's state: merged instructions, their tuples, exits, and the copy owning each start.
struct Walk {
    nodes: Vec<INode>,
    /// Successor tuple of an instruction only some copies have (the others continue there).
    cont: FxHashMap<u32, Vec<u16>>,
    index: FxHashMap<Vec<Option<u16>>, u32>,
    exits: Vec<Vec<u16>>,
    exit_ix: FxHashMap<Vec<u16>, u32>,
    owner: FxHashMap<u16, usize>,
    work: VecDeque<u32>,
}

impl Walk {
    fn add(&mut self, pcs: Vec<Option<u16>>) -> u32 {
        let i = self.nodes.len() as u32;
        for (k, p) in pcs.iter().enumerate() {
            if let Some(p) = p {
                self.owner.insert(*p, k);
            }
        }
        self.index.insert(pcs.clone(), i);
        self.nodes.push(INode {
            pcs,
            succ: Vec::new(),
        });
        self.work.push_back(i);
        i
    }
    fn exit(&mut self, t: Vec<u16>) -> Succ {
        let n = self.exits.len() as u32;
        let j = *self.exit_ix.entry(t.clone()).or_insert(n);
        if j == n {
            self.exits.push(t);
        }
        Succ::Exit(j)
    }
}

impl Cx<'_> {
    /// Whether present starts `t` can be one merged instruction.
    fn fits(&self, t: &[u16], w: &Walk, banned: &FxHashSet<u16>, entry: bool) -> bool {
        let mut seen = FxHashSet::default();
        if t.iter().any(|p| {
            w.owner.contains_key(p)
                || banned.contains(p)
                || (!entry && self.ext.contains(p))
                || !seen.insert(*p)
        }) {
            return false;
        }
        let Some(s0) = self.shape(t[0]) else {
            return false;
        };
        t[1..]
            .iter()
            .all(|&p| self.shape(p).is_some_and(|s| s == s0))
            && self.slots(t).is_some()
    }

    /// The successor tuple `t` (every copy present): a merged instruction, an instruction some
    /// copies lack followed by one, or an exit.
    fn resolve(&self, t: Vec<u16>, w: &mut Walk, banned: &FxHashSet<u16>) -> Succ {
        let full: Vec<Option<u16>> = t.iter().map(|&p| Some(p)).collect();
        if let Some(&i) = w.index.get(&full) {
            return Succ::Node(i);
        }
        if t.iter().all(|&p| p == t[0]) {
            return w.exit(t);
        }
        if self.fits(&t, w, banned, false) {
            return Succ::Node(w.add(full));
        }
        let ops: Vec<Option<u8>> = t.iter().map(|&p| self.var(p).map(|v| v.op)).collect();
        let kinds: BTreeSet<Option<u8>> = ops.iter().copied().collect();
        if kinds.len() == 2 && !kinds.contains(&None) {
            for &extra in &kinds {
                let has: Vec<usize> = (0..t.len()).filter(|&k| ops[k] == extra).collect();
                let other = ops[(0..t.len()).find(|k| !has.contains(k)).unwrap()];
                let ins: Vec<u16> = has.iter().map(|&k| t[k]).collect();
                let straight = ins.iter().all(|&p| {
                    self.var(p).is_some_and(|v| {
                        matches!(v.flow, Flow::Next(_)) && stack_delta(v.op) == Some(0)
                    })
                });
                if !straight || !self.fits(&ins, w, banned, false) {
                    continue;
                }
                let mut next = t.clone();
                for &k in &has {
                    let Flow::Next(n) = self.var(t[k]).unwrap().flow else {
                        unreachable!()
                    };
                    next[k] = n;
                }
                let resumes = has
                    .iter()
                    .all(|&k| self.var(next[k]).map(|v| v.op) == other);
                if !resumes
                    || !(w.index.contains_key(&next_full(&next))
                        || self.fits(&next, w, banned, false))
                {
                    continue;
                }
                let pcs = (0..t.len())
                    .map(|k| has.contains(&k).then_some(t[k]))
                    .collect();
                let i = w.add(pcs);
                w.cont.insert(i, next);
                return Succ::Node(i);
            }
        }
        w.exit(t)
    }

    /// The lockstep walk from the copies' entries `ms`, starts in `banned` left out.
    fn walk(&self, ms: &[u16], banned: &FxHashSet<u16>) -> Option<Class> {
        let mut w = Walk {
            nodes: Vec::new(),
            cont: FxHashMap::default(),
            index: FxHashMap::default(),
            exits: Vec::new(),
            exit_ix: FxHashMap::default(),
            owner: FxHashMap::default(),
            work: VecDeque::new(),
        };
        if !self.fits(ms, &w, banned, true) {
            return None;
        }
        w.add(ms.iter().map(|&p| Some(p)).collect());
        while let Some(i) = w.work.pop_front() {
            let pcs = w.nodes[i as usize].pcs.clone();
            let succ = match w.cont.get(&i) {
                Some(next) => vec![self.resolve(next.clone(), &mut w, banned)],
                None => {
                    let t: Vec<u16> = pcs.iter().map(|p| p.unwrap()).collect();
                    let slots = self.slots(&t).unwrap();
                    slots
                        .into_iter()
                        .map(|s| self.resolve(s, &mut w, banned))
                        .collect()
                }
            };
            w.nodes[i as usize].succ = succ;
        }
        Some(Class {
            nodes: w.nodes,
            exits: w.exits,
            consts: 0,
        })
    }

    /// Starts that keep the copies of `c` from being single-entry regions entered at their
    /// entries and left at their exits (one entered from outside its copy or from elsewhere
    /// than a flow edge, an exit into its own copy; empty when `c` is sound), and the number of
    /// merged instructions whose immediate is a per-copy constant.
    fn faults(&self, c: &Class) -> (Vec<u16>, usize) {
        let n = c.copies();
        let mut consts = 0;
        let mut own: Vec<FxHashSet<u16>> = vec![FxHashSet::default(); n];
        for node in &c.nodes {
            for (k, p) in node.pcs.iter().enumerate() {
                if let Some(p) = p {
                    own[k].insert(*p);
                }
            }
        }
        let mut bad = Vec::new();
        for k in 0..n {
            for &p in &own[k] {
                if p == c.entry(k) {
                    continue;
                }
                let outside = self
                    .preds
                    .get(&p)
                    .is_some_and(|ps| ps.iter().any(|q| !own[k].contains(q)));
                if outside || self.ext.contains(&p) {
                    bad.push(p);
                }
            }
            bad.extend(c.exits.iter().map(|x| x[k]).filter(|x| own[k].contains(x)));
        }
        // Operands the copies differ in are affine in the copy index, addresses inside each
        // copy's own code (its cells and code), or immediates (per-copy constants): what copies
        // of one routine at other places and over other per-copy state differ in.
        let code: Vec<FxHashSet<u16>> = own
            .iter()
            .map(|o| {
                o.iter()
                    .flat_map(|&p| {
                        let n = Template::of(self.g.img[p as usize]).len as u16;
                        (0..n).map(move |i| p.wrapping_add(i))
                    })
                    .collect()
            })
            .collect();
        for node in &c.nodes {
            let Some(p0) = node.pcs.iter().flatten().next().copied() else {
                continue;
            };
            let v = self.var(p0).unwrap();
            let len = Template::of(v.op).len;
            if len == 1 || !matches!(v.flow, Flow::Next(_)) {
                continue;
            }
            let vals: Vec<(usize, Option<u32>)> = node
                .pcs
                .iter()
                .enumerate()
                .filter_map(|(k, p)| {
                    let b = operand_bytes(&self.g.img, &self.g.cells, (*p)?, len);
                    Some((
                        k,
                        match (b[0], b[1], len) {
                            (Some(lo), _, 2) => Some(lo as u32),
                            (Some(lo), Some(hi), 3) => Some(u16::from_le_bytes([lo, hi]) as u32),
                            _ => None,
                        },
                    ))
                })
                .collect();
            if vals.iter().any(|v| v.1.is_none()) {
                continue;
            }
            let m = if len == 2 { 0xff } else { 0xffff };
            let pts: Vec<(u32, u32)> = vals.iter().map(|&(k, v)| (k as u32, v.unwrap())).collect();
            let inside = pts
                .iter()
                .all(|&(k, v)| code[k as usize].contains(&(v as u16)));
            let value = len == 2 && v.acc.is_empty();
            if line(&pts, m).is_none() && !inside {
                if value {
                    consts += 1;
                } else {
                    bad.extend(node.pcs.iter().flatten().copied());
                }
            }
        }
        bad.sort_unstable();
        bad.dedup();
        (bad, consts)
    }

    /// The stack height of each merged instruction relative to the entry, when every copy's
    /// pushes and pulls agree, nothing pulls above the entry and every exit is at its height.
    fn heights(&self, c: &Class) -> Option<Vec<i32>> {
        let mut h: Vec<Option<i32>> = vec![None; c.nodes.len()];
        h[0] = Some(0);
        let mut work = vec![0usize];
        while let Some(i) = work.pop() {
            let node = &c.nodes[i];
            let p = node.pcs.iter().flatten().next().copied()?;
            let v = self.var(p)?;
            let d = match v.flow {
                Flow::Next(_) => stack_delta(v.op)?,
                _ => 0,
            };
            if d != 0 && node.pcs.iter().any(|p| p.is_none()) {
                return None;
            }
            let hs = h[i]? + d;
            if hs > 0 {
                return None;
            }
            for s in &node.succ {
                match *s {
                    Succ::Exit(_) if hs != 0 => return None,
                    Succ::Exit(_) => {}
                    Succ::Node(j) => match h[j as usize] {
                        None => {
                            h[j as usize] = Some(hs);
                            work.push(j as usize);
                        }
                        Some(x) if x != hs => return None,
                        Some(_) => {}
                    },
                }
            }
        }
        h.into_iter().collect()
    }

    /// Whether the copies of `c` hold state of their own: an address they differ in, a cell
    /// (read at each copy's own address) or a jump through tables shifted per copy. Copies
    /// alike in every address are one routine repeated (per-copy immediates are constants).
    fn indexed(&self, c: &Class) -> bool {
        c.nodes.iter().any(|n| {
            let ps: Vec<u16> = n.pcs.iter().flatten().copied().collect();
            let v = self.var(ps[0]).unwrap();
            let len = Template::of(v.op).len;
            let b: Vec<[Option<u8>; 2]> = ps
                .iter()
                .map(|&p| operand_bytes(&self.g.img, &self.g.cells, p, len))
                .collect();
            let data = matches!(v.flow, Flow::Next(_)) || matches!(v.flow, Flow::Jmp(Tg::Calc(_)));
            let value = len == 2 && v.acc.is_empty();
            data && ps.len() > 1
                && (b
                    .iter()
                    .any(|x| x[..len as usize - 1].iter().any(|y| y.is_none()))
                    || !value
                        && b.iter()
                            .any(|x| x[..len as usize - 1] != b[0][..len as usize - 1])
                    || matches!(v.flow, Flow::Jmp(Tg::Calc(_))))
        })
    }

    /// Whether the copies of `c` run one after another, as an unrolled loop's bodies: each
    /// copy's entry is reached from the previous copy's exits (by address, ascending or
    /// descending) through code outside every copy, without returning.
    fn unrolled(&self, c: &Class) -> bool {
        let inside: FxHashSet<u16> = c.pcs().collect();
        let n = c.copies();
        let follows = |a: usize, b: usize| {
            let goal = c.entry(b);
            let mut seen: FxHashSet<u16> = FxHashSet::default();
            let mut work: Vec<u16> = c.exits.iter().map(|x| x[a]).collect();
            while let Some(p) = work.pop() {
                if p == goal {
                    return true;
                }
                if inside.contains(&p) || !seen.insert(p) {
                    continue;
                }
                let Some(vs) = self.g.vars.get(&p) else {
                    continue;
                };
                for v in vs {
                    work.extend(match &v.flow {
                        Flow::Jsr { ret, .. } => vec![*ret],
                        Flow::Rts(_) | Flow::Rti(_) | Flow::Brk(_) => Vec::new(),
                        f => targets(f),
                    });
                }
            }
            false
        };
        (1..n).all(|k| follows(k - 1, k)) || (1..n).all(|k| follows(k, k - 1))
    }

    /// The sound class of copies entered at `ms`: walked, then walked again without the starts
    /// that break its regions until none does.
    fn class(&self, ms: &[u16]) -> Option<Class> {
        let mut banned = FxHashSet::default();
        loop {
            let mut c = self.walk(ms, &banned)?;
            let (bad, consts) = self.faults(&c);
            c.consts = consts;
            if bad.is_empty() {
                return (self.indexed(&c) && self.unrolled(&c) && self.heights(&c).is_some())
                    .then_some(c);
            }
            banned.extend(bad);
        }
    }
}

/// `(a, s)` with the points `(k, v)` on `v = a + s * k` modulo `m + 1`, if any.
pub fn line(pts: &[(u32, u32)], m: u32) -> Option<(u32, u32)> {
    let (k0, v0) = pts[0];
    let s = match pts.iter().find(|p| p.1 != v0) {
        None => 0,
        Some(&(k1, v1)) => {
            let (dv, dk) = (v1.wrapping_sub(v0) & m, k1 - k0);
            (dv % dk == 0).then_some(dv / dk)?
        }
    };
    let a = v0.wrapping_sub(s.wrapping_mul(k0)) & m;
    pts.iter()
        .all(|&(k, v)| a.wrapping_add(s.wrapping_mul(k)) & m == v)
        .then_some((a, s))
}

fn next_full(t: &[u16]) -> Vec<Option<u16>> {
    t.iter().map(|&p| Some(p)).collect()
}

/// Classes of copies in the discovered program `g` entered at `roots`, whose `JSR` targets
/// lifted separately are `calls`: per leader (a start entered other than by falling through) the
/// set of other starts of its opcode whose copies save the most instructions, greedily by
/// saving; no start is in two classes. Copies are ordered by address, so their indices follow
/// the layout.
pub fn find(g: &Graph, roots: &[u16], calls: &FxHashSet<u16>) -> Vec<Class> {
    let mut preds: FxHashMap<u16, Vec<u16>> = FxHashMap::default();
    let mut lead: BTreeSet<u16> = roots.iter().copied().collect();
    let mut pcs: Vec<u16> = g.vars.keys().copied().collect();
    pcs.sort_unstable();
    for &pc in &pcs {
        for v in &g.vars[&pc] {
            for t in targets(&v.flow) {
                preds.entry(t).or_default().push(pc);
                if !matches!(v.flow, Flow::Next(_)) {
                    lead.insert(t);
                }
            }
        }
    }
    let ext: FxHashSet<u16> = roots.iter().chain(calls.iter()).copied().collect();
    lead.extend(ext.iter().copied());
    let cx = Cx {
        g,
        calls,
        preds,
        ext,
    };
    let mut by_op: FxHashMap<u8, Vec<u16>> = FxHashMap::default();
    for &p in &pcs {
        if let Some(v) = cx.var(p) {
            by_op.entry(v.op).or_default().push(p);
        }
    }
    let none = FxHashSet::default();
    let mut found: Vec<Class> = Vec::new();
    for &a in &lead {
        let Some(v) = cx.var(a) else { continue };
        let mut partners: Vec<(usize, u16)> = by_op[&v.op]
            .iter()
            .filter(|&&b| b != a)
            .filter_map(|&b| {
                let mut ms = [a, b];
                ms.sort_unstable();
                let n = cx.walk(&ms, &none)?.nodes.len();
                (n > 1).then_some((n, b))
            })
            .collect();
        partners.sort_unstable_by(|x, y| y.0.cmp(&x.0).then(x.1.cmp(&y.1)));
        let (mut best, mut set): (Option<Class>, Vec<u16>) = (None, vec![a]);
        for (n, b) in partners {
            let bound = (n * set.len()) as isize - set.len() as isize - 1;
            if best.as_ref().is_some_and(|c| bound <= c.saving()) {
                break;
            }
            let mut ms = set.clone();
            ms.push(b);
            ms.sort_unstable();
            // Two copies fit any line: only a third witnesses an indexed family.
            if let Some(c) = cx.class(&ms) {
                let gain = c.saving();
                if gain > 0 && (set.len() < 2 || gain > best.as_ref().map_or(0, |c| c.saving())) {
                    if ms.len() >= MIN_COPIES {
                        best = Some(c);
                    }
                    set = ms;
                }
            }
        }
        found.extend(best);
    }
    found.sort_by(|x, y| {
        y.saving()
            .cmp(&x.saving())
            .then(x.entry(0).cmp(&y.entry(0)))
    });
    let mut taken: FxHashSet<u16> = FxHashSet::default();
    let mut out = Vec::new();
    for c in found {
        if c.pcs().any(|p| taken.contains(&p)) {
            continue;
        }
        taken.extend(c.pcs());
        out.push(c);
    }
    out
}

/// Per merged instruction of `c` its stack height (a class [`find`] returns has them).
pub fn heights(g: &Graph, c: &Class) -> Vec<i32> {
    let cx = Cx {
        g,
        calls: &FxHashSet::default(),
        preds: FxHashMap::default(),
        ext: FxHashSet::default(),
    };
    cx.heights(c).expect("a found class has heights")
}
