//! Known bits: per value, the bits that hold the same on every path (a tristate number). The
//! interval analysis keeps them for `A`, `X` and `Y`, so a value set such as `{0, 2}` (an index
//! doubled by `ASL`, or the hull of the constants 0 and 2) is not widened to `[0, 2]`.

use crate::analysis::{slot_iv, Env, Iv};
use crate::ir::mask;
use di_pcode::{Op, Opc, Slot, Space, Vn, UNIQUE_SIZE};

/// Bits `u` unknown, the others equal to those of `v` (`v & u == 0`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub struct Kb {
    pub v: u32,
    pub u: u32,
}

impl Kb {
    pub const fn exact(x: u32) -> Kb {
        Kb { v: x, u: 0 }
    }
    pub const fn any(size: u32) -> Kb {
        Kb {
            v: 0,
            u: mask(size),
        }
    }
    /// The bits shared by every value of `iv`: those above its highest differing bit.
    pub fn of(iv: Iv) -> Kb {
        let d = iv.lo ^ iv.hi;
        let u = if d == 0 {
            0
        } else {
            u32::MAX >> d.leading_zeros()
        };
        Kb { v: iv.lo & !u, u }
    }
    /// What both say.
    pub fn meet(self, o: Kb) -> Kb {
        let u = self.u & o.u;
        Kb {
            v: (self.v | o.v) & !u,
            u,
        }
    }
    /// What holds of either.
    pub fn join(self, o: Kb) -> Kb {
        let u = self.u | o.u | (self.v ^ o.v);
        Kb { v: self.v & !u, u }
    }
    pub fn has(self, x: u32) -> bool {
        x & !self.u == self.v
    }
    /// The values of `iv` these bits allow.
    pub fn values(self, iv: Iv) -> impl Iterator<Item = u32> {
        (iv.lo..=iv.hi).filter(move |&x| self.has(x))
    }
    fn masked(self, m: u32) -> Kb {
        Kb {
            v: self.v & m,
            u: self.u & m,
        }
    }
}

/// Known bits of `opc(a, b)` with an `out`-byte result.
fn op(opc: Opc, a: Kb, b: Kb, out: u32) -> Kb {
    let m = mask(out);
    let r = match opc {
        Opc::Copy | Opc::IntZext => a,
        Opc::IntAnd => {
            let v = a.v & b.v;
            Kb {
                v,
                u: (a.v | a.u) & (b.v | b.u) & !v,
            }
        }
        Opc::IntOr => {
            let v = a.v | b.v;
            Kb {
                v,
                u: (a.u | b.u) & !v,
            }
        }
        Opc::IntXor => {
            let u = a.u | b.u;
            Kb {
                v: (a.v ^ b.v) & !u,
                u,
            }
        }
        Opc::IntAdd => {
            let (sv, su) = (a.v.wrapping_add(b.v), a.u.wrapping_add(b.u));
            let mu = (sv.wrapping_add(su) ^ sv) | a.u | b.u;
            Kb { v: sv & !mu, u: mu }
        }
        Opc::IntSub => {
            let dv = a.v.wrapping_sub(b.v);
            let mu = (dv.wrapping_add(a.u) ^ dv.wrapping_sub(b.u)) | a.u | b.u;
            Kb { v: dv & !mu, u: mu }
        }
        Opc::IntLeft | Opc::IntRight if b.u == 0 && b.v < 32 => {
            if opc == Opc::IntLeft {
                Kb {
                    v: a.v << b.v,
                    u: a.u << b.v,
                }
            } else {
                Kb {
                    v: a.v >> b.v,
                    u: a.u >> b.v,
                }
            }
        }
        Opc::IntEqual | Opc::IntNotEqual | Opc::IntLess | Opc::IntLessEqual | Opc::IntCarry => {
            Kb::any(1)
        }
        _ => Kb::any(out),
    };
    r.masked(m)
}

/// Known bits of the data registers `A`, `X`, `Y` after `ops` (operand bytes `b`, `None` for
/// cells) from registers `env` and the data registers' bits `kb`: `Some` for those written.
pub fn eval(ops: &[Op], b: [Option<u8>; 2], env: &Env, kb: &[Kb; 3]) -> [Option<Kb>; 3] {
    let mut reg: [Kb; 16] = std::array::from_fn(|r| {
        let k = Kb::of(env[r]);
        kb.get(r).map_or(k, |x| x.meet(k))
    });
    let mut uniq = [Kb::any(4); UNIQUE_SIZE];
    let mut out = [None; 3];
    for o in ops {
        let get = |v: Vn, reg: &[Kb; 16], uniq: &[Kb; UNIQUE_SIZE]| match v.space() {
            Space::Const if v.slot() == Slot::Fixed => Kb::exact(v.off()),
            Space::Const => Kb::of(slot_iv(v.slot(), b, v.size())),
            Space::Reg => reg[v.off() as usize],
            Space::Unique => uniq[v.off() as usize],
            Space::Ram => Kb::any(v.size()),
        };
        let r = match o.opc {
            Opc::Load => Kb::any(o.out.size()),
            Opc::Store => continue,
            opc => op(
                opc,
                get(o.ins[0], &reg, &uniq),
                if o.ins[1].is_none() {
                    Kb::any(1)
                } else {
                    get(o.ins[1], &reg, &uniq)
                },
                o.out.size(),
            ),
        };
        match o.out.space() {
            Space::Reg if !o.out.is_none() => {
                let i = o.out.off() as usize;
                reg[i] = r;
                if i < 3 {
                    out[i] = Some(r);
                }
            }
            Space::Unique if !o.out.is_none() => uniq[o.out.off() as usize] = r,
            _ => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::analysis::TOP_ENV;
    use di_pcode::{reg, Template};

    fn run(code: &[u8], env: Env, kb: [Kb; 3]) -> [Option<Kb>; 3] {
        let t = Template::of(code[0]);
        let b = [code.get(1).copied(), code.get(2).copied()];
        eval(t.ops(), b, &env, &kb)
    }

    #[test]
    fn asl_clears_bit_0_and_rol_of_zero_is_the_carry() {
        let mut env = TOP_ENV;
        let kb = [Kb::any(1); 3];
        let a = run(&[0x0a], env, kb)[reg::A].unwrap();
        assert_eq!((a.v, a.u), (0, 0xfe));
        env[reg::A] = Iv::exact(0);
        let a = run(&[0x2a], env, kb)[reg::A].unwrap();
        assert_eq!((a.v, a.u), (0, 1));
        env[reg::A] = Iv { lo: 0, hi: 1 };
        let a = run(&[0x0a], env, [a, kb[1], kb[2]])[reg::A].unwrap();
        assert_eq!((a.v, a.u), (0, 2));
        assert_eq!(a.values(Iv { lo: 0, hi: 2 }).collect::<Vec<_>>(), [0, 2]);
    }

    #[test]
    fn joins_keep_shared_bits_and_arithmetic_carries_unknowns() {
        let j = Kb::exact(0).join(Kb::exact(2));
        assert_eq!(j.values(Iv::full(1)).collect::<Vec<_>>(), [0, 2]);
        assert_eq!(Kb::of(Iv { lo: 0x10, hi: 0x13 }), Kb { v: 0x10, u: 3 });
        let s = op(Opc::IntAdd, Kb { v: 0, u: 2 }, Kb::exact(4), 1);
        assert_eq!(s.values(Iv::full(1)).collect::<Vec<_>>(), [4, 6]);
        let d = op(Opc::IntSub, Kb { v: 4, u: 2 }, Kb::exact(4), 1);
        assert_eq!(d.values(Iv::full(1)).collect::<Vec<_>>(), [0, 2]);
        let x = op(Opc::IntXor, Kb { v: 1, u: 2 }, Kb::exact(3), 1);
        assert_eq!(x.values(Iv::full(1)).collect::<Vec<_>>(), [0, 2]);
        let o = op(Opc::IntOr, Kb { v: 0, u: 3 }, Kb::exact(0x80), 1);
        assert_eq!(
            o.values(Iv::full(1)).collect::<Vec<_>>(),
            [0x80, 0x81, 0x82, 0x83]
        );
        let r = op(Opc::IntRight, Kb { v: 4, u: 2 }, Kb::exact(1), 1);
        assert_eq!(r.values(Iv::full(1)).collect::<Vec<_>>(), [2, 3]);
        assert_eq!(Kb::exact(5).meet(Kb { v: 0, u: 0xff }), Kb::exact(5));
    }
}
