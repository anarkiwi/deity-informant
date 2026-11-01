//! Emitting planned functions as P-Code blocks: templates with code cells read from memory,
//! opcode-cell dispatch, stack control as `CALL`/`RETURN`/`BRANCHIND`, cycle accounting in
//! the `CY` register, and guards that deopt when an assumption fails.

use crate::analysis::{is_stack_op, operand_bytes, Access, Entry, Graph, Iv, Tg};
use crate::func::{Ctl, Exit, Plan};
use crate::ir::{cy, k, mem, other, r1, reg, tmp, Block, Function, LOp, E};
use di_pcode::{reg as R, CtrlKind, Op, Opc, PenKind, Slot, Space, Template, Vn};
use fixedbitset::FixedBitSet;
use rustc_hash::FxHashMap;
use std::collections::VecDeque;

const NONE: Vn = Vn::NONE;
const PAGE1: Iv = Iv {
    lo: 0x100,
    hi: 0x1ff,
};

/// Program-wide facts the emitter consults.
pub struct Shared<'a> {
    pub img: &'a [u8; 0x10000],
    pub g: &'a Graph,
    pub volatile: &'a [(u32, u32)],
    /// Prefix counts of constant code bytes (`code - cells`).
    pub constcode: Vec<u32>,
    /// No access other than pushes and pulls reaches page 1.
    pub private: bool,
    /// Function index of each callee.
    pub fidx: FxHashMap<u16, u32>,
    /// Callees whose returns must check their return address (they may store into page 1).
    pub ret_guard: FixedBitSet,
}

impl Shared<'_> {
    fn volatile(&self, a: Iv) -> bool {
        self.volatile.iter().any(|&(lo, hi)| a.meets(lo, hi))
    }
    fn hits_code(&self, a: Iv) -> bool {
        let hi = (a.hi as usize).min(0xffff) + 1;
        self.constcode[hi] > self.constcode[a.lo as usize]
    }
}

/// Prefix counts over a bitset of 65536 bits.
pub fn prefix(b: &FixedBitSet) -> Vec<u32> {
    let mut p = vec![0u32; 0x10001];
    for i in 0..0x10000 {
        p[i + 1] = p[i] + b.contains(i) as u32;
    }
    p
}

struct Em<'a, 'b> {
    sh: &'a Shared<'b>,
    plan: &'a Plan,
    f: Function,
    blk: FxHashMap<u16, u32>,
    queue: VecDeque<u16>,
    cur: usize,
    next: u32,
    /// Cycles accounted but not yet added to `CY`: a constant and 4-byte values.
    pend: (u32, Vec<Vn>),
    /// Instruction variants and their template ops.
    insns: usize,
    raw: usize,
    guards: usize,
}

/// Emit one planned function; `(function, instruction variants, template ops, code guards)`.
pub fn emit(sh: &Shared, plan: &Plan, entry: Option<&Entry>) -> (Function, usize, usize, usize) {
    let mut em = Em {
        sh,
        plan,
        f: Function {
            pc: plan.pc,
            top: plan.top,
            ..Function::default()
        },
        blk: FxHashMap::default(),
        queue: VecDeque::new(),
        cur: 0,
        next: 0,
        pend: (0, Vec::new()),
        insns: 0,
        raw: 0,
        guards: 0,
    };
    em.new_block(plan.pc);
    for (i, vals) in entry.into_iter().flat_map(|e| &e.regs) {
        if let [v] = vals[..] {
            let bad = em.op(Opc::IntNotEqual, 1, r1(*i), k(v as u32, 1));
            em.side_exit(bad, |em| em.deopt(k(plan.pc as u32, 2)));
            em.emit(Opc::Copy, r1(*i), k(v as u32, 1), NONE, 0);
        }
    }
    let b = em.block(plan.pc);
    em.emit(Opc::Branch, NONE, NONE, NONE, b);
    while let Some(pc) = em.queue.pop_front() {
        em.cur = em.blk[&pc] as usize;
        em.next = 0;
        em.node(pc);
    }
    let Em {
        f,
        insns,
        raw,
        guards,
        ..
    } = em;
    (f, insns, raw, guards)
}

impl Em<'_, '_> {
    fn new_block(&mut self, pc: u16) -> u32 {
        self.f.blocks.push(Block {
            pc,
            ..Block::default()
        });
        (self.f.blocks.len() - 1) as u32
    }
    /// The block of the instruction at `pc`, queued for emission on first use; a deopt to
    /// `pc` if the plan does not reach it.
    fn block(&mut self, pc: u16) -> u32 {
        if let Some(&b) = self.blk.get(&pc) {
            return b;
        }
        let b = self.new_block(pc);
        self.blk.insert(pc, b);
        if self.plan.nodes.contains_key(&pc) {
            self.queue.push_back(pc);
        } else {
            let save = std::mem::replace(&mut self.cur, b as usize);
            self.deopt(k(pc as u32, 2));
            self.cur = save;
        }
        b
    }
    /// A temporary; numbering restarts per instruction start, as temporaries never outlive
    /// the blocks one instruction start emits.
    fn temp(&mut self, size: u8) -> Vn {
        self.next += 1;
        self.f.ntemps = self.f.ntemps.max(self.next);
        tmp((self.next - 1) as u16, size)
    }
    fn emit(&mut self, opc: Opc, out: Vn, a: Vn, b: Vn, aux: u32) {
        self.f.blocks[self.cur]
            .ops
            .push(LOp::new(opc, out, a, b, aux));
    }
    /// `opc(a, b)` into a fresh temporary of `size` bytes.
    fn op(&mut self, opc: Opc, size: u8, a: Vn, b: Vn) -> Vn {
        let t = self.temp(size);
        self.emit(opc, t, a, b, 0);
        t
    }
    fn zext(&mut self, v: Vn, size: u8) -> Vn {
        if v.space() == Space::Const {
            k(v.off(), size)
        } else {
            self.op(Opc::IntZext, size, v, NONE)
        }
    }
    fn load(&mut self, addr: Vn, iv: Iv, flags: u32) -> Vn {
        let flags = flags | self.vflag(iv);
        let t = self.temp(1);
        self.emit(Opc::Load, t, addr, NONE, flags);
        t
    }
    fn vflag(&mut self, iv: Iv) -> u32 {
        if self.sh.volatile(iv) {
            self.flush();
            mem::VOLATILE
        } else {
            0
        }
    }
    /// Add the pending cycles to `CY`.
    fn flush(&mut self) {
        let (mut c, dy) = std::mem::take(&mut self.pend);
        while c != 0 {
            let part = c.min(0xffff);
            self.emit(Opc::IntAdd, cy(), cy(), k(part, 4), 0);
            c -= part;
        }
        for v in dy {
            self.emit(Opc::IntAdd, cy(), cy(), v, 0);
        }
    }
    fn account(&mut self, cyc: u32, maxpen: u32) {
        self.pend.0 += cyc;
        self.f.blocks[self.cur].maxcyc += cyc + maxpen;
    }
    fn deopt(&mut self, pc: Vn) {
        self.emit(Opc::CallOther, NONE, pc, NONE, other::DEOPT);
    }
    /// Leave through a new block when `cond` holds (the block sees the pending cycles), and
    /// continue in a fresh block.
    fn side_exit(&mut self, cond: Vn, body: impl FnOnce(&mut Self)) {
        let pc = self.f.blocks[self.cur].pc;
        let (x, cont) = (self.new_block(pc), self.new_block(pc));
        self.emit(Opc::CBranch, NONE, cond, NONE, x);
        self.emit(Opc::Branch, NONE, NONE, NONE, cont);
        let pend = self.pend.clone();
        self.cur = x as usize;
        self.flush();
        body(self);
        self.pend = pend;
        self.cur = cont as usize;
    }
    fn table(&mut self, ts: &[u16]) -> u32 {
        let mut t: Vec<(u16, u32)> = ts
            .iter()
            .filter(|t| self.plan.nodes.contains_key(t))
            .map(|&t| (t, self.block(t)))
            .collect();
        t.sort_unstable();
        self.f.tables.push(t);
        (self.f.tables.len() - 1) as u32
    }
    fn goto(&mut self, t: &Tg, calc: impl FnOnce(&mut Self) -> Vn) {
        match t {
            Tg::At(a) => {
                let b = self.block(*a);
                self.emit(Opc::Branch, NONE, NONE, NONE, b);
            }
            Tg::Calc(ts) => {
                let v = calc(self);
                let tb = self.table(ts);
                self.emit(Opc::BranchInd, NONE, v, NONE, tb);
            }
        }
    }
    fn sp_addr(&mut self) -> Vn {
        let z = self.zext(r1(R::SP), 2);
        self.op(Opc::IntOr, 2, z, k(0x100, 2))
    }
    fn push(&mut self, v: Vn, chk: &mut Vec<Vn>) {
        let a = self.sp_addr();
        let flags = mem::STACK | self.vflag(PAGE1);
        self.emit(Opc::Store, NONE, a, v, flags);
        if self.sh.hits_code(PAGE1) {
            chk.push(self.op(Opc::CallOther, 1, a, v));
            self.f.blocks[self.cur].ops.last_mut().unwrap().aux = other::CODECHK;
        }
        self.emit(Opc::IntSub, r1(R::SP), r1(R::SP), k(1, 1), 0);
    }
    fn pull(&mut self) -> Vn {
        self.emit(Opc::IntAdd, r1(R::SP), r1(R::SP), k(1, 1), 0);
        let a = self.sp_addr();
        self.load(a, PAGE1, mem::STACK)
    }
    /// `hi << 8 | lo` as a 2-byte value.
    fn word(&mut self, lo: Vn, hi: Vn) -> Vn {
        let h = self.zext(hi, 2);
        let h = self.op(Opc::IntLeft, 2, h, k(8, 1));
        let l = self.zext(lo, 2);
        self.op(Opc::IntOr, 2, h, l)
    }
    /// Deopt to `pc` when any code check fired.
    fn check(&mut self, chk: Vec<Vn>, pc: Vn) {
        let Some(mut c) = chk.first().copied() else {
            return;
        };
        for &d in &chk[1..] {
            c = self.op(Opc::IntOr, 1, c, d);
        }
        self.guards += chk.len();
        self.side_exit(c, |em| em.deopt(pc));
    }

    fn node(&mut self, pc: u16) {
        let (plan, sh) = (self.plan, self.sh);
        let node = &plan.nodes[&pc];
        let vars = &sh.g.vars[&pc];
        if !sh.g.cells.contains(pc as usize) {
            let (ctl, exit) = &node.ctl[0];
            self.insn(pc, &vars[0].op, &vars[0].acc, ctl, *exit);
            return;
        }
        let v = self.load(k(pc as u32, 2), Iv::exact(pc as u32), 0);
        for (var, (ctl, exit)) in vars.iter().zip(&node.ctl) {
            let c = self.op(Opc::IntEqual, 1, v, k(var.op as u32, 1));
            let vb = self.new_block(pc);
            let cont = self.new_block(pc);
            self.emit(Opc::CBranch, NONE, c, NONE, vb);
            self.emit(Opc::Branch, NONE, NONE, NONE, cont);
            self.cur = vb as usize;
            self.insn(pc, &var.op, &var.acc, ctl, *exit);
            self.cur = cont as usize;
        }
        self.deopt(k(pc as u32, 2));
    }

    /// Instantiate template `ops` with operand values from `slot`, temporaries renamed and
    /// accesses flagged; the code checks of its stores.
    fn ops(
        &mut self,
        ops: &[Op],
        acc: &[Access],
        stack: bool,
        slot: &mut dyn FnMut(&mut Self, Slot, u8) -> Vn,
        umap: &mut FxHashMap<u32, Vn>,
    ) -> Vec<Vn> {
        let mut chk = Vec::new();
        let mut ai = 0;
        for op in ops {
            let mut map = |em: &mut Self, v: Vn| -> Vn {
                match v.space() {
                    _ if v.is_none() => NONE,
                    Space::Const if v.slot() != Slot::Fixed => slot(em, v.slot(), v.size() as u8),
                    Space::Unique => *umap
                        .entry(v.off())
                        .or_insert_with(|| em.temp(v.size() as u8)),
                    _ => v.canon(),
                }
            };
            let a = map(self, op.ins[0]);
            let b = map(self, op.ins[1]);
            let out = map(self, op.out);
            let mut aux = 0;
            if matches!(op.opc, Opc::Load | Opc::Store) {
                let ac = acc.get(ai).copied().unwrap_or(Access {
                    store: op.opc == Opc::Store,
                    addr: Iv::full(2),
                    val: Iv::full(1),
                });
                ai += 1;
                aux = self.vflag(ac.addr) | if stack { mem::STACK } else { 0 };
                if op.opc == Opc::Store && self.sh.hits_code(ac.addr) {
                    self.emit(Opc::Store, out, a, b, aux);
                    chk.push(self.op(Opc::CallOther, 1, a, b));
                    self.f.blocks[self.cur].ops.last_mut().unwrap().aux = other::CODECHK;
                    continue;
                }
            }
            self.emit(op.opc, out, a, b, aux);
        }
        chk
    }

    fn insn(&mut self, pc: u16, op: &u8, acc: &[Access], ctl: &Ctl, exit: Exit) {
        let t = Template::of(*op);
        self.insns += 1;
        self.raw += t.n as usize;
        let next = pc.wrapping_add(t.len as u16);
        let bytes = operand_bytes(self.sh.img, &self.sh.g.cells, pc, t.len);
        // Operand bytes that are cells are read once, before the instruction's ops.
        let ob: [Vn; 2] = std::array::from_fn(|i| match bytes[i] {
            Some(b) => k(b as u32, 1),
            None if (t.len as usize) > i + 1 => {
                let a = pc.wrapping_add(i as u16 + 1);
                self.load(k(a as u32, 2), Iv::exact(a as u32), 0)
            }
            None => k(0, 1),
        });
        let mut memo: FxHashMap<(Slot, u8), Vn> = FxHashMap::default();
        let mut slot = move |em: &mut Self, s: Slot, size: u8| -> Vn {
            if let Some(&v) = memo.get(&(s, size)) {
                return v;
            }
            let v = match s {
                Slot::B1 => em.zext(ob[0], size),
                Slot::Word => {
                    let w = em.word(ob[0], ob[1]);
                    em.zext(w, size)
                }
                Slot::B1Inc | Slot::B2Inc => {
                    let b = ob[(s == Slot::B2Inc) as usize];
                    let v = em.op(Opc::IntAdd, 1, b, k(1, 1));
                    em.zext(v, size)
                }
                Slot::Fixed => unreachable!(),
            };
            memo.insert((s, size), v);
            v
        };
        let mut umap = FxHashMap::default();
        let mut chk = self.ops(t.ops(), acc, is_stack_op(*op), &mut slot, &mut umap);
        // Penalties use the registers the instruction leaves.
        let maxpen = match t.pen {
            PenKind::Ax | PenKind::Ay => {
                let idx = r1(if t.pen == PenKind::Ax { R::X } else { R::Y });
                let c = self.op(Opc::IntCarry, 1, ob[0], idx);
                let z = self.zext(c, 4);
                self.pend.1.push(z);
                1
            }
            PenKind::Iy => {
                let p = umap[&(t.iy_base as u32)];
                let lo = self.op(Opc::IntAnd, 2, p, k(0xff, 2));
                let y = self.zext(r1(R::Y), 2);
                let s = self.op(Opc::IntAdd, 2, lo, y);
                let c = self.op(Opc::IntLess, 1, k(0xff, 2), s);
                let z = self.zext(c, 4);
                self.pend.1.push(z);
                1
            }
            PenKind::Branch => 2,
            PenKind::None => 0,
        };
        let cyc = t.cyc as u32;
        match ctl {
            Ctl::Next(n) => {
                self.account(cyc, maxpen);
                self.check(chk, k(next as u32, 2));
                let sets_sp = crate::analysis::stack_delta(*op).is_none();
                self.exit_or(exit, k(*n as u32, 2), |em| {
                    let b = em.block(*n);
                    em.f.blocks[b as usize].check |= sets_sp;
                    em.emit(Opc::Branch, NONE, NONE, NONE, b);
                });
            }
            Ctl::Br { t: tg, f } => {
                let CtrlKind::Br { flag, pol } = t.ctrl else {
                    unreachable!()
                };
                let fl = r1(flag as usize);
                let c = if pol == 1 {
                    fl
                } else {
                    self.op(Opc::IntEqual, 1, fl, k(0, 1))
                };
                self.account(cyc, maxpen);
                match tg {
                    Tg::At(a) => {
                        let z = self.zext(c, 4);
                        let z = if (a ^ f) & 0xff00 != 0 {
                            self.op(Opc::IntLeft, 4, z, k(1, 1))
                        } else {
                            z
                        };
                        self.pend.1.push(z);
                        self.flush();
                        let (bt, bf) = (self.block(*a), self.block(*f));
                        self.emit(Opc::CBranch, NONE, c, NONE, bt);
                        self.emit(Opc::Branch, NONE, NONE, NONE, bf);
                    }
                    Tg::Calc(ts) => {
                        self.flush();
                        let (x, bf) = (self.new_block(pc), self.block(*f));
                        self.emit(Opc::CBranch, NONE, c, NONE, x);
                        self.emit(Opc::Branch, NONE, NONE, NONE, bf);
                        self.cur = x as usize;
                        let o = self.zext(ob[0], 2);
                        let neg = self.op(Opc::IntLess, 1, k(0x7f, 2), o);
                        let neg = self.zext(neg, 2);
                        let neg = self.op(Opc::IntSub, 2, k(0, 2), neg);
                        let hi = self.op(Opc::IntAnd, 2, neg, k(0xff00, 2));
                        let ext = self.op(Opc::IntOr, 2, o, hi);
                        let tgt = self.op(Opc::IntAdd, 2, k(*f as u32, 2), ext);
                        let d = self.op(Opc::IntXor, 2, tgt, k(*f as u32, 2));
                        let d = self.op(Opc::IntAnd, 2, d, k(0xff00, 2));
                        let d = self.op(Opc::IntNotEqual, 1, d, k(0, 2));
                        let d = self.zext(d, 4);
                        self.pend = (1, vec![d]);
                        self.flush();
                        let tb = self.table(ts);
                        self.emit(Opc::BranchInd, NONE, tgt, NONE, tb);
                    }
                }
            }
            Ctl::Jmp(tg) => {
                self.account(cyc, maxpen);
                self.flush();
                self.goto(tg, |em| slot(em, Slot::Word, 2));
            }
            Ctl::JmpInd(ts) => {
                let (lo_a, hi_a, iv) = match bytes {
                    [Some(a), Some(b)] => {
                        let w = u16::from_le_bytes([a, b]);
                        let h = (w & 0xff00) | (w.wrapping_add(1) & 0xff);
                        let iv = Iv {
                            lo: w.min(h) as u32,
                            hi: w.max(h) as u32,
                        };
                        (k(w as u32, 2), k(h as u32, 2), iv)
                    }
                    _ => {
                        let w = slot(self, Slot::Word, 2);
                        let page = self.op(Opc::IntAnd, 2, w, k(0xff00, 2));
                        let inc = self.op(Opc::IntAdd, 2, w, k(1, 2));
                        let inc = self.op(Opc::IntAnd, 2, inc, k(0xff, 2));
                        (w, self.op(Opc::IntOr, 2, page, inc), Iv::full(2))
                    }
                };
                let lo = self.load(lo_a, iv, 0);
                let hi = self.load(hi_a, iv, 0);
                let tgt = self.word(lo, hi);
                self.account(cyc, maxpen);
                self.flush();
                let tb = self.table(ts);
                self.emit(Opc::BranchInd, NONE, tgt, NONE, tb);
            }
            Ctl::Call { f, ret } => {
                let ra = ret.wrapping_sub(1) as u32;
                if self.sh.private {
                    self.emit(Opc::IntSub, r1(R::SP), r1(R::SP), k(2, 1), 0);
                } else {
                    self.push(k(ra >> 8, 1), &mut chk);
                    self.push(k(ra & 0xff, 1), &mut chk);
                }
                self.account(cyc, maxpen);
                self.check(chk, k(*f as u32, 2));
                self.flush();
                let fi = self.sh.fidx[f];
                self.emit(Opc::Call, NONE, k(ra, 2), NONE, fi);
                let rb = self.new_block(*ret);
                self.emit(Opc::Branch, NONE, NONE, NONE, rb);
                self.cur = rb as usize;
                if self.sh.ret_guard.contains(fi as usize) {
                    let s1 = self.op(Opc::IntSub, 1, r1(R::SP), k(1, 1));
                    let a = self.zext(s1, 2);
                    let a = self.op(Opc::IntOr, 2, a, k(0x100, 2));
                    let lo = self.load(a, PAGE1, mem::STACK);
                    let a = self.sp_addr();
                    let hi = self.load(a, PAGE1, mem::STACK);
                    let w = self.word(lo, hi);
                    let bad = self.op(Opc::IntNotEqual, 1, w, k(ra, 2));
                    self.side_exit(bad, |em| {
                        let t = em.op(Opc::IntAdd, 2, w, k(1, 2));
                        em.deopt(t);
                    });
                }
                let b = self.block(*ret);
                self.emit(Opc::Branch, NONE, NONE, NONE, b);
            }
            Ctl::PushJmp { t: tg, ret } => {
                let ra = ret.wrapping_sub(1) as u32;
                let tv = matches!(tg, Tg::Calc(_)).then(|| slot(self, Slot::Word, 2));
                self.push(k(ra >> 8, 1), &mut chk);
                self.push(k(ra & 0xff, 1), &mut chk);
                self.account(cyc, maxpen);
                let to = match tg {
                    Tg::At(a) => k(*a as u32, 2),
                    Tg::Calc(_) => tv.unwrap(),
                };
                self.check(chk, to);
                self.flush();
                self.goto(tg, |_| tv.unwrap());
            }
            Ctl::Return => {
                self.emit(Opc::IntAdd, r1(R::SP), r1(R::SP), k(2, 1), 0);
                self.account(cyc, maxpen);
                self.flush();
                self.emit(Opc::Return, NONE, NONE, NONE, 0);
            }
            Ctl::Rts(ts) | Ctl::Rti(ts) => {
                if matches!(ctl, Ctl::Rti(_)) {
                    let plp = Template::of(0x28);
                    let mut none = |_: &mut Self, _: Slot, _: u8| -> Vn { unreachable!() };
                    let _ = self.ops(plp.ops(), &[], true, &mut none, &mut FxHashMap::default());
                }
                let lo = self.pull();
                let hi = self.pull();
                let mut tgt = self.word(lo, hi);
                if matches!(ctl, Ctl::Rts(_)) {
                    tgt = self.op(Opc::IntAdd, 2, tgt, k(1, 2));
                }
                self.account(cyc, maxpen);
                self.flush();
                self.exit_or(exit, tgt, |em| {
                    let tb = em.table(ts);
                    em.emit(Opc::BranchInd, NONE, tgt, NONE, tb);
                });
            }
            Ctl::Brk(ts) => {
                let ra = pc.wrapping_add(2) as u32;
                self.push(k(ra >> 8, 1), &mut chk);
                self.push(k(ra & 0xff, 1), &mut chk);
                let php = Template::of(0x08);
                let mut none = |_: &mut Self, _: Slot, _: u8| -> Vn { unreachable!() };
                let acc = [Access {
                    store: true,
                    addr: PAGE1,
                    val: Iv::full(1),
                }];
                chk.extend(self.ops(php.ops(), &acc, true, &mut none, &mut FxHashMap::default()));
                self.emit(Opc::Copy, r1(R::I), k(1, 1), NONE, 0);
                let vec = Iv {
                    lo: 0xfffe,
                    hi: 0xffff,
                };
                let lo = self.load(k(0xfffe, 2), vec, 0);
                let hi = self.load(k(0xffff, 2), vec, 0);
                let tgt = self.word(lo, hi);
                self.account(cyc, maxpen);
                self.check(chk, tgt);
                self.flush();
                let tb = self.table(ts);
                self.emit(Opc::BranchInd, NONE, tgt, NONE, tb);
            }
            Ctl::Jam => {
                self.flush();
                self.deopt(k(pc as u32, 2));
            }
        }
    }

    /// Exit to `pc` per `exit`, otherwise continue with `cont`.
    fn exit_or(&mut self, exit: Exit, pc: Vn, cont: impl FnOnce(&mut Self)) {
        match exit {
            Exit::Static => {
                self.flush();
                self.emit(Opc::Return, NONE, pc, NONE, 0);
            }
            Exit::Dynamic => {
                self.flush();
                let s = self.zext(r1(R::SP), 2);
                let c = self.op(Opc::IntLessEqual, 1, reg(E, 2), s);
                self.side_exit(c, |em| em.emit(Opc::Return, NONE, pc, NONE, 0));
                cont(self);
            }
            Exit::No => {
                self.flush();
                cont(self);
            }
        }
    }
}
