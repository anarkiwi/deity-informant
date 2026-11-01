//! Functions and stack heights: which `JSR`s become `CALL`s (callees that return at the
//! height they were entered, never touching the return address), the stack height of every
//! instruction relative to its function's entry, and the control each instruction lifts to.

use crate::analysis::{stack_delta, Flow, Graph, Tg, Var};
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::{BTreeMap, BTreeSet};

/// Stack height relative to the function entry (pushes are negative).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
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

/// A planned instruction start: its height and per-variant control.
#[derive(Clone, Debug)]
pub struct Node {
    pub h: H,
    pub ctl: Vec<(Ctl, Exit)>,
}

#[derive(Clone, Debug)]
pub struct Plan {
    pub pc: u16,
    pub top: bool,
    pub nodes: BTreeMap<u16, Node>,
    /// A callee returning at its entry height with no access above it.
    pub balanced: bool,
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

/// Plan the function entered at `pc` given the `JSR` targets lifted as calls.
pub fn plan(g: &Graph, pc: u16, top: bool, calls: &FxHashSet<u16>) -> Plan {
    let mut heights: FxHashMap<u16, H> = FxHashMap::default();
    let mut rets: FxHashMap<i32, BTreeSet<u16>> = FxHashMap::default();
    let mut all_rets: BTreeSet<u16> = BTreeSet::new();
    let mut pulls: FxHashSet<u16> = FxHashSet::default();
    let mut work = vec![pc];
    heights.insert(pc, H::Known(0));
    while let Some(p) = work.pop() {
        let h = heights[&p];
        for v in g.vars.get(&p).into_iter().flatten() {
            if matches!(v.flow, Flow::Rts(_) | Flow::Rti(_)) {
                pulls.insert(p);
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
                if !g.vars.contains_key(&t) {
                    continue;
                }
                let old = heights.get(&t).copied();
                let m = H::merge(old, ht);
                if old != Some(m) {
                    heights.insert(t, m);
                    work.push(t);
                }
            }
        }
    }
    let mut balanced = true;
    let nodes = heights
        .iter()
        .map(|(&p, &h)| {
            let ctl = g.vars[&p]
                .iter()
                .map(|v| {
                    let s = step(v, h, top, calls, &rets, &all_rets);
                    balanced &= !s.unbalanced;
                    (s.ctl, s.exit)
                })
                .collect();
            (p, Node { h, ctl })
        })
        .collect();
    Plan {
        pc,
        top,
        nodes,
        balanced,
    }
}

/// Plans for `entries` (outermost) and every callee they reach, entries first; the call
/// targets are the static `JSR` targets whose plans are balanced, by a decreasing fixpoint.
pub fn plan_all(g: &Graph, entries: &[u16]) -> Vec<Plan> {
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
            .filter(|&c| !plan(g, c, false, &calls).balanced)
            .collect();
        if bad.is_empty() {
            break;
        }
        for b in bad {
            calls.remove(&b);
        }
    }
    let mut plans: Vec<Plan> = entries.iter().map(|&e| plan(g, e, true, &calls)).collect();
    let mut seen: FxHashSet<u16> = FxHashSet::default();
    let mut i = 0;
    while i < plans.len() {
        let callees: Vec<u16> = plans[i]
            .nodes
            .values()
            .flat_map(|n| &n.ctl)
            .filter_map(|(c, _)| match c {
                Ctl::Call { f, .. } => Some(*f),
                _ => None,
            })
            .collect();
        for f in callees {
            if seen.insert(f) {
                plans.push(plan(g, f, false, &calls));
            }
        }
        i += 1;
    }
    plans
}
