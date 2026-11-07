//! Pointer pairs as 16-bit values in [`crate::vsa`]'s reach pass.
//!
//! A pair of tracked bytes `q, q + 1` holds `lo..=hi`, integers that do not wrap: a base plus
//! a non-negative offset, `hi` up to [`MAX`]. A pointer advanced by `ADC lo; BCC; INC hi`
//! (or `ADC lo; ...; ADC #0` into its high byte) keeps its base's lower bound, where its high
//! byte alone wraps: a value past `$FFFF` is a pointer walked past the top of memory, which
//! a reader of a held span treats as out of bounds. Between the two stores of such an
//! advance the pair owes its high byte the carry: it holds 256 less while the carry flag is
//! set (`Owe::C`), 256 less once a branch found it set (`Owe::One`), either (`Owe::Either`)
//! once another instruction wrote the flag.
//!
//! A byte moved through a register keeps what it is of a 16-bit quantity ([`Half`]): the byte
//! at a tracked address as it stands, or a part of the sum an `ADC` made; a pair whose two
//! bytes were stored from the two parts of one quantity holds that quantity's value.
//!
//! A pair no instruction since the entry stored (no entry in `St::wide`) holds its bytes'
//! hull and at least its floor (`Code::floor`): a lower bound that holds whenever an entry
//! other than the init entry is called, as it holds of every pair the init entry's returns
//! leave, of every pair any other entry's return leaves, and (without an init entry) of the
//! image: a least fixpoint over the rounds of the reach pass, from floors that hold no value.

use crate::analysis::{Access, Iv};
use crate::vsa::{Code, Ix, St, Sym, Tab, DATA, MEM, NOREG};
use di_pcode::reg;

/// The greatest value a pair may be taken to hold: unbounded above.
pub(crate) const MAX: u32 = 0x00ff_ffff;
/// The floor of a pair no run has given a value yet: past [`MAX`], so it holds none.
pub(crate) const NONE: u32 = 0x7fff_ffff;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Owe {
    No,
    C,
    One,
    Either,
}

/// A 16-bit quantity: the value of pair `q` as it stands, or the sum the `ADC` at `pc` made.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Id {
    Pair(u16),
    Sum(u16),
}

/// A pair's value `lo..=hi` less what it owes its high byte.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) struct Wv {
    pub(crate) lo: u32,
    pub(crate) hi: u32,
    pub(crate) owe: Owe,
    pub(crate) id: Id,
}

impl Wv {
    /// The values the pair holds, None when it holds none (a floor no run has met yet).
    pub(crate) fn range(self) -> Option<(u32, u32)> {
        let (dl, dh) = match self.owe {
            Owe::No => (0, 0),
            Owe::C | Owe::Either => (256, 0),
            Owe::One => (256, 256),
        };
        (self.lo <= self.hi).then(|| (self.lo.saturating_sub(dl), self.hi.saturating_sub(dh)))
    }
    fn plain(lo: u32, hi: u32, q: u16) -> Wv {
        Wv {
            lo,
            hi,
            owe: Owe::No,
            id: Id::Pair(q),
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Part {
    Lo,
    Hi,
    /// The high byte less the carry flag.
    HiC,
}

/// What a byte holds of a 16-bit quantity: the byte at a tracked address as it stands, or a
/// part of the sum `lo..=hi` the `ADC` at `pc` made from pair `src` (while its bytes stand).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub(crate) enum Half {
    Byte(u16),
    /// A byte read through a pointer pair holding `lo..=hi`: a byte of a pointer into what
    /// the pair points into.
    Data(u32, u32),
    Sum {
        pc: u16,
        src: Option<u16>,
        lo: u32,
        hi: u32,
        part: Part,
    },
}

impl Half {
    fn part(self) -> Option<Part> {
        match self {
            Half::Sum { part, .. } => Some(part),
            _ => None,
        }
    }
    /// The sum and its values when this is part `want` of the sum the `ADC` at some `pc`
    /// made. Every execution of that `ADC` forgets the parts of the sum the last one made,
    /// so parts of the sums of one `pc` are parts of one value.
    fn sum(self, want: Part) -> Option<(u16, u32, u32)> {
        match self {
            Half::Sum {
                pc, lo, hi, part, ..
            } if part == want => Some((pc, lo, hi)),
            _ => None,
        }
    }
    /// What holds of a byte holding `self` on one path and `o` on another.
    fn join(self, o: Half) -> Option<Half> {
        match (self, o) {
            _ if self == o => Some(self),
            (Half::Data(a, b), Half::Data(c, d)) => Some(Half::Data(a.min(c), b.max(d))),
            (
                Half::Sum {
                    pc,
                    src,
                    lo,
                    hi,
                    part,
                },
                Half::Sum {
                    pc: p2,
                    src: s2,
                    lo: l2,
                    hi: h2,
                    part: q2,
                },
            ) if (pc, part) == (p2, q2) => Some(Half::Sum {
                pc,
                src: if src == s2 { src } else { None },
                lo: lo.min(l2),
                hi: hi.max(h2),
                part,
            }),
            _ => None,
        }
    }
}

/// The value of a sum both of whose parts give it `(lo, hi)` and `(l2, h2)`.
fn both(pc: u16, (lo, hi): (u32, u32), (l2, h2): (u32, u32)) -> Wv {
    Wv {
        lo: lo.max(l2),
        hi: hi.min(h2).max(lo.max(l2)),
        owe: Owe::No,
        id: Id::Sum(pc),
    }
}

fn tracked(code: &Code, a: u16) -> bool {
    code.vecs.binary_search(&a).is_ok()
}

/// Whether `q, q + 1` are tracked bytes.
pub(crate) fn pair(code: &Code, q: u16) -> bool {
    q != 0xffff && tracked(code, q) && tracked(code, q + 1)
}

/// The value pair `q` was given since the entry, where it was given one.
fn explicit(st: &St, q: u16) -> Option<Wv> {
    st.wide
        .binary_search_by_key(&q, |e| e.0)
        .ok()
        .and_then(|i| st.wide[i].1)
}

/// What pair `q` holds since the entry: a value, or (`Some(None)`) its bytes' hull.
fn touched(st: &St, q: u16) -> Option<Option<Wv>> {
    st.wide
        .binary_search_by_key(&q, |e| e.0)
        .ok()
        .map(|i| st.wide[i].1)
}

fn put(s: &mut St, q: u16, w: Option<Wv>) {
    match s.wide.binary_search_by_key(&q, |e| e.0) {
        Ok(i) => s.wide[i].1 = w,
        Err(i) => s.wide.insert(i, (q, w)),
    }
}

fn byte(code: &Code, st: &St, a: u16) -> Iv {
    st.mem
        .iter()
        .find(|e| e.0 == a)
        .map(|e| e.2)
        .or_else(|| code.byte(a as u32, st))
        .unwrap_or(Iv::full(1))
}

/// The pair's value from its bytes' intervals, or from the words of the tables they hold
/// ([`tabled`]).
fn derived(code: &Code, st: &St, q: u16) -> Wv {
    tabled(code, st, q).unwrap_or_else(|| {
        let (l, h) = (byte(code, st, q), byte(code, st, q.wrapping_add(1)));
        Wv::plain(h.lo << 8 | l.lo, h.hi << 8 | l.hi, q)
    })
}

/// The hull of the words pair `q` holds when its two bytes hold table bytes indexed by one
/// value (one index register, still its value or one group): per index value both loads
/// read (`Code::clip`, the register's values when it still holds the index), the word of
/// the two bytes, each holding its entry byte alone.
fn tabled(code: &Code, st: &St, q: u16) -> Option<Wv> {
    let tag = |a: u16| st.mem.iter().find(|e| e.0 == a)?.1;
    let (
        Tab::T {
            pc: p0,
            op: o0,
            reg: r,
            ix,
        },
        Tab::T {
            pc: p1,
            op: o1,
            reg: r1,
            ix: ix1,
        },
    ) = (tag(q)?, tag(q.wrapping_add(1))?)
    else {
        return None;
    };
    if r == NOREG || (r, ix) != (r1, ix1) || ix == Ix::Lost {
        return None;
    }
    let b0 = crate::dispatch::load_addr(code, p0, o0, r, 0)?;
    let b1 = crate::dispatch::load_addr(code, p1, o1, r, 0)?;
    // Columns only the two halves of stored pointers write hold those pointers: a load reads
    // what a store left.
    if let Some(&(lo, hi)) = code.words.get(&(b0, b1)) {
        return Some(Wv::plain(lo, hi, q));
    }
    let (c0, c1) = (code.clip.get(&p0)?, code.clip.get(&p1)?);
    // A pointer whose bytes are read from one run of data bytes points into it.
    let mut read = c0.iter().chain(c1).map(|&a| code.run_of(a as u32));
    let one = read
        .next()
        .flatten()
        .filter(|r| read.all(|x| x == Some(*r)));
    if let Some(run) = one.filter(|_| {
        let k = |a: &u16| code.byte(*a as u32, st).and_then(Iv::value).is_some();
        !c0.iter().chain(c1).all(k)
    }) {
        return Some(Wv::plain(run.0, run.1 - 1, q));
    }
    let live = (ix == Ix::Live).then(|| st.vals(r as usize));
    let (mut lo, mut hi) = (u32::MAX, 0);
    for x in (0..=0xff_u8).filter(|&x| live.is_none_or(|v| v.has(x as u32))) {
        let a0 = crate::dispatch::load_addr(code, p0, o0, r, x)?;
        let a1 = crate::dispatch::load_addr(code, p1, o1, r, x)?;
        if c0.binary_search(&a0).is_err() || c1.binary_search(&a1).is_err() {
            continue;
        }
        let at = |a: u16| code.byte(a as u32, st).and_then(Iv::value);
        let w = at(a1)? << 8 | at(a0)?;
        (lo, hi) = (lo.min(w), hi.max(w));
    }
    (lo <= hi).then(|| Wv::plain(lo, hi, q))
}

/// The value of pair `q` no instruction since the entry stored: its bytes' hull from its floor.
fn untouched(code: &Code, st: &St, q: u16) -> Wv {
    let d = derived(code, st, q);
    // Where the pass bounds pointers into the program's data, a pair the init entry's run
    // has not stored holds its image word (init runs first, on the image), joined with what
    // the other entries find.
    if let (false, Some(&l), true) = (code.runs.is_empty(), code.floor.get(&q), code.init != 0) {
        let w = code.img[q as usize] as u32 | (code.img[q.wrapping_add(1) as usize] as u32) << 8;
        let init = (st.from & code.init != 0).then_some((w, w));
        let other = (st.from & !code.init != 0).then_some((d.lo.max(l), d.hi));
        let (lo, hi) = match (init, other) {
            (Some(a), Some(b)) if b.0 <= b.1 => (a.0.min(b.0), a.1.max(b.1)),
            (Some(a), _) => a,
            (None, Some(b)) => b,
            (None, None) => (NONE, 0),
        };
        return Wv { lo, hi, ..d };
    }
    match code.floor.get(&q).filter(|_| st.from & code.init == 0) {
        Some(&l) => Wv {
            lo: d.lo.max(l),
            ..d
        },
        None => d,
    }
}

fn holds(code: &Code, st: &St, q: u16, t: Option<Option<Wv>>) -> Wv {
    match t {
        Some(Some(w)) => w,
        Some(None) => derived(code, st, q),
        None => untouched(code, st, q),
    }
}

/// The hull of a word column no store has written yet: none.
pub(crate) const EMPTY: (u32, u32) = (NONE, 0);

/// What a byte holding `h` in `st` may be of a pointer: its low or high byte, the pointer
/// identified by the sum or pair it comes from, and the pointer's values.
pub(crate) type Whole = Vec<(bool, Id, (u32, u32))>;

/// [`Whole`] of a byte holding `h` in `st`.
pub(crate) fn whole(code: &Code, st: &St, h: Half) -> Whole {
    let of = |q: u16| value(code, st, q).range();
    match h {
        Half::Sum {
            pc, lo, hi, part, ..
        } if part != Part::HiC => {
            vec![(part == Part::Hi, Id::Sum(pc), (lo, hi))]
        }
        Half::Byte(m) => [(false, m), (true, m.wrapping_sub(1))]
            .into_iter()
            .filter(|&(_, q)| pair(code, q))
            .filter_map(|(hi, q)| Some((hi, Id::Pair(q), of(q)?)))
            .collect(),
        _ => Vec::new(),
    }
}

/// Word columns ([`crate::vsa::Code::words`]) of the stores `halves` (instruction, table
/// base, what the stored byte is of a pointer): two stores at one index (`same`) of the low
/// and high bytes of one pointer pair their bases, when every store into the run of data
/// bytes holding either base is such a store and no other store writes a byte of it
/// (`other`). The hull of the pointers the pairs store.
pub(crate) fn columns(
    code: &Code,
    halves: &[(u16, u16, Whole)],
    other: &fixedbitset::FixedBitSet,
    same: impl Fn(u16, u16) -> bool,
) -> rustc_hash::FxHashMap<(u16, u16), (u32, u32)> {
    let mut out: rustc_hash::FxHashMap<(u16, u16), (u32, u32)> = Default::default();
    let mut paired = vec![false; halves.len()];
    for (i, (p0, b0, w0)) in halves.iter().enumerate() {
        for &(_, id, (lo, hi)) in w0.iter().filter(|w| !w.0) {
            for (j, (p1, b1, w1)) in halves.iter().enumerate() {
                if w1.iter().any(|w| w.0 && w.1 == id) && same(*p0, *p1) {
                    let e = out.entry((*b0, *b1)).or_insert(EMPTY);
                    *e = (e.0.min(lo), e.1.max(hi));
                    paired[i] = true;
                    paired[j] = true;
                }
            }
        }
    }
    let run = |b: u16| code.run_of(b as u32);
    let clean = |r: Option<(u32, u32)>| {
        r.is_some_and(|(lo, hi)| {
            (lo..hi).all(|x| !other.contains(x as usize))
                && halves.iter().zip(&paired).all(|(h, &p)| p || run(h.0) != r)
        })
    };
    out.retain(|&(b0, b1), _| clean(run(b0)) && clean(run(b1)));
    out
}

/// The value pair `q` holds in `st`.
pub(crate) fn value(code: &Code, st: &St, q: u16) -> Wv {
    holds(code, st, q, touched(st, q))
}

/// The addresses pair `q` plus an index in `idx` reaches in `st` (unwrapped), None when it
/// holds no value.
pub(crate) fn reach(code: &Code, st: &St, q: u16, idx: Iv) -> Option<Iv> {
    let (lo, hi) = value(code, st, q).range()?;
    Some(Iv {
        lo: lo + idx.lo,
        hi: (hi + idx.hi).min(MAX),
    })
}

fn drop_where(s: &mut St, f: impl Fn(&Half) -> bool) {
    for h in s.half.iter_mut() {
        if h.as_ref().is_some_and(&f) {
            *h = None;
        }
    }
    s.mhalf.retain(|e| !f(&e.1));
}

fn map_halves(s: &mut St, f: impl Fn(Half) -> Option<Half>) {
    for h in s.half.iter_mut() {
        *h = h.and_then(&f);
    }
    s.mhalf = s
        .mhalf
        .iter()
        .filter_map(|&(a, h)| Some((a, f(h)?)))
        .collect();
}

/// Byte `x` changes: what stood for it no longer does.
fn forget(s: &mut St, x: u16) {
    drop_where(s, |h| *h == Half::Byte(x));
    s.mhalf.retain(|e| e.0 != x);
    map_halves(s, |h| match h {
        Half::Sum {
            src: Some(q),
            pc,
            lo,
            hi,
            part,
        } if q == x || q.wrapping_add(1) == x => Some(Half::Sum {
            src: None,
            pc,
            lo,
            hi,
            part,
        }),
        h => Some(h),
    });
}

fn half_at(s: &St, a: u16) -> Option<Half> {
    s.mhalf
        .binary_search_by_key(&a, |e| e.0)
        .ok()
        .map(|i| s.mhalf[i].1)
}

/// What register `r` holds after loading byte `m` of `st`.
fn load_half(st: &St, m: u16) -> Half {
    let sum = |q: u16, part: Part| match explicit(st, q) {
        Some(Wv {
            lo,
            hi,
            id: Id::Sum(pc),
            ..
        }) => Some(Half::Sum {
            pc,
            src: None,
            lo,
            hi,
            part,
        }),
        _ => None,
    };
    let hi = match explicit(st, m.wrapping_sub(1)).map(|w| w.owe) {
        Some(Owe::No) => sum(m.wrapping_sub(1), Part::Hi),
        Some(Owe::C) => sum(m.wrapping_sub(1), Part::HiC),
        _ => None,
    };
    hi.or_else(|| sum(m, Part::Lo)).unwrap_or(Half::Byte(m))
}

/// What a byte read through a pointer pair holds: a byte of a pointer into what the pair
/// points into, its values.
fn data(code: &Code, st: &St, acc: &[Access]) -> Option<Half> {
    let p = acc.iter().rfind(|a| !a.store && !a.dummy)?.ptr?;
    let q = p.lo?.value()?;
    if p.hi.value()? != q + 1 || !pair(code, q as u16) {
        return None;
    }
    // A pair holding no value yet gives none.
    let w = value(code, st, q as u16);
    let (lo, hi) = w.range().unwrap_or((w.lo, w.hi));
    Some(Half::Data(lo, hi))
}

/// The sum `ADC` at `pc` makes of pair `q`'s value and `v`.
fn sum(code: &Code, st: &St, pc: u16, q: u16, v: Iv) -> Option<Half> {
    let w = value(code, st, q);
    let (lo, hi) = w.range().unwrap_or((w.lo, w.hi));
    Some(Half::Sum {
        pc,
        src: Some(q),
        lo: lo + v.lo,
        hi: (hi + v.hi).min(MAX),
        part: Part::Lo,
    })
}

/// The pair updates of the instruction `op` at `pc` (template `ops`, operand bytes `b`, its
/// accesses `acc` and the symbols and written mask of its registers) from `st` to `s`.
#[allow(clippy::too_many_arguments)]
pub(crate) fn step(
    code: &Code,
    pc: u16,
    op: u8,
    ops: &[di_pcode::Op],
    b: [Option<u8>; 2],
    st: &St,
    s: &mut St,
    acc: &[Access],
    sym: &[Option<Sym>; 16],
    written: u32,
) {
    if written >> reg::C & 1 == 1 {
        for w in s.wide.iter_mut().flat_map(|e| e.1.as_mut()) {
            if w.owe == Owe::C {
                w.owe = Owe::Either;
            }
        }
        drop_where(s, |h| h.part() == Some(Part::HiC));
    }
    // The byte the instruction loads: its last load (a pointer's bytes load before it).
    let ld = acc
        .iter()
        .rfind(|a| !a.store && !a.dummy)
        .and_then(|a| a.addr.value())
        .map(|a| a as u16);
    let adc = matches!(op, 0x61 | 0x65 | 0x69 | 0x6d | 0x71 | 0x75 | 0x79 | 0x7d)
        && st.env[reg::C].value() == Some(0)
        && st.env[reg::D].value() == Some(0);
    // `ADC #0` adds the carry a byte holding a sum's high byte less it owes: whatever the
    // carry, the sum's high byte.
    let owed = match st.half[0] {
        Some(Half::Sum {
            pc,
            src,
            lo,
            hi,
            part: Part::HiC,
        }) if op == 0x69 && b[0] == Some(0) && st.env[reg::D].value() == Some(0) => {
            Some(Half::Sum {
                pc,
                src,
                lo,
                hi,
                part: Part::Hi,
            })
        }
        _ => None,
    };
    let mut new = s.half;
    for r in (0..DATA).filter(|&r| written >> r & 1 == 1) {
        new[r] = match sym[r] {
            _ if owed.is_some() && r == 0 => owed,
            _ if adc && r == 0 => {
                let operand = match op {
                    0x69 => Iv::exact(b[0].unwrap_or(0) as u32),
                    _ => ld.map_or(Iv::full(1), |m| byte(code, st, m)),
                };
                let a_pair = matches!(st.half[0], Some(Half::Byte(q)) if pair(code, q));
                match (st.half[0], ld.filter(|&m| pair(code, m))) {
                    (Some(Half::Byte(q)), None) if a_pair => sum(code, st, pc, q, operand),
                    (_, Some(q)) if !a_pair => sum(code, st, pc, q, st.env[0]),
                    _ => None,
                }
            }
            Some(Sym::Lin(b0, 0)) if (b0 as usize) < DATA => st.half[b0 as usize],
            Some(Sym::Lin(b0, 0)) if b0 >= DATA as u8 && b0 != MEM => ld
                .map(|m| match half_at(st, m) {
                    Some(
                        h @ Half::Sum {
                            part: Part::HiC, ..
                        },
                    ) => h,
                    _ => load_half(st, m),
                })
                .or_else(|| data(code, st, acc)),
            _ => None,
        };
    }
    if let Some(Half::Sum {
        pc: p,
        part: Part::Lo,
        ..
    }) = new[0].filter(|_| adc)
    {
        // A sum an earlier execution of this `ADC` made is another quantity.
        let old = |h: &Half| matches!(h, Half::Sum { pc, .. } if *pc == p);
        drop_where(s, old);
        for h in new[1..].iter_mut().filter(|h| h.as_ref().is_some_and(old)) {
            *h = None;
        }
        for (q, w) in s.wide.iter_mut().filter_map(|e| Some((e.0, e.1.as_mut()?))) {
            if w.id == Id::Sum(p) {
                w.id = Id::Pair(q);
            }
        }
        // While the carry flag holds the sum's carry, the source's high byte is the sum's
        // high byte less it.
        if let Some(Half::Sum {
            src: Some(q),
            lo,
            hi,
            ..
        }) = new[0]
        {
            let x = q.wrapping_add(1);
            let h = Half::Sum {
                pc: p,
                src: Some(q),
                lo,
                hi,
                part: Part::HiC,
            };
            match s.mhalf.binary_search_by_key(&x, |e| e.0) {
                Ok(i) => s.mhalf[i].1 = h,
                Err(i) => s.mhalf.insert(i, (x, h)),
            }
        }
    }
    s.half = new;
    for a in acc.iter().filter(|a| a.store && !a.back) {
        match a.addr.value() {
            Some(x) => store(code, st, s, ops, b, x as u16),
            None => {
                // A pointer store whose sum may wrap reaches the low addresses it wraps to.
                let reach = a.ptr.and_then(|p| st.reach(p));
                let hit = |x: u16| match reach {
                    Some(r) => {
                        let m = x as u32;
                        (r.lo..=r.hi).contains(&m) || (r.lo..=r.hi).contains(&(m + 0x10000))
                    }
                    None => a.addr.meets(x as u32, x as u32 + 1),
                };
                let mut qs: Vec<u16> = code
                    .floor
                    .keys()
                    .copied()
                    .chain(s.wide.iter().map(|e| e.0))
                    .filter(|&q| hit(q) || hit(q.wrapping_add(1)))
                    .collect();
                qs.sort_unstable();
                qs.dedup();
                let named = |h: &Half| match *h {
                    Half::Byte(x) => vec![x],
                    Half::Sum { src: Some(q), .. } => vec![q, q.wrapping_add(1)],
                    _ => vec![],
                };
                let mut bytes: Vec<u16> = s
                    .mhalf
                    .iter()
                    .flat_map(|e| std::iter::once(e.0).chain(named(&e.1)))
                    .chain(s.half.iter().flatten().flat_map(named))
                    .chain(qs.iter().flat_map(|&q| [q, q.wrapping_add(1)]))
                    .filter(|&x| hit(x))
                    .collect();
                bytes.sort_unstable();
                bytes.dedup();
                for x in bytes {
                    forget(s, x);
                }
                for q in qs {
                    put(s, q, None);
                }
            }
        }
    }
}

/// A store to byte `x`.
fn store(code: &Code, st: &St, s: &mut St, ops: &[di_pcode::Op], b: [Option<u8>; 2], x: u16) {
    let (_, _, held) = crate::vsa::symbolic_in(ops, b, (None, None, [None; DATA]), Some(x as u32));
    let (h, rmw) = match held {
        Some(Some(Sym::Lin(r, 0))) if (r as usize) < DATA => (st.half[r as usize], None),
        Some(Some(Sym::Lin(MEM, k))) => (None, Some(k)),
        _ => (None, None),
    };
    forget(s, x);
    let parts = |q: u16, lo: Option<Half>, hi: Option<Half>| match (
        lo.and_then(|h| h.sum(Part::Lo)),
        hi.and_then(|h| h.sum(Part::Hi)),
    ) {
        (Some((pc, lo, hi)), Some((p2, l2, h2))) if pc == p2 => Some(both(pc, (lo, hi), (l2, h2))),
        // A pointer whose bytes are read through a pointer points into what that points into.
        _ => match (lo, hi) {
            (Some(Half::Data(a, b)), Some(h)) if h == Half::Data(a, b) => Some(Wv::plain(a, b, q)),
            _ => None,
        },
    };
    if pair(code, x) {
        let next = half_at(s, x + 1);
        let w = match h {
            Some(Half::Sum {
                pc,
                src: Some(q),
                lo,
                hi,
                part: Part::Lo,
            }) if q == x => Some(Wv {
                lo,
                hi,
                owe: Owe::C,
                id: Id::Sum(pc),
            }),
            Some(Half::Byte(m)) if pair(code, m) && next == Some(Half::Byte(m.wrapping_add(1))) => {
                Some(Wv {
                    id: Id::Pair(x),
                    ..value(code, st, m)
                })
            }
            _ => parts(x, h, next),
        };
        put(s, x, w);
    }
    let q = x.wrapping_sub(1);
    if pair(code, q) {
        let low = explicit(s, q)
            .and_then(|w| match w.id {
                Id::Sum(pc) => Some(Half::Sum {
                    pc,
                    src: None,
                    lo: w.lo,
                    hi: w.hi,
                    part: Part::Lo,
                }),
                Id::Pair(_) => None,
            })
            .or_else(|| half_at(s, q));
        let w = match (h, rmw) {
            (_, Some(1)) => match value(code, st, q) {
                w @ Wv { owe: Owe::One, .. } => Some(Wv { owe: Owe::No, ..w }),
                w @ Wv { owe: Owe::No, .. } => Some(Wv {
                    lo: w.lo + 256,
                    hi: (w.hi + 256).min(MAX),
                    ..w
                }),
                _ => None,
            },
            (Some(Half::Byte(m)), None)
                if m != 0 && pair(code, m - 1) && half_at(s, q) == Some(Half::Byte(m - 1)) =>
            {
                Some(Wv {
                    id: Id::Pair(q),
                    ..value(code, st, m - 1)
                })
            }
            (_, None) => parts(q, low, h),
            _ => None,
        };
        put(s, q, w);
    }
    if let (Some(h), None) = (h, rmw) {
        let i = s.mhalf.partition_point(|e| e.0 < x);
        s.mhalf.insert(i, (x, h));
    }
}

/// `s` on the edge where the carry flag is `c`.
pub(crate) fn carry(s: &mut St, c: bool) {
    for w in s.wide.iter_mut().flat_map(|e| e.1.as_mut()) {
        if w.owe == Owe::C {
            w.owe = if c { Owe::One } else { Owe::No };
        }
    }
    map_halves(s, |h| match h {
        Half::Sum {
            part: Part::HiC,
            pc,
            src,
            lo,
            hi,
        } => (!c).then_some(Half::Sum {
            part: Part::Hi,
            pc,
            src,
            lo,
            hi,
        }),
        h => Some(h),
    });
}

/// Pair `q` holding `x` in `a` and `y` in `b` (`b` widened against `a` when `widen`): the
/// hull of what each holds, its bytes' hull where both hold that.
fn join1(
    code: &Code,
    (a, b): (&St, &St),
    q: u16,
    (x, y): (Option<Option<Wv>>, Option<Option<Wv>>),
    widen: bool,
) -> Option<Wv> {
    if (x, y) == (Some(None), Some(None)) {
        return None;
    }
    let (x, y) = (holds(code, a, q, x), holds(code, b, q, y));
    let mut w = if x == y {
        x
    } else if (x.id, x.owe) == (y.id, y.owe) {
        Wv {
            lo: x.lo.min(y.lo),
            hi: x.hi.max(y.hi),
            ..x
        }
    } else {
        match (x.range(), y.range()) {
            (Some(p), Some(r)) => Wv::plain(p.0.min(r.0), p.1.max(r.1), q),
            (Some(p), None) | (None, Some(p)) => Wv::plain(p.0, p.1, q),
            (None, None) => x,
        }
    };
    // Lower bounds fall only to values the pairs' bytes, floors and sums give, but an owed
    // carry taken as a range falls by up to 256 on each pass of a loop.
    if widen && w != x {
        if w.hi > x.hi {
            w.hi = MAX;
        }
        if w.lo < x.lo && (x.owe, y.owe) != (Owe::No, Owe::No) {
            w.lo = 0;
        }
    }
    Some(w)
}

/// The pairs of `a` and `b` joined (`b` widened against `a` when `widen`).
pub(crate) fn join(code: &Code, a: &St, b: &St, widen: bool) -> Vec<(u16, Option<Wv>)> {
    let (mut i, mut j) = (0, 0);
    let mut out = Vec::with_capacity(a.wide.len().max(b.wide.len()));
    while i < a.wide.len() || j < b.wide.len() {
        let (ka, kb) = (a.wide.get(i).map(|e| e.0), b.wide.get(j).map(|e| e.0));
        let q = ka.into_iter().chain(kb).min().unwrap();
        let x = (ka == Some(q)).then(|| a.wide[i].1);
        let y = (kb == Some(q)).then(|| b.wide[j].1);
        i += x.is_some() as usize;
        j += y.is_some() as usize;
        out.push((q, join1(code, (a, b), q, (x, y), widen)));
    }
    out
}

/// The halves `a` and `b` agree on.
pub(crate) fn join_halves(a: &St, b: &St) -> ([Option<Half>; DATA], Vec<(u16, Half)>) {
    let mut m = Vec::new();
    let (mut i, mut j) = (0, 0);
    while let (Some(&(x, h)), Some(&(y, g))) = (a.mhalf.get(i), b.mhalf.get(j)) {
        if x == y {
            m.extend(h.join(g).map(|h| (x, h)));
        }
        i += (x <= y) as usize;
        j += (y <= x) as usize;
    }
    (std::array::from_fn(|r| a.half[r]?.join(b.half[r]?)), m)
}
