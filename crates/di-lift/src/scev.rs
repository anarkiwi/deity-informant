//! Induction variables of the discovered program's loops.
//!
//! A loop is a head (target of a depth-first back edge) with the instruction starts that reach
//! a back edge's source from it without passing it (its natural loop); one entered only at its
//! head is analysed. An index register, or a byte that an
//! instruction of the loop loads and stores back changed (`INC`/`DEC`), is an induction variable
//! when every path from the head to each instruction start changes it by one constant, and every
//! path around the loop by a nonzero step (from the symbols of [`crate::vsa`]; an `ADC`/`SBC`
//! of a constant with the carry known counts). Its start is the hull of its intervals on the
//! entry edges. A test every iteration passes (the conditional branch of the loop's one back
//! edge, or an exit dominating every back edge's source) on a flag set from the variable and
//! constant bytes gives a trip bound: the most head visits per entry, found by evaluating that
//! flag on each value the variable takes there.

use crate::analysis::{absint_known, operand_bytes, Flow, Iv, Tg, Var};
use crate::vsa::{hull, meet, refine, shift, step, symbolic_in, Code, St, Sym, MEM, VAR};
use di_pcode::{reg, CtrlKind, Space, Template};
use petgraph::algo::dominators::simple_fast;
use petgraph::graphmap::DiGraphMap;
use rustc_hash::{FxHashMap, FxHashSet};
use std::collections::BTreeSet;

/// What an induction variable is: a register (`lift()` index) or the byte at an address.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Place {
    Reg(u8),
    Mem(u16),
}

/// An induction variable of the loop at `head`.
#[derive(Clone, Debug)]
pub struct Induction {
    pub head: u16,
    pub var: Place,
    /// Its interval entering the loop.
    pub start: Iv,
    /// Its change per iteration, modulo 256.
    pub step: u8,
    /// The most times the head is reached per entry of the loop.
    pub trips: Option<u32>,
    /// Per instruction start of the loop, the change since the head, modulo 256.
    pub at: FxHashMap<u16, u8>,
    /// Per instruction start of the loop, `A`, `X` and `Y` as the variable's value at the
    /// head plus a constant, where they are.
    pub with: FxHashMap<u16, [Option<u8>; 3]>,
}

type Succs = FxHashMap<u16, Vec<u16>>;

/// The carry, the decimal flag and the data registers where known.
type Known = (Option<u8>, Option<u8>, [Option<u8>; 3]);

/// What the walk from a loop head knows at an instruction start: the variable's change since
/// the head, and each data register as the variable's value at the head plus a constant.
type Track = (Option<u8>, [Option<u8>; 3]);

fn merge(a: Track, b: Track) -> Track {
    let m = |x: Option<u8>, y: Option<u8>| if x == y { x } else { None };
    (m(a.0, b.0), std::array::from_fn(|q| m(a.1[q], b.1[q])))
}

/// [`Track`] after variant `v` at `pc` from `tr`, with `known` before it.
fn advance(code: &Code, pc: u16, v: &Var, var: Place, tr: Track, known: Known) -> Track {
    let t = Template::of(v.op);
    let b = operand_bytes(code.img, code.cells, pc, t.len);
    let init = std::array::from_fn(|q| match (tr.1[q], known.2[q]) {
        (Some(k), _) => Some(Sym::Lin(VAR, k)),
        (None, Some(c)) => Some(Sym::K(c)),
        _ => None,
    });
    let mem = match var {
        Place::Mem(a) => Some(a as u32),
        Place::Reg(_) => None,
    };
    let (sym, written, st) = symbolic_in(t.ops(), b, (known.0, known.1, init), mem);
    let off = |s: Option<Sym>| match s {
        Some(Sym::Lin(VAR, k)) => Some(k),
        Some(Sym::Lin(MEM, o)) => tr.0.map(|d| d.wrapping_add(o)),
        _ => None,
    };
    let regs: [Option<u8>; 3] = std::array::from_fn(|q| {
        if written >> q & 1 == 1 {
            off(sym[q])
        } else {
            tr.1[q]
        }
    });
    let change = match var {
        Place::Reg(r) => regs[r as usize],
        Place::Mem(a) => {
            let hits: Vec<_> = v
                .acc
                .iter()
                .filter(|x| x.store && !x.back && x.addr.meets(a as u32, a as u32 + 1))
                .collect();
            match st {
                _ if hits.is_empty() => tr.0,
                Some(s) if hits.iter().all(|x| x.addr.value() == Some(a as u32)) => off(s),
                _ => None,
            }
        }
    };
    (change, regs)
}

/// The places holding one value: data registers (bits of `A`, `X`, `Y`) and constant
/// addresses (`STX a` ... `LDX a`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
struct Holds {
    regs: u32,
    mem: BTreeSet<u16>,
}

/// One place: a data register or a constant address.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Loc {
    Reg(u8),
    Mem(u16),
}

impl Holds {
    fn of(l: Loc) -> Holds {
        match l {
            Loc::Reg(q) => Holds {
                regs: 1 << q,
                mem: BTreeSet::new(),
            },
            Loc::Mem(a) => Holds {
                regs: 0,
                mem: [a].into_iter().collect(),
            },
        }
    }
    fn locs(&self) -> Vec<Loc> {
        (0..3)
            .filter(|q| self.regs >> q & 1 == 1)
            .map(Loc::Reg)
            .chain(self.mem.iter().map(|&a| Loc::Mem(a)))
            .collect()
    }
    fn meet(&self, o: &Holds) -> Holds {
        Holds {
            regs: self.regs & o.regs,
            mem: self.mem.intersection(&o.mem).copied().collect(),
        }
    }
    fn join(&mut self, o: &Holds) {
        self.regs |= o.regs;
        self.mem.extend(&o.mem);
    }
}

/// `h` after variant `v` at `pc`: a register written with the value, a load of an address
/// holding it, a store of it to a constant address holds it; any other store forgets the
/// addresses it may reach.
fn hold(code: &Code, pc: u16, v: &Var, h: &Holds) -> Holds {
    let t = Template::of(v.op);
    let b = operand_bytes(code.img, code.cells, pc, t.len);
    let init = std::array::from_fn(|q| (h.regs >> q & 1 == 1).then_some(Sym::Lin(VAR, 0)));
    let held = |s: Option<Sym>, mem: bool| match s {
        Some(Sym::Lin(VAR, 0)) => true,
        Some(Sym::Lin(MEM, 0)) => mem,
        _ => false,
    };
    let load = v
        .acc
        .iter()
        .filter(|a| !a.store && !a.dummy)
        .find_map(|a| a.addr.value().filter(|&x| h.mem.contains(&(x as u16))));
    let (sym, written, _) = symbolic_in(t.ops(), b, (None, None, init), load);
    let regs = (0..3).fold(0, |m, q| {
        let r = if written >> q & 1 == 1 {
            held(sym[q], load.is_some())
        } else {
            h.regs >> q & 1 == 1
        };
        m | (r as u32) << q
    });
    let mut out = Holds {
        regs,
        mem: h.mem.clone(),
    };
    for a in v.acc.iter().filter(|a| a.store) {
        out.mem.retain(|&m| !a.addr.meets(m as u32, m as u32 + 1));
        if let Some(x) = a.addr.value() {
            let (_, _, st) = symbolic_in(t.ops(), b, (None, None, init), Some(x));
            if held(st.flatten(), h.mem.contains(&(x as u16))) {
                out.mem.insert(x as u16);
            }
        }
    }
    out
}

/// The places holding the value one place held at a call: per instruction start the call runs
/// (its callees' included) on every path there, and when it returns.
#[derive(Clone)]
struct Held {
    at: FxHashMap<u16, Holds>,
    back: Holds,
    /// The intervals the call stores to.
    stores: Vec<Iv>,
}

type Memo = FxHashMap<(u16, u16, Loc), Option<Held>>;

/// [`Held`] of the value at `o` for a call of `f` returning to `ret`, when every path from `f`
/// returns to `ret`: calls within it composed from theirs, or walked through where they do not
/// return (a callee unwinding to a frame above it), `BRK` as a call of its handlers returning
/// past the signature byte, and a return or computed jump elsewhere followed to its targets.
fn held(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    succs: &Succs,
    (f, ret, o): (u16, u16, Loc),
    memo: &mut Memo,
) -> Option<Held> {
    if let Some(c) = memo.get(&(f, ret, o)) {
        return c.clone();
    }
    memo.insert((f, ret, o), None);
    let mut c = Held {
        at: [(f, Holds::of(o))].into_iter().collect(),
        back: Holds::default(),
        stores: Vec::new(),
    };
    let mut back: Option<Holds> = None;
    let mut nested: FxHashMap<u16, Holds> = FxHashMap::default();
    let mut work = vec![f];
    let mut seen = FxHashSet::default();
    while let Some(pc) = work.pop() {
        let first = seen.insert(pc);
        let h = c.at[&pc].clone();
        for v in vars.get(&pc)? {
            let out = hold(code, pc, v, &h);
            if first {
                c.stores
                    .extend(v.acc.iter().filter(|a| a.store).map(|a| a.addr));
            }
            let returns = succs.get(&pc).is_some_and(|s| s.contains(&ret));
            let next: Vec<(u16, Holds)> = match &v.flow {
                Flow::Next(_) | Flow::Br { .. } | Flow::Jmp(Tg::At(_)) => {
                    crate::analysis::flow_succs(&v.flow)
                        .into_iter()
                        .map(|s| (s, out.clone()))
                        .collect()
                }
                Flow::Jsr {
                    t: Tg::At(g),
                    ret: r,
                } => sub(
                    code,
                    vars,
                    succs,
                    (&[*g], *r),
                    &out,
                    first,
                    &mut c,
                    &mut nested,
                    memo,
                ),
                Flow::Brk(ts) if !ts.is_empty() => {
                    let r = pc.wrapping_add(2);
                    sub(
                        code,
                        vars,
                        succs,
                        (ts, r),
                        &out,
                        first,
                        &mut c,
                        &mut nested,
                        memo,
                    )
                }
                Flow::Rts(_) | Flow::Rti(_) if returns => {
                    back = Some(back.map_or(out.clone(), |b| b.meet(&out)));
                    Vec::new()
                }
                Flow::Rts(_) | Flow::Rti(_) | Flow::JmpInd(_) => {
                    let ts = succs.get(&pc).filter(|s| !s.is_empty())?;
                    ts.iter().map(|&s| (s, out.clone())).collect()
                }
                _ => return None,
            };
            for (s, hs) in next {
                let new = match c.at.get(&s) {
                    None => hs,
                    Some(o) => o.meet(&hs),
                };
                if c.at.get(&s) != Some(&new) || !seen.contains(&s) {
                    c.at.insert(s, new);
                    work.push(s);
                }
            }
        }
    }
    // A call that never returns keeps everything vacuously.
    c.back = back.unwrap_or_else(|| Holds {
        regs: 0b111,
        ..Holds::of(o)
    });
    for (p, h) in nested {
        let m = c.at.get(&p).map_or(h.clone(), |o| o.meet(&h));
        c.at.insert(p, m);
    }
    memo.insert((f, ret, o), Some(c.clone()));
    Some(c)
}

/// The successors of a call of `gs` returning to `r` with `out` holding the value: the return
/// site holding what the places holding it at the call hold on its return, the calls'
/// instructions noted in `nested` and their stores in `c` (on the call's `first` visit), where
/// each callee is composed; else the callee's entry, walked through.
#[allow(clippy::too_many_arguments)]
fn sub(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    succs: &Succs,
    (gs, r): (&[u16], u16),
    out: &Holds,
    first: bool,
    c: &mut Held,
    nested: &mut FxHashMap<u16, Holds>,
    memo: &mut Memo,
) -> Vec<(u16, Holds)> {
    let mut next = Vec::new();
    let mut back: Option<Holds> = None;
    for &g in gs {
        let Some(base) = held(code, vars, succs, (g, r, Loc::Reg(0)), memo) else {
            next.push((g, out.clone()));
            continue;
        };
        let parts: Option<Vec<Held>> = out
            .locs()
            .into_iter()
            .map(|l| held(code, vars, succs, (g, r, l), memo))
            .collect();
        let Some(parts) = parts else {
            next.push((g, out.clone()));
            continue;
        };
        let mut h = Holds::default();
        let mut at: FxHashMap<u16, Holds> =
            base.at.keys().map(|&p| (p, Holds::default())).collect();
        for n in &parts {
            h.join(&n.back);
            for (p, x) in &n.at {
                at.entry(*p).or_default().join(x);
            }
        }
        for (p, x) in at {
            let m = nested.get(&p).map_or(x.clone(), |o| o.meet(&x));
            nested.insert(p, m);
        }
        if first {
            c.stores.extend(base.stores);
        }
        back = Some(back.map_or(h.clone(), |b| b.meet(&h)));
    }
    next.extend(back.map(|b| (r, b)));
    next
}

/// What a call of `f` returning to `ret` may change, when every path from `f` returns to `ret`
/// ([`held`]).
#[derive(Clone)]
struct Call {
    /// Per instruction start it runs, the data registers (bits of `A`, `X`, `Y`) holding their
    /// values at the call on every path there.
    keeps: FxHashMap<u16, u32>,
    /// The data registers holding their values at the call when it returns.
    kept: u32,
    stores: Vec<Iv>,
}

fn callee(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    succs: &Succs,
    f: u16,
    ret: u16,
    memo: &mut Memo,
) -> Option<Call> {
    let hs: Vec<Held> = (0..3)
        .map(|q| held(code, vars, succs, (f, ret, Loc::Reg(q)), memo))
        .collect::<Option<_>>()?;
    let mut keeps: FxHashMap<u16, u32> = FxHashMap::default();
    let mut kept = 0;
    for (q, h) in hs.iter().enumerate() {
        for (&p, x) in &h.at {
            *keeps.entry(p).or_default() |= x.regs & 1 << q;
        }
        kept |= h.back.regs & 1 << q;
    }
    let h = hs.into_iter().next()?;
    Some(Call {
        keeps,
        kept,
        stores: h.stores,
    })
}

/// `succs` with each call whose callee returns to its return site ([`callee`]) also an edge to
/// that site, and the edges to it from the returns the call runs dropped, so a loop calling a
/// routine others also call is entered only at its head; and the summary of each such call by
/// its start.
fn collapse(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    succs: &Succs,
) -> (Succs, FxHashMap<u16, Call>) {
    let (mut out, mut calls, mut memo) =
        (succs.clone(), FxHashMap::default(), FxHashMap::default());
    for (&pc, vs) in vars {
        let [Var {
            flow: Flow::Jsr { t: Tg::At(f), ret },
            ..
        }] = &vs[..]
        else {
            continue;
        };
        if let Some(c) = callee(code, vars, succs, *f, *ret, &mut memo) {
            let s = out.entry(pc).or_default();
            if !s.contains(ret) {
                s.push(*ret);
            }
            calls.insert(pc, c);
        }
    }
    for (pc, c) in &calls {
        let Some(Var {
            flow: Flow::Jsr { ret, .. },
            ..
        }) = vars[pc].first()
        else {
            continue;
        };
        let rets = c.keeps.keys().filter(|p| {
            vars.get(p).is_some_and(|vs| {
                vs.iter()
                    .any(|v| matches!(v.flow, Flow::Rts(_) | Flow::Rti(_)))
            })
        });
        for r in rets {
            if let Some(s) = out.get_mut(r) {
                s.retain(|x| x != ret);
            }
        }
    }
    (out, calls)
}

/// The change of register `r` at the instructions of the calls the loop `body` makes where `r`
/// holds its value at the call, as at their call sites (`at`), when only the loop and those
/// calls run them: a voice loop's index holds its strided values in the routines it calls.
fn callee_steps(
    calls: &FxHashMap<u16, Call>,
    body: &FxHashSet<u16>,
    preds: &Succs,
    r: u8,
    at: &mut FxHashMap<u16, Track>,
) {
    let mut inner: FxHashMap<u16, Option<u8>> = FxHashMap::default();
    for (&n, c) in calls {
        let Some(&(Some(e), _)) = at.get(&n).filter(|_| body.contains(&n)) else {
            continue;
        };
        for (&pc, &k) in c.keeps.iter().filter(|(pc, _)| !body.contains(pc)) {
            let held = (k >> r & 1 == 1).then_some(e);
            let x = inner.entry(pc).or_insert(held);
            if *x != held {
                *x = None;
            }
        }
    }
    let closed = inner.keys().all(|pc| {
        preds
            .get(pc)
            .into_iter()
            .flatten()
            .all(|p| body.contains(p) || inner.contains_key(p))
    });
    if closed {
        for (pc, e) in inner {
            if let Some(e) = e {
                at.insert(pc, (Some(e), [None; 3]));
            }
        }
    }
}

/// The induction variables of every loop of `vars`, given the interval states `st` at each
/// instruction start; `roots` are the entries, `konst` gives the bytes taken as constant and
/// each a trip bound read is appended to `reads`.
#[allow(clippy::too_many_arguments)]
pub(crate) fn find(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    heads: &FxHashSet<u16>,
    succs: &Succs,
    st: &FxHashMap<u16, St>,
    roots: &[u16],
    konst: &dyn Fn(u32) -> Option<u8>,
    reads: &mut Vec<u16>,
) -> Vec<Induction> {
    let (succs, calls) = collapse(code, vars, succs);
    let succs = &succs;
    let mut preds: Succs = FxHashMap::default();
    for (&p, ss) in succs {
        for &s in ss {
            preds.entry(s).or_default().push(p);
        }
    }
    for p in preds.values_mut() {
        p.sort_unstable();
        p.dedup();
    }
    let none = Vec::new();
    let pr = |n: u16| preds.get(&n).unwrap_or(&none);
    // Dominators from a root above the entries: a loop's back edges come from the starts its
    // head dominates.
    const ROOT: u32 = 0x10000;
    let mut graph: DiGraphMap<u32, ()> = DiGraphMap::new();
    for &r in roots {
        graph.add_edge(ROOT, r as u32, ());
    }
    for (&p, ss) in succs {
        graph.add_node(p as u32);
        for &s in ss {
            graph.add_edge(p as u32, s as u32, ());
        }
    }
    let dom = simple_fast(&graph, ROOT);
    let dominated = |n: u16, h: u16| {
        dom.dominators(n as u32)
            .is_some_and(|mut d| d.any(|x| x == h as u32))
    };
    let roots_set: FxHashSet<u16> = roots.iter().copied().collect();
    let mut out = Vec::new();
    let mut hs: Vec<u16> = heads.iter().copied().collect();
    hs.sort_unstable();
    for h in hs.into_iter().filter(|h| !roots_set.contains(h)) {
        let mut body: FxHashSet<u16> = [h].into_iter().collect();
        let mut stack: Vec<u16> = pr(h).iter().copied().filter(|&p| dominated(p, h)).collect();
        while let Some(n) = stack.pop() {
            if body.insert(n) {
                stack.extend(pr(n));
            }
        }
        let entries: Vec<(u16, u16)> = body
            .iter()
            .flat_map(|&n| {
                pr(n)
                    .iter()
                    .filter(|p| !body.contains(p))
                    .map(move |&p| (p, n))
            })
            .collect();
        if entries.is_empty()
            || entries.iter().any(|e| e.1 != h)
            || body.iter().any(|&n| n != h && roots_set.contains(&n))
        {
            continue;
        }
        let back: Vec<u16> = pr(h).iter().copied().filter(|p| body.contains(p)).collect();
        let mut cands: Vec<Place> = (0..3).map(Place::Reg).collect();
        let mut mems: Vec<u16> = body
            .iter()
            .flat_map(|n| vars[n].iter())
            .flat_map(|v| {
                v.acc.iter().filter(|x| x.store && !x.back).filter_map(|x| {
                    let a = x.addr.value()?;
                    v.acc
                        .iter()
                        .any(|l| !l.store && !l.dummy && l.addr.value() == Some(a))
                        .then_some(a as u16)
                })
            })
            .collect();
        mems.sort_unstable();
        mems.dedup();
        cands.extend(mems.into_iter().map(Place::Mem));
        let known = |n: u16| -> Known {
            let exact = |f: usize| st.get(&n).and_then(|s| s.env[f].value()).map(|c| c as u8);
            (exact(reg::C), exact(reg::D), std::array::from_fn(exact))
        };
        let mut ivs: Vec<Induction> = Vec::new();
        for var in cands {
            let head: Track = match var {
                Place::Reg(r) => (
                    Some(0),
                    std::array::from_fn(|q| (q == r as usize).then_some(0)),
                ),
                Place::Mem(_) => (Some(0), [None; 3]),
            };
            let mut at: FxHashMap<u16, Track> = [(h, head)].into_iter().collect();
            let mut round: Option<Option<u8>> = None;
            let mut work = vec![h];
            while let Some(n) = work.pop() {
                let mut d = vars[&n]
                    .iter()
                    .map(|v| advance(code, n, v, var, at[&n], known(n)))
                    .reduce(merge)
                    .unwrap();
                if let Some(c) = calls.get(&n) {
                    for q in 0..3 {
                        if c.kept >> q & 1 == 0 {
                            d.1[q] = None;
                        }
                    }
                    let lost = match var {
                        Place::Reg(r) => c.kept >> r & 1 == 0,
                        Place::Mem(a) => c.stores.iter().any(|x| x.meets(a as u32, a as u32 + 1)),
                    };
                    if lost {
                        d.0 = None;
                    }
                }
                for &s in succs
                    .get(&n)
                    .into_iter()
                    .flatten()
                    .filter(|s| body.contains(s))
                {
                    if s == h {
                        round = Some(match round {
                            None => d.0,
                            Some(r) if r == d.0 => r,
                            _ => None,
                        });
                        continue;
                    }
                    let new = match at.get(&s) {
                        None => d,
                        Some(&o) if merge(o, d) == o => continue,
                        Some(&o) => merge(o, d),
                    };
                    if at.get(&s) != Some(&new) {
                        at.insert(s, new);
                        work.push(s);
                    }
                }
            }
            let Some(Some(stp)) = round.filter(|r| r.is_some_and(|x| x != 0)) else {
                continue;
            };
            if let Place::Reg(r) = var {
                callee_steps(&calls, &body, &preds, r, &mut at);
            }
            let start = entries
                .iter()
                .map(|&(p, _)| entry_value(code, vars, st, &preds, heads, roots, p, h, var))
                .reduce(hull)
                .unwrap();
            ivs.push(Induction {
                head: h,
                var,
                start,
                step: stp,
                trips: None,
                at: at
                    .iter()
                    .filter_map(|(&n, d)| d.0.map(|e| (n, e)))
                    .collect(),
                with: at.into_iter().map(|(n, d)| (n, d.1)).collect(),
            });
        }
        // Tests every iteration passes: the only back edge's, and each exit dominating every
        // back edge's source.
        let mut tests: Vec<u16> = match back[..] {
            [s] => vec![s],
            _ => Vec::new(),
        };
        let mut exits: Vec<u16> = body
            .iter()
            .copied()
            .filter(|&n| {
                vars[&n].iter().any(|v| matches!(v.flow, Flow::Br { .. }))
                    && succs
                        .get(&n)
                        .is_some_and(|ss| ss.iter().any(|x| !body.contains(x)))
                    && back.iter().all(|&b| dominated(b, n))
            })
            .collect();
        exits.sort_unstable();
        tests.extend(exits);
        let mut t: Option<u32> = None;
        for &s in &tests {
            let mut r = Vec::new();
            let b = ivs
                .iter()
                .find_map(|iv| trips(code, vars, st, &preds, &body, s, h, iv, konst, &mut r));
            if let Some(b) = b.filter(|&b| t.is_none_or(|t| b < t)) {
                t = Some(b);
                reads.extend(r);
            }
        }
        for iv in &mut ivs {
            iv.trips = t;
        }
        out.extend(ivs);
    }
    out
}

/// The interval of `var` on the edge from `p` to the loop head `h`.
#[allow(clippy::too_many_arguments)]
fn entry_value(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    st: &FxHashMap<u16, St>,
    preds: &Succs,
    heads: &FxHashSet<u16>,
    roots: &[u16],
    p: u16,
    h: u16,
    var: Place,
) -> Iv {
    let Some(s) = st.get(&p) else {
        return Iv::full(1);
    };
    match var {
        Place::Reg(r) => vars[&p]
            .iter()
            .flat_map(|v| {
                let out = step(code, p, v, s, &mut Vec::new());
                let edges: Vec<Option<St>> = match (&v.flow, Template::of(v.op).ctrl) {
                    (Flow::Br { t, f }, CtrlKind::Br { flag, pol }) => {
                        [(t.all().contains(&h), pol != 0), (*f == h, pol == 0)]
                            .into_iter()
                            .filter(|e| e.0)
                            .map(|e| refine(code, &out, flag as usize, e.1))
                            .collect()
                    }
                    _ => vec![Some(out)],
                };
                edges.into_iter().flatten().map(|e| e.env[r as usize])
            })
            .reduce(hull)
            .unwrap_or(Iv::full(1)),
        Place::Mem(a) => after(vars, p, a, mem_in(vars, preds, heads, roots, a).get(&p)),
    }
}

/// The interval of the byte at `a` after the instruction at `pc`, given it before (`None`:
/// unknown).
fn after(vars: &FxHashMap<u16, Vec<Var>>, pc: u16, a: u16, before: Option<&Iv>) -> Iv {
    let hits: Vec<_> = vars[&pc]
        .iter()
        .flat_map(|v| &v.acc)
        .filter(|x| x.store && !x.back && x.addr.meets(a as u32, a as u32 + 1))
        .collect();
    match (&vars[&pc][..], &hits[..]) {
        (_, []) => before.copied().unwrap_or(Iv::full(1)),
        ([_], [x]) if x.addr.value() == Some(a as u32) => x.val,
        _ => Iv::full(1),
    }
}

/// Per instruction start, the interval of the byte at `a` on every path reaching it (from
/// any value at the entries; widened to any value at loop heads).
fn mem_in(
    vars: &FxHashMap<u16, Vec<Var>>,
    preds: &Succs,
    heads: &FxHashSet<u16>,
    roots: &[u16],
    a: u16,
) -> FxHashMap<u16, Iv> {
    let mut succs: Succs = FxHashMap::default();
    for (&s, ps) in preds {
        for &p in ps {
            succs.entry(p).or_default().push(s);
        }
    }
    let mut val: FxHashMap<u16, Iv> = roots.iter().map(|&r| (r, Iv::full(1))).collect();
    let mut work: Vec<u16> = roots.to_vec();
    while let Some(n) = work.pop() {
        let out = after(vars, n, a, val.get(&n));
        for &s in succs.get(&n).into_iter().flatten() {
            let new = match val.get(&s) {
                None => out,
                Some(&o) if heads.contains(&s) && hull(o, out) != o => Iv::full(1),
                Some(&o) => hull(o, out),
            };
            if val.get(&s) != Some(&new) {
                val.insert(s, new);
                work.push(s);
            }
        }
    }
    val
}

/// The trip bound of the loop at `h` from a test every iteration passes: the conditional
/// branch at `s` (the only back edge's, or one a chain of `JMP`s alone leads to it from, or
/// an exit), when the flag it tests is set on the straight chain before it from `iv` alone
/// and the bytes `konst` gives (each read is appended to `reads`). A value the flag is not
/// known at continues the loop.
#[allow(clippy::too_many_arguments)]
fn trips(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    st: &FxHashMap<u16, St>,
    preds: &Succs,
    body: &FxHashSet<u16>,
    s: u16,
    h: u16,
    iv: &Induction,
    konst: &dyn Fn(u32) -> Option<u8>,
    reads: &mut Vec<u16>,
) -> Option<u32> {
    let mut s = s;
    while let [Var {
        flow: Flow::Jmp(Tg::At(_)),
        ..
    }] = &vars[&s][..]
    {
        s = match &preds.get(&s)?[..] {
            [q] if body.contains(q) => *q,
            _ => return None,
        };
    }
    let [v] = &vars[&s][..] else { return None };
    let (Flow::Br { t, f }, CtrlKind::Br { flag, pol }) = (&v.flow, Template::of(v.op).ctrl) else {
        return None;
    };
    let inside = |x: &u16| body.contains(x);
    let on_taken = match (t.all().iter().all(inside), inside(f)) {
        (true, false) => true,
        (false, true) if !t.all().iter().any(inside) => false,
        _ => return None,
    };
    let writes = |pc: u16| {
        vars[&pc].iter().any(|v| {
            Template::of(v.op)
                .ops()
                .iter()
                .any(|o| o.out.space() == Space::Reg && o.out.off() == flag as u32)
        })
    };
    let mut set = s;
    loop {
        if set == h {
            return None;
        }
        set = match &preds.get(&set)?[..] {
            [q] if body.contains(q) => *q,
            _ => return None,
        };
        if writes(set) {
            break;
        }
    }
    let [sv] = &vars[&set][..] else { return None };
    let e = *iv.at.get(&set)?;
    let with = iv.with.get(&set).copied().unwrap_or([None; 3]);
    let tpl = Template::of(sv.op);
    let b = operand_bytes(code.img, code.cells, set, tpl.len);
    let base = st.get(&set)?.env;
    let mut cont = [false; 0x100];
    let read = std::cell::RefCell::new(Vec::new());
    for (x, c) in cont.iter_mut().enumerate() {
        let mut env = base;
        for (q, k) in with.iter().enumerate() {
            if let Some(k) = k {
                env[q] = Iv::exact((x as u32 + *k as u32 + 0x100 - e as u32) & 0xff);
            }
        }
        let mem = match iv.var {
            Place::Reg(r) => {
                env[r as usize] = Iv::exact(x as u32);
                None
            }
            Place::Mem(a) => Some(a as u32),
        };
        let known = |a: u32| match mem {
            Some(m) if m == a => Some(x as u8),
            _ => {
                let k = konst(a);
                if k.is_some() {
                    read.borrow_mut().push(a as u16);
                }
                k
            }
        };
        absint_known(tpl.ops(), b, &mut env, &mut Vec::new(), &known);
        *c = env[flag as usize]
            .value()
            .is_none_or(|fl| ((fl != 0) == (pol != 0)) == on_taken);
    }
    let t = (iv.start.lo..=iv.start.hi)
        .map(|s0| {
            (0..0x100u32)
                .find(|&k| !cont[((s0 + k * iv.step as u32 + e as u32) & 0xff) as usize])
                .map(|k| k + 1)
        })
        .try_fold(0, |m, t| t.map(|t| m.max(t)));
    if t.is_some() {
        reads.extend(read.into_inner());
    }
    t
}

/// Per instruction start of a loop with a trip bound (and of the calls it makes that leave
/// the register alone), the interval of index register `r` as an induction variable: its
/// start plus its change since the head, over the trips.
pub(crate) fn bounds(ivs: &[Induction], r: usize) -> FxHashMap<u16, Iv> {
    let mut out: FxHashMap<u16, Iv> = FxHashMap::default();
    for iv in ivs.iter().filter(|iv| iv.var == Place::Reg(r as u8)) {
        let Some(t) = iv.trips else { continue };
        let span = (t as i32 - 1) * iv.step as i8 as i32;
        for (&pc, &e) in &iv.at {
            let b = shift(iv.start, e);
            let (lo, hi) = (b.lo as i32 + span.min(0), b.hi as i32 + span.max(0));
            if b == Iv::full(1) || lo < 0 || hi > 0xff {
                continue;
            }
            let r = Iv {
                lo: lo as u32,
                hi: hi as u32,
            };
            let m = out.get(&pc).map_or(Some(r), |&o| meet(o, r));
            if let Some(m) = m {
                out.insert(pc, m);
            }
        }
    }
    out
}
