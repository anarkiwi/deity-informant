//! Emitting planned functions as P-Code blocks: templates with code cells read from memory,
//! opcode-cell dispatch, stack control as `CALL`/`RETURN`/`BRANCHIND`, pushes and pulls as
//! stack-slot registers, optional cycle accounting in the `CY` register, and guards that
//! deopt when an assumption fails.

use crate::analysis::{
    is_stack_op, operand_bytes, page1_check, Access, Check, Entry, Graph, Iv, Tg,
};
use crate::bank::{Back, BankMap};
use crate::func::{Arm, Ctl, Exit, Key, Plan, H};
use crate::ir::{cy, k, mem, other, r1, reg, tmp, Block, Function, LOp, E, NREG};
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
    /// Only pushes and pulls reach page 1, given a check before every other access that may.
    pub private: bool,
    /// Pushes and return addresses are also stored to memory (a load may read page 1).
    pub shadow: bool,
    /// Functions whose pushes and pulls are stack-slot registers.
    pub promote: FixedBitSet,
    /// Functions in a call-graph cycle.
    pub recursive: FixedBitSet,
    /// Count cycles.
    pub cycles: bool,
    /// Function index of each callee per entry configuration.
    pub fidx: FxHashMap<(u16, u8), u32>,
    /// The bank map (none: flat memory).
    pub bank: Option<&'a BankMap>,
    /// Callees whose returns must check their return address (they may store into page 1).
    pub ret_guard: FixedBitSet,
    /// Per function: the slot indices whose stack bytes it keeps in slot registers.
    pub slots: Vec<FixedBitSet>,
    /// The `[lo, hi]` page-1 addresses each instruction reached in the facts.
    pub seen: FxHashMap<u16, (u16, u16)>,
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
    blk: FxHashMap<Key, u32>,
    queue: VecDeque<Key>,
    /// Height of the node being emitted and the successors of its current variant.
    nh: H,
    /// Configuration of the node being emitted.
    nc: u8,
    succ: &'a [Key],
    cur: usize,
    next: u32,
    /// Cycles accounted but not yet added to `CY`: a constant and 4-byte values.
    pend: (u32, Vec<Vn>),
    /// Stack height of the current instruction relative to the function entry, when its
    /// pushes and pulls are stack slots.
    h: Option<i32>,
    promote: bool,
    slots: &'a FixedBitSet,
    n: Counts,
    /// The block the current instruction started in.
    istart: u32,
    /// The decimal flag may be set at the current instruction.
    dec: bool,
    /// The current instruction's checks include a code check: the code a deopt resumes may
    /// have changed, so it hands over every register.
    coded: bool,
}

/// What emitting a function produced.
#[derive(Clone, Copy, Debug, Default)]
pub struct Counts {
    /// Instruction variants and their template ops.
    pub insns: usize,
    pub raw: usize,
    pub code_guards: usize,
    pub stack_guards: usize,
    pub ptr_guards: usize,
    pub slot_ops: usize,
    /// Dispatches on SP after SP-setting instructions.
    pub sp_dispatch: usize,
    /// Dispatches on the bank configuration at entries and after port writes.
    pub bank_dispatch: usize,
}

/// Emit planned function `fi`.
/// `entry_port` is the port registers every call of an outermost plan was seen entered with,
/// when they were always the same.
pub fn emit(
    sh: &Shared,
    plan: &Plan,
    entry: Option<&Entry>,
    entry_port: Option<(u8, u8)>,
    fi: usize,
) -> (Function, Counts) {
    let mut em = Em {
        sh,
        plan,
        f: Function {
            pc: plan.pc,
            top: plan.top,
            recursive: sh.recursive.contains(fi),
            ..Function::default()
        },
        blk: FxHashMap::default(),
        queue: VecDeque::new(),
        nh: H::Known(0),
        nc: 0,
        succ: &[],
        cur: 0,
        next: 0,
        pend: (0, Vec::new()),
        h: None,
        promote: sh.promote.contains(fi),
        slots: &sh.slots[fi],
        n: Counts::default(),
        istart: 0,
        dec: false,
        coded: false,
    };
    em.new_block(plan.pc);
    if let Some(s) = plan.sp {
        let bad = em.op(Opc::IntNotEqual, 1, r1(R::SP), k(s as u32, 1));
        em.side_exit(bad, true, |em| em.deopt(k(plan.pc as u32, 2)));
    }
    if plan.top {
        let pre =
            sh.g.prot
                .pre
                .get(&plan.pc)
                .into_iter()
                .flat_map(|b| b.ones());
        let bad: Vec<Vn> = pre.map(|q| em.window(q as u8)).collect();
        if let Some(c) = bad.into_iter().reduce(|a, b| em.op(Opc::IntOr, 1, a, b)) {
            em.side_exit(c, true, |em| em.deopt(k(plan.pc as u32, 2)));
        }
    }
    for (i, vals) in entry.into_iter().flat_map(|e| &e.regs) {
        if let [v] = vals[..] {
            let bad = em.op(Opc::IntNotEqual, 1, r1(*i), k(v as u32, 1));
            em.side_exit(bad, true, |em| em.deopt(k(plan.pc as u32, 2)));
            em.emit(Opc::Copy, r1(*i), k(v as u32, 1), NONE, 0);
        }
    }
    let roots: Vec<Key> = plan.roots().collect();
    match (sh.bank, plan.top) {
        (Some(m), true) => em.entry_port(m, entry_port, &roots),
        _ => {
            let b = em.block(roots[0]);
            em.emit(Opc::Branch, NONE, NONE, NONE, b);
        }
    }
    while let Some(key) = em.queue.pop_front() {
        em.cur = em.blk[&key] as usize;
        em.next = 0;
        em.node(key);
    }
    if em.n.sp_dispatch > 0 && em.f.nslots > 0 {
        em.preload();
    }
    if em.promote {
        em.f.slotted = em.slots.clone();
    }
    (em.f, em.n)
}

impl<'a> Em<'a, '_> {
    fn new_block(&mut self, pc: u16) -> u32 {
        self.f.blocks.push(Block {
            pc,
            ..Block::default()
        });
        (self.f.blocks.len() - 1) as u32
    }
    /// The block of node `key`, queued for emission on first use; a deopt to its start if
    /// the plan has no such node.
    fn block(&mut self, key: Key) -> u32 {
        if let Some(&b) = self.blk.get(&key) {
            return b;
        }
        let pc = key.0;
        let b = self.new_block(pc);
        self.blk.insert(key, b);
        if self.plan.nodes.contains_key(&key) {
            self.queue.push_back(key);
        } else {
            let save = std::mem::replace(&mut self.cur, b as usize);
            self.deopt(k(pc as u32, 2));
            self.cur = save;
        }
        b
    }
    /// The node the current variant transfers to at `pc`.
    fn key(&self, pc: u16) -> Key {
        self.succ
            .iter()
            .copied()
            .find(|s| s.0 == pc)
            .unwrap_or((pc, H::Unknown, self.nc))
    }
    /// The block of the current variant's successor at `pc`.
    fn to(&mut self, pc: u16) -> u32 {
        self.block(self.key(pc))
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
        let t = self.temp(1);
        self.load_into(t, addr, iv, flags);
        t
    }
    /// `out = LOAD addr` (interval `iv`) as the current configuration maps it: a ROM byte
    /// is a constant, a read of the port's data cell is computed from its registers.
    fn load_into(&mut self, out: Vn, addr: Vn, iv: Iv, flags: u32) {
        let Some(m) = self.sh.bank else {
            let f = flags | self.vflag(iv);
            return self.emit(Opc::Load, out, addr, NONE, f);
        };
        match self.backing(m, iv) {
            Some(Back::Rom) => match iv.value() {
                Some(a) => self.emit(Opc::Copy, out, k(m.rom[a as usize] as u32, 1), NONE, 0),
                None => self.emit(Opc::Load, out, addr, NONE, flags | mem::ROM),
            },
            _ if iv.value() == Some(m.data as u32) => {
                let (d, v) = self.port_regs(m);
                let hi = self.op(Opc::IntAnd, 1, v, d);
                let nd = self.op(Opc::IntXor, 1, d, k(0xff, 1));
                let lo = self.op(Opc::IntAnd, 1, nd, k(m.pull as u32, 1));
                self.emit(Opc::IntOr, out, hi, lo, 0);
            }
            b => {
                let f = flags | self.bflags(b, iv);
                self.emit(Opc::Load, out, addr, NONE, f);
            }
        }
    }
    /// The port's direction and data registers (the RAM under its cells).
    fn port_regs(&mut self, m: &BankMap) -> (Vn, Vn) {
        let (d, v) = (self.temp(1), self.temp(1));
        self.emit(Opc::Load, d, k(m.dir as u32, 2), NONE, mem::RAM);
        self.emit(Opc::Load, v, k(m.data as u32, 2), NONE, mem::RAM);
        (d, v)
    }
    /// Access flags for interval `iv` with uniform backing `b` (`None`: mixed, resolved per
    /// address in the current configuration at run time).
    fn bflags(&mut self, b: Option<Back>, iv: Iv) -> u32 {
        match b {
            Some(Back::Ram | Back::Rom) => mem::RAM,
            Some(Back::Io) => mem::IO | self.vflag(iv),
            None => mem::BANK | (self.nc as u32) << 8 | self.vflag(iv),
        }
    }
    /// The backing of every page of `iv` in the current configuration, if it is one.
    fn backing(&self, m: &BankMap, iv: Iv) -> Option<Back> {
        let b = m.back(self.nc, iv.lo as u16);
        let port = iv.meets(m.data as u32, m.data as u32 + 1) && iv.value().is_none();
        ((iv.lo >> 8..=iv.hi.min(0xffff) >> 8).all(|p| m.back(self.nc, (p << 8) as u16) == b)
            && !port)
            .then_some(b)
    }
    /// Store flags for interval `iv` in the current configuration.
    fn store_flags(&mut self, iv: Iv) -> u32 {
        match self.sh.bank {
            None => self.vflag(iv),
            Some(m) => {
                let b = self.backing(m, iv);
                self.bflags(b, iv)
            }
        }
    }
    /// The stack-slot register of the byte at offset `d` from the entry SP, if promoted and
    /// a slot (other bytes stay in memory).
    fn slot(&mut self, d: i32) -> Option<Vn> {
        let i = u32::try_from(-d)
            .ok()
            .filter(|&i| i < 0x100 && self.h.is_some() && self.slots.contains(i as usize))?;
        self.f.nslots = self.f.nslots.max(i + 1);
        self.n.slot_ops += 1;
        Some(reg((NREG as u32 + i) as u16, 1))
    }
    /// Whether an access at `addr` from the current node may hit a stack byte kept out of
    /// memory (none unless the stack is private).
    fn kept(&self, addr: Iv) -> bool {
        self.sh.private && self.plan.kept(self.slots, self.nh, addr)
    }
    /// Deopt to `pc` before the access at `addr` when it reaches page 1 (outside `allow`).
    fn page1_guard(&mut self, addr: Vn, pc: u16, allow: Option<Iv>) {
        let page = self.op(Opc::IntAnd, 2, addr, k(0xff00, 2));
        let mut hit = self.op(Opc::IntEqual, 1, page, k(0x100, 2));
        if let Some(r) = allow {
            let off = self.op(Opc::IntSub, 2, addr, k(r.lo, 2));
            let out = self.op(Opc::IntLess, 1, k(r.hi - r.lo, 2), off);
            hit = self.op(Opc::IntAnd, 1, hit, out);
        }
        self.n.stack_guards += 1;
        let x = self.side_exit(hit, true, |em| em.deopt(k(pc as u32, 2)));
        self.f.blocks[x as usize].replay = Some(self.istart);
    }
    /// Whether an access in `iv` may reach a volatile address in the current configuration.
    fn reaches(&self, iv: Iv) -> bool {
        self.sh.volatile(iv)
            && self
                .sh
                .bank
                .is_none_or(|m| !matches!(self.backing(m, iv), Some(Back::Ram | Back::Rom)))
    }
    /// A throw-away read of `addr` (interval `iv`) when `pred` holds (none: always), emitted
    /// only where it may reach a volatile address: elsewhere it has no effect.
    fn touch(&mut self, addr: Vn, pred: Vn, iv: Iv) {
        if !self.reaches(iv) {
            return;
        }
        let f = match self.sh.bank {
            None => self.vflag(iv),
            Some(m) => {
                let b = self.backing(m, iv);
                self.bflags(b, iv)
            }
        };
        self.emit(Opc::Load, NONE, addr, pred, f);
    }
    /// The entry's return address is still the host's (`Program.call`'s `$0001`, never
    /// volatile) at an exit: nothing but pushes reaches page 1 and none lands above the entry.
    fn host_return(&self) -> bool {
        let p = self.plan;
        self.sh.private
            && p.top
            && !p.sets_sp
            && p.nodes.keys().all(|k| matches!(k.1, H::Known(h) if h <= 0))
    }
    fn touch_at(&mut self, a: u16, pred: Vn) {
        self.touch(k(a as u32, 2), pred, Iv::exact(a as u32));
    }
    /// The throw-away read of the address an `RTS` pulled (`ra`, interval `iv`), made after
    /// the `RTS` in a caller: with cycles, at the `RTS`'s start cycle.
    fn touch_ret(&mut self, ra: Vn, iv: Iv) {
        if !self.reaches(iv) {
            return;
        }
        let rts = Template::of(0x60).cyc as u32;
        if self.sh.cycles {
            self.emit(Opc::IntSub, cy(), cy(), k(rts, 4), 0);
        }
        self.touch(ra, NONE, iv);
        if self.sh.cycles {
            self.emit(Opc::IntAdd, cy(), cy(), k(rts, 4), 0);
        }
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
        if !self.sh.cycles {
            return;
        }
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
        if !self.sh.cycles {
            return;
        }
        self.pend.0 += cyc;
        self.f.blocks[self.cur].maxcyc += cyc + maxpen;
    }
    fn deopt(&mut self, pc: Vn) {
        self.emit(Opc::CallOther, NONE, pc, NONE, other::DEOPT);
    }
    /// Leave through a new block when `cond` holds (the block sees the pending cycles), and
    /// continue in a fresh block. A `soft` exit's deopt state is what the continuation would
    /// run from.
    fn side_exit(&mut self, cond: Vn, soft: bool, body: impl FnOnce(&mut Self)) -> u32 {
        let pc = self.f.blocks[self.cur].pc;
        let (x, cont) = (self.new_block(pc), self.new_block(pc));
        self.f.blocks[x as usize].resume = soft.then_some(cont);
        self.emit(Opc::CBranch, NONE, cond, NONE, x);
        self.emit(Opc::Branch, NONE, NONE, NONE, cont);
        let pend = self.pend.clone();
        self.cur = x as usize;
        self.flush();
        body(self);
        self.pend = pend;
        self.cur = cont as usize;
        x
    }
    fn table(&mut self, ts: &[u16]) -> u32 {
        let keys: Vec<Key> = ts
            .iter()
            .map(|&t| self.key(t))
            .filter(|k| self.plan.nodes.contains_key(k))
            .collect();
        let mut t: Vec<(u16, u32)> = keys.into_iter().map(|k| (k.0, self.block(k))).collect();
        t.sort_unstable();
        self.f.tables.push(t);
        (self.f.tables.len() - 1) as u32
    }
    fn goto(&mut self, t: &Tg, calc: impl FnOnce(&mut Self) -> Vn) {
        match t {
            Tg::At(a) => {
                let b = self.to(*a);
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
    fn store_stack(&mut self, v: Vn) -> Vn {
        let a = self.sp_addr();
        let flags = mem::STACK | self.store_flags(PAGE1);
        self.emit(Opc::Store, NONE, a, v, flags);
        a
    }
    fn push(&mut self, v: Vn, chk: &mut Vec<Vn>) {
        let slot = self.h.and_then(|h| self.slot(h));
        if let Some(s) = slot {
            self.emit(Opc::Copy, s, v, NONE, 0);
        }
        if slot.is_none() || self.sh.shadow {
            let a = self.store_stack(v);
            if self.sh.hits_code(PAGE1) {
                self.n.code_guards += 1;
                chk.push(self.op(Opc::CallOther, 1, a, v));
                self.f.blocks[self.cur].ops.last_mut().unwrap().aux = other::CODECHK;
                self.coded = true;
            }
        }
        self.emit(Opc::IntSub, r1(R::SP), r1(R::SP), k(1, 1), 0);
        self.h = self.h.map(|h| h - 1);
    }
    fn pull(&mut self) -> Vn {
        self.emit(Opc::IntAdd, r1(R::SP), r1(R::SP), k(1, 1), 0);
        self.h = self.h.map(|h| h + 1);
        if let Some(s) = self.h.and_then(|h| self.slot(h)) {
            return self.op(Opc::Copy, 1, s, NONE);
        }
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
    /// Whether the pointer pair at `q` is in its window, read from memory.
    fn window(&mut self, q: u8) -> Vn {
        let t = self.sh.g.prot.win[q as usize].unwrap() as u32;
        let ld = |em: &mut Self, a: u8| em.load(k(a as u32, 2), Iv::exact(a as u32), 0);
        let lo = ld(self, q);
        let hi = ld(self, q.wrapping_add(1));
        let p = self.word(lo, hi);
        let off = self.op(Opc::IntSub, 2, p, k(0x100 - t, 2));
        self.n.ptr_guards += 1;
        self.op(Opc::IntLess, 1, off, k(0x100 + t, 2))
    }
    /// Deopt to `pc` when any check fired; after a code check with every register handed
    /// over, as the reference may run changed code.
    fn check(&mut self, chk: Vec<Vn>, pc: Vn) {
        let Some(mut c) = chk.first().copied() else {
            return;
        };
        for &d in &chk[1..] {
            c = self.op(Opc::IntOr, 1, c, d);
        }
        let soft = !std::mem::take(&mut self.coded);
        self.side_exit(c, soft, |em| em.deopt(pc));
    }

    fn node(&mut self, key: Key) {
        let (plan, sh) = (self.plan, self.sh);
        let pc = key.0;
        let arms = &plan.nodes[&key].arms;
        let vars = &sh.g.vars[&pc];
        (self.nh, self.nc) = (key.1, key.2);
        if !sh.g.cells.contains(pc as usize) {
            self.insn(pc, &vars[0].op, &arms[0]);
            return;
        }
        let v = self.load(k(pc as u32, 2), Iv::exact(pc as u32), 0);
        for (var, arm) in vars.iter().zip(arms) {
            let c = self.op(Opc::IntEqual, 1, v, k(var.op as u32, 1));
            let vb = self.new_block(pc);
            let cont = self.new_block(pc);
            self.emit(Opc::CBranch, NONE, c, NONE, vb);
            self.emit(Opc::Branch, NONE, NONE, NONE, cont);
            self.cur = vb as usize;
            self.insn(pc, &var.op, arm);
            self.cur = cont as usize;
        }
        self.deopt(k(pc as u32, 2));
    }

    /// Instantiate template `ops` of opcode `opc` at `pc` with operand values from `slot`,
    /// temporaries renamed, accesses flagged, pushes and pulls as stack slots where promoted
    /// and page-1 checks where private; the code checks of its stores.
    #[allow(clippy::too_many_arguments)]
    fn ops(
        &mut self,
        pc: u16,
        opc: u8,
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
            if stack && matches!(op.opc, Opc::Load | Opc::Store) {
                let h = self.h.map(|h| h + (op.opc == Opc::Load) as i32);
                if let Some(s) = h.and_then(|d| self.slot(d)) {
                    match op.opc {
                        Opc::Store => self.emit(Opc::Copy, s, b, NONE, 0),
                        _ => self.emit(Opc::Copy, out, s, NONE, 0),
                    }
                    self.h = self.h.map(|h| h + if op.opc == Opc::Load { 1 } else { -1 });
                    continue;
                }
                self.h = self.h.map(|h| h + if op.opc == Opc::Load { 1 } else { -1 });
            }
            if matches!(op.opc, Opc::Load | Opc::Store) {
                let ac = acc.get(ai).copied().unwrap_or(Access {
                    store: op.opc == Opc::Store,
                    addr: Iv::full(2),
                    val: Iv::full(1),
                    dummy: op.out.is_none(),
                    back: false,
                    defd: false,
                    above: false,
                    ptr: None,
                });
                if op.opc == Opc::Load && op.out.is_none() {
                    ai += 1;
                    self.touch(a, b, ac.addr);
                    continue;
                }
                if !stack
                    && page1_check(opc, ai) == Check::Own
                    && self.sh.private
                    && ac.page1(&self.sh.g.prot.win)
                {
                    match self.plan.narrowed(&self.sh.seen, pc, opc, ai, ac.addr) {
                        Some(r) => self.page1_guard(a, pc, Some(r)),
                        None if self.kept(ac.addr) => self.page1_guard(a, pc, None),
                        None => {}
                    }
                }
                ai += 1;
                aux = match (stack, op.opc) {
                    (true, Opc::Load) => {
                        self.load_into(out, a, ac.addr, mem::STACK);
                        continue;
                    }
                    (true, _) => self.store_flags(ac.addr) | mem::STACK,
                    (false, Opc::Load) => {
                        self.load_into(out, a, ac.addr, 0);
                        continue;
                    }
                    _ => self.store_flags(ac.addr),
                };
                if op.opc == Opc::Store {
                    self.emit(Opc::Store, out, a, b, aux);
                    let pairs = self.sh.g.prot.chk.get(&(pc, opc, ai - 1));
                    for &q in pairs.into_iter().flatten().filter(|_| !stack) {
                        let bad = self.window(q);
                        chk.push(bad);
                    }
                    if self.sh.hits_code(ac.addr) {
                        self.n.code_guards += 1;
                        chk.push(self.op(Opc::CallOther, 1, a, b));
                        self.f.blocks[self.cur].ops.last_mut().unwrap().aux = other::CODECHK;
                        self.coded = true;
                    }
                    continue;
                }
            }
            let aux = if self.dec { crate::ir::DECIMAL } else { aux };
            self.emit(op.opc, out, a, b, aux);
        }
        chk
    }

    fn insn(&mut self, pc: u16, op: &u8, arm: &'a Arm) {
        let (ctl, exit, acc) = (&arm.ctl, arm.exit, &arm.acc[..]);
        self.succ = &arm.succ;
        self.istart = self.cur as u32;
        self.dec = self.sh.g.dec.contains(pc as usize);
        self.coded = false;
        let t = Template::of(*op);
        self.n.insns += 1;
        self.n.raw += t.n as usize;
        self.h = match self.nh {
            H::Known(h) if self.promote => Some(h),
            _ => None,
        };
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
        // A one-byte instruction first reads the byte after its opcode.
        if t.len == 1 && t.ctrl != CtrlKind::Jam {
            self.touch_at(next, NONE);
        }
        let mut umap = FxHashMap::default();
        let stack = is_stack_op(*op);
        let mut chk = self.ops(pc, *op, t.ops(), acc, stack, &mut slot, &mut umap);
        // Penalties use the registers the instruction leaves.
        let maxpen = match t.pen {
            _ if !self.sh.cycles => 0,
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
                    if sets_sp && em.plan.top {
                        em.sp_dispatch(*n);
                    } else if arm.port {
                        let keys = em.succ.to_vec();
                        em.bank_dispatch(*n, &keys);
                    } else {
                        let b = em.to(*n);
                        em.f.blocks[b as usize].check |= sets_sp;
                        em.emit(Opc::Branch, NONE, NONE, NONE, b);
                    }
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
                // Taken, a branch reads the next byte, then on a page cross the target's low
                // byte in the next byte's page.
                self.touch_at(*f, c);
                let page = Iv {
                    lo: (*f & 0xff00) as u32,
                    hi: (*f | 0xff) as u32,
                };
                let calc = match tg {
                    Tg::At(a) => {
                        if (a ^ f) & 0xff00 != 0 {
                            self.touch_at(*f & 0xff00 | *a & 0xff, c);
                        }
                        None
                    }
                    Tg::Calc(_) => {
                        let o = self.zext(ob[0], 2);
                        let neg = self.op(Opc::IntLess, 1, k(0x7f, 2), o);
                        let neg = self.zext(neg, 2);
                        let neg = self.op(Opc::IntSub, 2, k(0, 2), neg);
                        let hi = self.op(Opc::IntAnd, 2, neg, k(0xff00, 2));
                        let ext = self.op(Opc::IntOr, 2, o, hi);
                        let tgt = self.op(Opc::IntAdd, 2, k(*f as u32, 2), ext);
                        if self.reaches(page) {
                            let lo = self.op(Opc::IntAnd, 2, tgt, k(0xff, 2));
                            let u = self.op(Opc::IntOr, 2, lo, k((*f & 0xff00) as u32, 2));
                            let x = self.op(Opc::IntNotEqual, 1, u, tgt);
                            let p = self.op(Opc::IntAnd, 1, x, c);
                            self.touch(u, p, page);
                        }
                        Some(tgt)
                    }
                };
                self.account(cyc, maxpen);
                match tg {
                    Tg::At(a) => {
                        if self.sh.cycles {
                            let z = self.zext(c, 4);
                            let z = if (a ^ f) & 0xff00 != 0 {
                                self.op(Opc::IntLeft, 4, z, k(1, 1))
                            } else {
                                z
                            };
                            self.pend.1.push(z);
                        }
                        self.flush();
                        let (bt, bf) = (self.to(*a), self.to(*f));
                        self.emit(Opc::CBranch, NONE, c, NONE, bt);
                        self.emit(Opc::Branch, NONE, NONE, NONE, bf);
                    }
                    Tg::Calc(ts) => {
                        self.flush();
                        let (x, bf) = (self.new_block(pc), self.to(*f));
                        self.emit(Opc::CBranch, NONE, c, NONE, x);
                        self.emit(Opc::Branch, NONE, NONE, NONE, bf);
                        self.cur = x as usize;
                        let tgt = calc.unwrap();
                        if self.sh.cycles {
                            let d = self.op(Opc::IntXor, 2, tgt, k(*f as u32, 2));
                            let d = self.op(Opc::IntAnd, 2, d, k(0xff00, 2));
                            let d = self.op(Opc::IntNotEqual, 1, d, k(0, 2));
                            let d = self.zext(d, 4);
                            self.pend = (1, vec![d]);
                            self.flush();
                        }
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
                if self.kept(iv) {
                    self.page1_guard(lo_a, pc, None);
                    self.page1_guard(hi_a, pc, None);
                }
                let lo = self.load(lo_a, iv, 0);
                let hi = self.load(hi_a, iv, 0);
                let tgt = self.word(lo, hi);
                self.account(cyc, maxpen);
                self.flush();
                let tb = self.table(ts);
                self.emit(Opc::BranchInd, NONE, tgt, NONE, tb);
            }
            Ctl::Call { f, ret, c } => {
                let ra = ret.wrapping_sub(1) as u32;
                if self.sh.private {
                    let bytes: &[u32] = if self.sh.shadow {
                        &[ra >> 8, ra & 0xff]
                    } else {
                        &[]
                    };
                    for &b in bytes {
                        self.store_stack(k(b, 1));
                        self.emit(Opc::IntSub, r1(R::SP), r1(R::SP), k(1, 1), 0);
                    }
                    if !self.sh.shadow {
                        self.emit(Opc::IntSub, r1(R::SP), r1(R::SP), k(2, 1), 0);
                    }
                } else {
                    self.push(k(ra >> 8, 1), &mut chk);
                    self.push(k(ra & 0xff, 1), &mut chk);
                }
                self.account(cyc, maxpen);
                self.check(chk, k(*f as u32, 2));
                self.flush();
                let fi = self.sh.fidx[&(*f, *c)];
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
                    self.side_exit(bad, false, |em| {
                        em.touch_ret(w, Iv::full(2));
                        let t = em.op(Opc::IntAdd, 2, w, k(1, 2));
                        em.deopt(t);
                    });
                }
                self.touch_ret(k(ra, 2), Iv::exact(ra));
                if arm.port {
                    let keys = self.succ.to_vec();
                    self.bank_dispatch(*ret, &keys);
                } else {
                    let b = self.to(*ret);
                    self.emit(Opc::Branch, NONE, NONE, NONE, b);
                }
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
                    let mut u = FxHashMap::default();
                    let _ = self.ops(pc, 0x28, plp.ops(), &[], true, &mut none, &mut u);
                }
                let lo = self.pull();
                let hi = self.pull();
                let mut tgt = self.word(lo, hi);
                if matches!(ctl, Ctl::Rts(_)) {
                    if !(exit == Exit::Static && self.host_return()) {
                        self.touch(tgt, NONE, Iv::full(2));
                    }
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
                    dummy: false,
                    back: false,
                    defd: false,
                    above: false,
                    ptr: None,
                }];
                let mut u = FxHashMap::default();
                chk.extend(self.ops(pc, 0x08, php.ops(), &acc, true, &mut none, &mut u));
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

    /// Load every stack slot from memory before the entry, so the stack bytes a deopt after an
    /// SP-setting instruction writes back hold memory's values where this call did not push.
    fn preload(&mut self) {
        let body = self.new_block(self.plan.pc);
        self.f.blocks.swap(0, body as usize);
        (self.cur, self.next) = (0, 0);
        let (pushed, n) = (self.slots, self.f.nslots as usize);
        for i in pushed.ones().take_while(|&i| i < n) {
            let i = i as u32;
            let a = self.op(Opc::IntSub, 2, reg(E, 2), k(i + 2, 2));
            let a = self.op(Opc::IntAnd, 2, a, k(0xff, 2));
            let a = self.op(Opc::IntOr, 2, a, k(0x100, 2));
            let v = self.load(a, PAGE1, mem::STACK);
            self.emit(Opc::Copy, reg((NREG as u32 + i) as u16, 1), v, NONE, 0);
        }
        self.emit(Opc::Branch, NONE, NONE, NONE, body);
    }

    /// After an SP-setting instruction of an outermost plan: branch to the successor node at
    /// `pc` whose height SP is at relative to the entry (`E` is the entry SP + 2, compared
    /// without wrapping), restating SP as `E + height - 2` so its height is static again, else
    /// deopt.
    fn sp_dispatch(&mut self, pc: u16) {
        self.n.sp_dispatch += 1;
        let s = self.zext(r1(R::SP), 2);
        let d = self.op(Opc::IntSub, 2, s, reg(E, 2));
        for &key in self.succ {
            if key.0 != pc || !self.plan.nodes.contains_key(&key) {
                continue;
            }
            let H::Known(x) = key.1 else { unreachable!() };
            let rel = k((x - 2) as u32 & 0xffff, 2);
            let c = self.op(Opc::IntEqual, 1, d, rel);
            let arm = self.new_block(pc);
            self.f.blocks[arm as usize].check = true;
            self.emit(Opc::CBranch, NONE, c, NONE, arm);
            let save = std::mem::replace(&mut self.cur, arm as usize);
            self.emit(Opc::IntAdd, r1(R::SP), reg(E, 2), rel, 0);
            let b = self.block(key);
            self.emit(Opc::Branch, NONE, NONE, NONE, b);
            self.cur = save;
        }
        self.deopt(k(pc as u32, 2));
    }

    /// Branch to the node of `keys` at `pc` whose configuration the port registers select,
    /// else deopt at `pc`.
    fn bank_dispatch(&mut self, pc: u16, keys: &[Key]) {
        let m = self.sh.bank.unwrap();
        self.n.bank_dispatch += 1;
        let (d, v) = self.port_regs(m);
        let nd = self.op(Opc::IntXor, 1, d, k(0xff, 1));
        let e = self.op(Opc::IntOr, 1, v, nd);
        let e = self.op(Opc::IntAnd, 1, e, k(m.mask as u32, 1));
        for &key in keys {
            if key.0 != pc || !self.plan.nodes.contains_key(&key) {
                continue;
            }
            let b = self.block(key);
            for sel in m.selectors(key.2) {
                let hit = self.op(Opc::IntEqual, 1, e, k(sel as u32, 1));
                self.emit(Opc::CBranch, NONE, hit, NONE, b);
            }
        }
        self.deopt(k(pc as u32, 2));
    }

    /// An outermost entry: with the port registers its calls were always entered with, check
    /// them and restate them (so their configuration folds), else dispatch on them.
    fn entry_port(&mut self, m: &BankMap, regs: Option<(u8, u8)>, roots: &[Key]) {
        let pc = self.plan.pc;
        if let Some((d0, v0)) = regs {
            let (d, v) = self.port_regs(m);
            let bd = self.op(Opc::IntNotEqual, 1, d, k(d0 as u32, 1));
            let bv = self.op(Opc::IntNotEqual, 1, v, k(v0 as u32, 1));
            let bad = self.op(Opc::IntOr, 1, bd, bv);
            self.side_exit(bad, true, |em| em.deopt(k(pc as u32, 2)));
            self.emit(
                Opc::Store,
                NONE,
                k(m.dir as u32, 2),
                k(d0 as u32, 1),
                mem::RAM,
            );
            self.emit(
                Opc::Store,
                NONE,
                k(m.data as u32, 2),
                k(v0 as u32, 1),
                mem::RAM,
            );
        }
        self.bank_dispatch(pc, roots);
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
                self.side_exit(c, false, |em| em.emit(Opc::Return, NONE, pc, NONE, 0));
                cont(self);
            }
            Exit::No => {
                self.flush();
                cont(self);
            }
        }
    }
}
