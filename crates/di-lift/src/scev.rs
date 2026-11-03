//! Induction variables of the discovered program's loops.
//!
//! A loop is a head (target of a depth-first back edge) with the instruction starts that reach
//! it from it; one entered only at its head is analysed. An index register, or a byte that an
//! instruction of the loop loads and stores back changed (`INC`/`DEC`), is an induction variable
//! when every path from the head to each instruction start changes it by one constant, and every
//! path around the loop by a nonzero step (from the symbols of [`crate::vsa`]; an `ADC`/`SBC`
//! of a constant with the carry known counts). Its start is the hull of its intervals on the
//! entry edges. A loop whose one back edge is a conditional branch on a flag set on every
//! iteration from the variable alone has a trip bound: the most head visits per entry, found
//! by evaluating that flag on each value the variable takes there.

use crate::analysis::{absint_known, operand_bytes, Flow, Iv, Var};
use crate::vsa::{hull, meet, refine, shift, step, symbolic_in, Code, St, Sym, MEM};
use di_pcode::{reg, CtrlKind, Space, Template};
use petgraph::algo::tarjan_scc;
use petgraph::graphmap::DiGraphMap;
use rustc_hash::{FxHashMap, FxHashSet};

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
}

type Succs = FxHashMap<u16, Vec<u16>>;

/// The change of `var` over variant `v` at `pc` with the carry `carry`; `None` if not constant.
fn delta(code: &Code, pc: u16, v: &Var, var: Place, known: (Option<u8>, Option<u8>)) -> Option<u8> {
    let t = Template::of(v.op);
    let b = operand_bytes(code.img, code.cells, pc, t.len);
    match var {
        Place::Reg(r) => {
            let (sym, written, _) = symbolic_in(t.ops(), b, known, None);
            match sym[r as usize] {
                _ if written >> r & 1 == 0 => Some(0),
                Some(Sym::Lin(base, off)) if base == r => Some(off),
                _ => None,
            }
        }
        Place::Mem(a) => {
            let hit = v
                .acc
                .iter()
                .any(|x| x.store && !x.back && x.addr.meets(a as u32, a as u32 + 1));
            if !hit {
                return Some(0);
            }
            let (_, _, st) = symbolic_in(t.ops(), b, known, Some(a as u32));
            let exact = v
                .acc
                .iter()
                .filter(|x| x.store && !x.back && x.addr.meets(a as u32, a as u32 + 1))
                .all(|x| x.addr.value() == Some(a as u32));
            match st {
                Some(Some(Sym::Lin(MEM, off))) if exact => Some(off),
                _ => None,
            }
        }
    }
}

/// The induction variables of every loop of `vars`, given the interval states `st` at each
/// instruction start; `roots` are the entries.
pub(crate) fn find(
    code: &Code,
    vars: &FxHashMap<u16, Vec<Var>>,
    heads: &FxHashSet<u16>,
    succs: &Succs,
    st: &FxHashMap<u16, St>,
    roots: &[u16],
) -> Vec<Induction> {
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
    let mut graph: DiGraphMap<u16, ()> = DiGraphMap::new();
    for (&p, ss) in succs {
        graph.add_node(p);
        for &s in ss {
            graph.add_edge(p, s, ());
        }
    }
    let comps = tarjan_scc(&graph);
    let comp_of: FxHashMap<u16, usize> = comps
        .iter()
        .enumerate()
        .flat_map(|(i, c)| c.iter().map(move |&n| (n, i)))
        .collect();
    let roots_set: FxHashSet<u16> = roots.iter().copied().collect();
    let mut out = Vec::new();
    let mut hs: Vec<u16> = heads.iter().copied().collect();
    hs.sort_unstable();
    for h in hs.into_iter().filter(|h| !roots_set.contains(h)) {
        let body: FxHashSet<u16> = comps[comp_of[&h]].iter().copied().collect();
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
        let known = |n: u16| {
            let flag = |f: usize| st.get(&n).and_then(|s| s.env[f].value()).map(|c| c as u8);
            (flag(reg::C), flag(reg::D))
        };
        let mut ivs: Vec<Induction> = Vec::new();
        for var in cands {
            // Unset, a constant change since the head, or `None` for several.
            let mut at: FxHashMap<u16, Option<u8>> = [(h, Some(0))].into_iter().collect();
            let mut round: Option<Option<u8>> = None;
            let mut work = vec![h];
            while let Some(n) = work.pop() {
                let d = at[&n].and_then(|d| {
                    let ds: Option<Vec<u8>> = vars[&n]
                        .iter()
                        .map(|v| delta(code, n, v, var, known(n)))
                        .collect();
                    let ds = ds?;
                    ds.iter()
                        .all(|&x| x == ds[0])
                        .then(|| d.wrapping_add(ds[0]))
                });
                for &s in succs
                    .get(&n)
                    .into_iter()
                    .flatten()
                    .filter(|s| body.contains(s))
                {
                    if s == h {
                        round = Some(match round {
                            None => d,
                            Some(r) if r == d => r,
                            _ => None,
                        });
                        continue;
                    }
                    let new = match at.get(&s) {
                        None => d,
                        Some(&o) if o == d => continue,
                        Some(_) => None,
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
                    .into_iter()
                    .filter_map(|(n, d)| d.map(|d| (n, d)))
                    .collect(),
            });
        }
        if let [s] = back[..] {
            let t = ivs
                .iter()
                .find_map(|iv| trips(code, vars, st, &preds, &body, s, h, iv));
            for iv in &mut ivs {
                iv.trips = t;
            }
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
                            .map(|e| refine(&out, flag as usize, e.1))
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

/// The trip bound of the loop at `h` whose only back edge is from the conditional branch at
/// `s`, when the flag it tests is set on the straight chain before it from `iv` alone.
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
) -> Option<u32> {
    let [v] = &vars[&s][..] else { return None };
    let (Flow::Br { t, f }, CtrlKind::Br { flag, pol }) = (&v.flow, Template::of(v.op).ctrl) else {
        return None;
    };
    let on_taken = match (t.all().contains(&h), *f == h) {
        (true, false) if !body.contains(f) => true,
        (false, true) if t.all().iter().all(|x| !body.contains(x)) => false,
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
    let tpl = Template::of(sv.op);
    let b = operand_bytes(code.img, code.cells, set, tpl.len);
    let base = st.get(&set)?.env;
    let mut cont = [false; 0x100];
    for (x, c) in cont.iter_mut().enumerate() {
        let mut env = base;
        let known = match iv.var {
            Place::Reg(r) => {
                env[r as usize] = Iv::exact(x as u32);
                None
            }
            Place::Mem(a) => Some((a as u32, x as u8)),
        };
        absint_known(tpl.ops(), b, &mut env, &mut Vec::new(), known);
        let fl = env[flag as usize].value()?;
        *c = ((fl != 0) == (pol != 0)) == on_taken;
    }
    (iv.start.lo..=iv.start.hi)
        .map(|s0| {
            (0..0x100u32)
                .find(|&k| !cont[((s0 + k * iv.step as u32 + e as u32) & 0xff) as usize])
                .map(|k| k + 1)
        })
        .try_fold(0, |m, t| t.map(|t| m.max(t)))
}

/// Per instruction start, the interval of `Y` its loops' trip bounds give, where tighter than
/// none.
pub(crate) fn y_bounds(ivs: &[Induction]) -> FxHashMap<u16, Iv> {
    let mut out: FxHashMap<u16, Iv> = FxHashMap::default();
    for iv in ivs.iter().filter(|iv| iv.var == Place::Reg(reg::Y as u8)) {
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
