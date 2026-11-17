//! Device models: what a read of a volatile address answers, from the values the program's
//! writes leave in its registers (`deity_informant.devices`, lowered).
//!
//! An address the model describes is a register at a canonical address (its mirrors share
//! it). Bits `latch` of a read are the value last written, `ones` read 1, `live` any; a
//! register with sets answers the set of the value bits `cmask` of register `ctrl` hold (one
//! set without `ctrl`). The analysis tracks the values written to the registers reads depend
//! on as bytes at their canonical addresses; an address the model does not describe answers
//! any byte.

use crate::analysis::Iv;
use crate::vsa::Bits;
use rustc_hash::FxHashMap;

/// One register ([module documentation](self)).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Reg {
    pub latch: u8,
    pub ones: u8,
    pub live: u8,
    pub ctrl: Option<u16>,
    pub cmask: u8,
    pub sets: Vec<Bits>,
}

/// A machine's described registers by canonical address and the canonical address of each
/// described volatile address.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Devices {
    pub regs: FxHashMap<u16, Reg>,
    pub canon: FxHashMap<u16, u16>,
}

/// The values of the bits of `mask`, each with the others clear.
fn subs(mask: u8) -> impl Iterator<Item = u8> {
    let mut x = 0u16;
    let mut done = false;
    std::iter::from_fn(move || {
        if done {
            return None;
        }
        let v = x as u8;
        x = ((x | !mask as u16) + 1) & mask as u16;
        done = x == 0;
        Some(v)
    })
}

/// The bits of `v` that `mask` selects, packed from bit 0 up: the index of `v & mask` among
/// the values of `mask`'s bits, ascending.
fn pext(v: u8, mask: u8) -> usize {
    (0..8)
        .filter(|&b| mask >> b & 1 == 1)
        .enumerate()
        .fold(0, |out, (i, b)| out | ((v >> b & 1) as usize) << i)
}

impl Devices {
    /// The canonical address of described address `a`.
    pub fn canon(&self, a: u16) -> Option<u16> {
        self.canon.get(&a).copied()
    }
    /// The canonical registers whose written values reads depend on.
    pub fn held(&self) -> impl Iterator<Item = u16> + '_ {
        self.regs
            .iter()
            .filter(|(_, r)| r.latch != 0)
            .map(|(&c, _)| c)
            .chain(self.regs.values().filter_map(|r| r.ctrl))
    }
    /// The values a read of `a` answers, `val(c)` the interval of the value written to
    /// canonical register `c` where known; none where `a` is not described.
    pub fn read(&self, a: u16, val: impl Fn(u16) -> Option<Iv>) -> Option<Bits> {
        let c = self.canon(a)?;
        let r = &self.regs[&c];
        let full = Iv { lo: 0, hi: 0xff };
        let mut out = Bits::default();
        if !r.sets.is_empty() {
            let held = r.ctrl.and_then(&val).filter(|_| r.sets.len() > 1);
            match held {
                Some(iv) => (iv.lo..=iv.hi.min(0xff))
                    .for_each(|v| out = out.or(r.sets[pext(v as u8, r.cmask)])),
                None => r.sets.iter().for_each(|&b| out = out.or(b)),
            }
            return Some(out);
        }
        let w = if r.latch != 0 { val(c) } else { None };
        let w = w.unwrap_or(full);
        let bases: Bits = (w.lo..=w.hi.min(0xff))
            .map(|v| v & r.latch as u32)
            .collect();
        for b in bases.values() {
            for x in subs(r.live) {
                out.add(b | r.ones as u32 | x as u32);
            }
        }
        Some(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bits(vs: impl IntoIterator<Item = u32>) -> Bits {
        vs.into_iter().collect()
    }

    #[test]
    fn subsets_of_a_mask() {
        assert_eq!(subs(0).collect::<Vec<_>>(), vec![0]);
        assert_eq!(subs(0x81).collect::<Vec<_>>(), vec![0, 1, 0x80, 0x81]);
        assert_eq!(subs(0xff).count(), 256);
        for (i, v) in subs(0xfe).enumerate() {
            assert_eq!(pext(v | 1, 0xfe), i);
        }
    }

    #[test]
    fn reads() {
        let mut d = Devices::default();
        let latch = Reg {
            latch: 0x0f,
            ones: 0xf0,
            ..Reg::default()
        };
        let osc = Reg {
            ctrl: Some(0x12),
            cmask: 0x08,
            sets: vec![bits(0..256), bits([0])],
            ..Reg::default()
        };
        d.regs.insert(0x20, latch);
        d.regs.insert(0x1b, osc);
        d.regs.insert(0x12, Reg::default());
        d.canon
            .extend([(0x20, 0x20), (0x60, 0x20), (0x1b, 0x1b), (0x12, 0x12)]);
        let none = |_| None;
        let five = |c| (c == 0x20).then_some(Iv::exact(5));
        assert_eq!(d.read(0x60, five), Some(bits([0xf5])));
        assert_eq!(d.read(0x20, none), Some(bits(0xf0..0x100)));
        assert_eq!(d.read(0x30, none), None);
        assert_eq!(d.read(0x1b, |_| Some(Iv::exact(0x28))), Some(bits([0])));
        assert_eq!(d.read(0x1b, none), Some(bits(0..256)));
        assert_eq!(d.read(0x12, none), Some(bits([0])));
        let mut held: Vec<u16> = d.held().collect();
        held.sort_unstable();
        assert_eq!(held, vec![0x12, 0x20]);
    }
}
