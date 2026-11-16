//! Walks: the loads through a `(zp),Y` pair after an instruction that sets `Y`, as offsets of
//! the pointer plus `Y` there.
//!
//! An instruction writing `Y` other than `INY`/`DEY` is an *anchor*: after it `Y` is `Y₀` and
//! the pair `p`, so a load through the pair reads `p + Y₀ + d`. A forward dataflow per pair
//! carries, from each anchor until a store that may write the pair or another anchor, the
//! offsets `d` (`INY` adds one, `DEY` takes one) and a *marker walk*: an `INY` made only on
//! the path where the byte at `p + Y` was found not to be a marker ([`Mk`]: the sign bit, a
//! compare's carry or zero flag, or zero after its load) leaves every byte from the walk's
//! start to `p + Y` short of a marker, so `p + Y` lies at or before the first marker at or
//! after the start. A path incrementing `Y` without the test, or decrementing it, ends the
//! walk. Joins hull the offsets; different anchors give no anchor.

use crate::analysis::{flow_succs, Flow, Graph, Var};
use crate::objects::Cfg;
use rustc_hash::{FxHashMap, FxHashSet};

/// A marker byte: one at least `k` (`Ge(0x80)` the sign bit) or equal to `k`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Mk {
    Ge(u8),
    Eq(u8),
}

impl Mk {
    /// Whether byte `b` is a marker.
    pub fn hit(self, b: u8) -> bool {
        match self {
            Mk::Ge(k) => b >= k,
            Mk::Eq(k) => b == k,
        }
    }
    /// Whether a byte that is no marker of `self` is none of `need`.
    fn implies(self, need: Mk) -> bool {
        match (self, need) {
            (Mk::Ge(a), Mk::Ge(b) | Mk::Eq(b)) => a <= b,
            (Mk::Eq(a), Mk::Eq(b)) => a == b,
            (Mk::Eq(_), Mk::Ge(_)) => false,
        }
    }
    /// The marker a byte that is no marker of `a` or of `b` is none of.
    fn common(a: Mk, b: Mk) -> Option<Mk> {
        match () {
            _ if a.implies(b) => Some(b),
            _ if b.implies(a) => Some(a),
            _ => None,
        }
    }
}

/// What is known of the byte at `p + Y`: `A` holds it, `N` and `Z` are its, `Z` and `C` a
/// compare of it with a constant gave, or it is no marker.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct Cur {
    a: bool,
    nz: bool,
    z: Option<u8>,
    c: Option<u8>,
    ok: Option<Mk>,
}

/// The offsets of `Y` from an anchor's: `Y` is the anchor's plus one of `d`, or, for `run`,
/// plus one of its starts and the count of bytes from that start on that are no marker.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Walk {
    pub at: u16,
    pub d: Option<(i32, i32)>,
    pub run: Option<((i32, i32), Mk)>,
    cur: Cur,
}

/// Offsets wider than a byte's values are no bound.
fn hull(a: (i32, i32), b: (i32, i32)) -> Option<(i32, i32)> {
    let h = (a.0.min(b.0), a.1.max(b.1));
    (h.1 - h.0 < 0x100).then_some(h)
}

fn join(a: Option<Walk>, b: Option<Walk>) -> Option<Walk> {
    let (x, y) = (a?, b?);
    if x.at != y.at {
        return None;
    }
    let d = x.d.zip(y.d).and_then(|(a, b)| hull(a, b));
    let run = match (x.run, y.run) {
        (Some((s, m)), Some((t, n))) => Mk::common(m, n).zip(hull(s, t)).map(|(m, h)| (h, m)),
        (Some((s, m)), None) => y.d.and_then(|d| hull(s, d)).map(|h| (h, m)),
        (None, Some((s, m))) => x.d.and_then(|d| hull(s, d)).map(|h| (h, m)),
        (None, None) => None,
    };
    let same = |a: Option<u8>, b: Option<u8>| a.filter(|_| a == b);
    let cur = Cur {
        a: x.cur.a && y.cur.a,
        nz: x.cur.nz && y.cur.nz,
        z: same(x.cur.z, y.cur.z),
        c: same(x.cur.c, y.cur.c),
        ok: x.cur.ok.zip(y.cur.ok).and_then(|(a, b)| Mk::common(a, b)),
    };
    (d.is_some() || run.is_some()).then_some(Walk { d, run, cur, ..x })
}

/// The walk after `INY`: a walk goes on where the byte was found no marker of it, or starts
/// at the offsets `Y` held.
fn iny(w: Walk) -> Option<Walk> {
    let d = w.d.map(|(a, b)| (a + 1, b + 1));
    let run = match (w.run, w.cur.ok) {
        (Some((s, m)), Some(ok)) if ok.implies(m) => Some((s, m)),
        (_, Some(ok)) => w.d.map(|d| (d, ok)),
        _ => None,
    };
    (d.is_some() || run.is_some()).then_some(Walk {
        d,
        run,
        cur: Cur::default(),
        ..w
    })
}

/// The walk on the edge of branch `op` taken (or not): where the flag it reads says the byte
/// is no marker, the byte is none.
fn edge(op: u8, taken: bool, mut w: Walk) -> Walk {
    const BPL: u8 = 0x10;
    const BMI: u8 = 0x30;
    const BCC: u8 = 0x90;
    const BCS: u8 = 0xb0;
    const BNE: u8 = 0xd0;
    const BEQ: u8 = 0xf0;
    let ok = match (op, taken) {
        (BPL, true) | (BMI, false) if w.cur.nz => Some(Mk::Ge(0x80)),
        (BNE, true) | (BEQ, false) if w.cur.nz => Some(Mk::Eq(0)),
        (BNE, true) | (BEQ, false) => w.cur.z.map(Mk::Eq),
        (BCC, true) | (BCS, false) => w.cur.c.map(Mk::Ge),
        _ => None,
    };
    w.cur.ok = ok.or(w.cur.ok);
    w
}

/// The walk of pair `q` after variant `v` at `pc`, the stores at `kill` writing the pair.
fn step(
    g: &Graph,
    pc: u16,
    v: &Var,
    q: u8,
    s: Option<Walk>,
    kill: &FxHashSet<u16>,
) -> Option<Walk> {
    use di_pcode::reg::{A, C, N, Y, Z};
    const LDA: u8 = 0xb1;
    const LAX: u8 = 0xb3;
    const CMP: u8 = 0xc9;
    const INY: u8 = 0xc8;
    const DEY: u8 = 0x88;
    if kill.contains(&pc) {
        return None;
    }
    let writes = |r: usize| crate::objects::writes(v.op, r);
    let mut s = s.map(|mut w| {
        let imm = (!g.cells.contains(pc.wrapping_add(1) as usize))
            .then(|| g.img[pc.wrapping_add(1) as usize]);
        match v.op {
            LDA | LAX if imm == Some(q) => {
                w.cur = Cur {
                    a: true,
                    nz: true,
                    ok: w.cur.ok,
                    ..Cur::default()
                }
            }
            CMP if w.cur.a && imm.is_some() => (w.cur.nz, w.cur.z, w.cur.c) = (false, imm, imm),
            _ => {
                w.cur.a &= !writes(A);
                if writes(N) || writes(Z) {
                    (w.cur.nz, w.cur.z) = (false, None);
                }
                if writes(C) {
                    w.cur.c = None;
                }
            }
        }
        w
    });
    if writes(Y) {
        s = match v.op {
            INY => s.and_then(iny),
            DEY => s.and_then(|w| {
                let d = w.d.map(|(a, b)| (a - 1, b - 1))?;
                Some(Walk {
                    d: Some(d),
                    run: None,
                    cur: Cur::default(),
                    ..w
                })
            }),
            _ => Some(Walk {
                at: pc,
                d: Some((0, 0)),
                run: None,
                cur: Cur::default(),
            }),
        };
    }
    s
}

/// Per load of `loads` reached from an anchor alone, the walk of pair `q` reaching it: over
/// the program `cfg` from `roots`, the stores at `kill` writing the pair.
pub(crate) fn walks(
    cfg: &Cfg,
    roots: &[u16],
    q: u8,
    kill: &FxHashSet<u16>,
    loads: &[u16],
) -> FxHashMap<u16, Walk> {
    let g = cfg.g;
    let mut ins: FxHashMap<u16, Option<Walk>> = FxHashMap::default();
    let mut work: Vec<u16> = Vec::new();
    for &r in roots {
        ins.insert(r, None);
        work.push(r);
    }
    let mut queued: FxHashSet<u16> = work.iter().copied().collect();
    while let Some(pc) = work.pop() {
        queued.remove(&pc);
        let s = ins[&pc];
        let mut out: Vec<(u16, Option<Walk>)> = Vec::new();
        for v in g.vars.get(&pc).into_iter().flatten() {
            let o = step(g, pc, v, q, s, kill);
            match &v.flow {
                Flow::Br { t, f } => {
                    out.extend(t.all().iter().map(|&t| (t, o.map(|w| edge(v.op, true, w)))));
                    out.push((*f, o.map(|w| edge(v.op, false, w))));
                }
                Flow::Rts(t) | Flow::Rti(t) if t.is_empty() => {
                    out.extend(cfg.rets.iter().map(|&r| (r, o)));
                }
                fl => out.extend(flow_succs(fl).into_iter().map(|t| (t, o))),
            }
        }
        for (t, o) in out {
            if !g.vars.contains_key(&t) {
                continue;
            }
            let n = match ins.get(&t) {
                Some(&e) => join(e, o),
                None => o,
            };
            if ins.get(&t) != Some(&n) {
                ins.insert(t, n);
                if queued.insert(t) {
                    work.push(t);
                }
            }
        }
    }
    (loads.iter())
        .filter_map(|pc| Some((*pc, (*ins.get(pc)?)?)))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn markers_imply_their_weaker_markers() {
        assert!(Mk::Ge(0x80).hit(0xff) && !Mk::Ge(0x80).hit(0x7f));
        assert!(Mk::Eq(0).hit(0) && !Mk::Eq(0).hit(1));
        assert!(Mk::Ge(3).implies(Mk::Eq(5)) && !Mk::Eq(5).implies(Mk::Ge(9)));
        assert_eq!(Mk::common(Mk::Ge(3), Mk::Ge(9)), Some(Mk::Ge(9)));
        assert_eq!(Mk::common(Mk::Eq(5), Mk::Ge(3)), Some(Mk::Eq(5)));
        assert_eq!(Mk::common(Mk::Eq(5), Mk::Eq(6)), None);
    }

    #[test]
    fn joins_hull_offsets_and_keep_walks() {
        let w = |d, run| Walk {
            at: 1,
            d,
            run,
            cur: Cur::default(),
        };
        let a = w(Some((0, 0)), None);
        let b = w(None, Some(((0, 0), Mk::Ge(0x80))));
        assert_eq!(
            join(Some(a), Some(b)).unwrap().run,
            Some(((0, 0), Mk::Ge(0x80)))
        );
        assert_eq!(
            join(Some(a), Some(w(Some((3, 4)), None))).unwrap().d,
            Some((0, 4))
        );
        assert_eq!(join(Some(a), Some(w(Some((256, 256)), None))), None);
        assert_eq!(join(Some(a), Some(Walk { at: 2, ..a })), None);
        assert_eq!(join(Some(a), None), None);
        let ok = Walk {
            cur: Cur {
                ok: Some(Mk::Eq(0)),
                ..Cur::default()
            },
            ..a
        };
        let n = iny(ok).unwrap();
        assert_eq!((n.d, n.run), (Some((1, 1)), Some(((0, 0), Mk::Eq(0)))));
        assert_eq!(iny(n).unwrap().run, None);
        assert_eq!(
            edge(
                0x10,
                true,
                Walk {
                    cur: Cur {
                        nz: true,
                        ..Cur::default()
                    },
                    ..a
                }
            )
            .cur
            .ok,
            Some(Mk::Ge(0x80))
        );
    }
}
