//! Native code for an [`Exec`] (Cranelift): one machine function per lifted function. The
//! value-file cells a function uses live in SSA variables, written back before calls and
//! exits and reloaded after calls; flat memory is accessed directly and the rest through the
//! bus. A function returns `kind << 16 | pc`: kind 0 a `RETURN` (`pc` the exit PC at the
//! outermost frame), 1 a deopt to `pc`, 2 the iteration budget running out at `pc`.
//! Every exit leaves through the shared exit block with the written value-file cells as
//! arguments of the branch that takes it. Cranelift's optimizer places a pure value where
//! something uses it and keeps the copy in that block's dominator subtree only, so state read
//! by an exit block alone would be recomputed in each exit, a chain of guards squaring the
//! work; as branch arguments it is computed once, where the guard is.

use crate::bank::BankMap;
use crate::exec::{load, store, Exec, Frame, Runner, IMM, NOCHECK, SPACES};
use crate::ir::{mask, mem, other};
use cranelift_codegen::ir::condcodes::IntCC;
use cranelift_codegen::ir::{
    types, AbiParam, Block, BlockArg, FuncRef, InstBuilder, MemFlagsData as MemFlags, SigRef,
    Signature, UserFuncName, Value,
};
use cranelift_codegen::settings::{self, Configurable};
use cranelift_frontend::{FunctionBuilder, FunctionBuilderContext, Switch, Variable};
use cranelift_jit::{JITBuilder, JITModule};
use cranelift_module::{default_libcall_names, FuncId, Module};
use di_pcode::{reg as R, Bus, Opc};
use rustc_hash::FxHashMap;
use std::mem::offset_of;
use std::time::Instant;

/// Run state shared with native code.
#[repr(C)]
struct Ctx {
    f: *mut u32,
    plain: *mut u8,
    ram: *mut u8,
    rom: *const u8,
    frames: *mut Frame,
    nframes: usize,
    cap: usize,
    iters: u64,
    invalid: u32,
    host: *mut u8,
    load: extern "C" fn(*mut Ctx, u32, u32) -> u32,
    store: extern "C" fn(*mut Ctx, u32, u32, u32),
    grow: extern "C" fn(*mut Ctx),
}

struct Host<'a, B> {
    bus: &'a mut B,
    bank: Option<&'a BankMap>,
    frames: *mut Vec<Frame>,
}

extern "C" fn load_fn<B: Bus>(c: *mut Ctx, aux: u32, a: u32) -> u32 {
    // SAFETY: `host` is the live `Host<B>` of the run that passed `c`.
    let h = unsafe { &mut *((*c).host as *mut Host<B>) };
    load(h.bank, h.bus, aux, a as u16) as u32
}

extern "C" fn store_fn<B: Bus>(c: *mut Ctx, aux: u32, a: u32, v: u32) {
    // SAFETY: as in `load_fn`.
    let h = unsafe { &mut *((*c).host as *mut Host<B>) };
    store(h.bank, h.bus, aux, a as u16, v as u8)
}

extern "C" fn grow_fn<B: Bus>(c: *mut Ctx) {
    // SAFETY: as in `load_fn`; the first `nframes` frames are initialized.
    unsafe {
        let c = &mut *c;
        let v = &mut *(*(c.host as *mut Host<B>)).frames;
        v.set_len(c.nframes);
        v.reserve(c.nframes.max(16));
        (c.frames, c.cap) = (v.as_mut_ptr(), v.capacity());
    }
}

type NFn = unsafe extern "C" fn(*mut Ctx, u32) -> u32;

/// The compiled program.
pub struct Jit {
    module: Option<JITModule>,
    funcs: Vec<NFn>,
    /// Per address: the constant code byte, or `0xffff` where there is none.
    _guard: Box<[u16]>,
    /// Compilation wall time.
    pub secs: f64,
    /// Machine code bytes.
    pub bytes: usize,
}

// SAFETY: the finalized code and the guard table are immutable after `Jit::new`.
unsafe impl Send for Jit {}
unsafe impl Sync for Jit {}

impl std::fmt::Debug for Jit {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Jit")
            .field("funcs", &self.funcs.len())
            .field("secs", &self.secs)
            .finish()
    }
}

impl Drop for Jit {
    fn drop(&mut self) {
        if let Some(m) = self.module.take() {
            // SAFETY: no function pointer outlives `self`.
            unsafe { m.free_memory() }
        }
    }
}

const NOGUARD: i64 = 0xffff;

impl Jit {
    pub(crate) fn new(x: &Exec) -> Result<Jit, String> {
        let t = Instant::now();
        let mut fb = settings::builder();
        for (k, v) in [
            ("opt_level", "speed"),
            ("use_colocated_libcalls", "false"),
            ("is_pic", "false"),
        ] {
            fb.set(k, v).map_err(|e| e.to_string())?;
        }
        let isa = cranelift_native::builder()?
            .finish(settings::Flags::new(fb))
            .map_err(|e| e.to_string())?;
        let mut module = JITModule::new(JITBuilder::with_isa(isa, default_libcall_names()));
        let ptr = module.target_config().pointer_type();
        let mut sig = module.make_signature();
        sig.params
            .extend([AbiParam::new(ptr), AbiParam::new(types::I32)]);
        sig.returns.push(AbiParam::new(types::I32));
        let ids = (0..x.fentry.len())
            .map(|_| {
                module
                    .declare_anonymous_function(&sig)
                    .map_err(|e| e.to_string())
            })
            .collect::<Result<Vec<FuncId>, _>>()?;
        let mut guard = vec![NOGUARD as u16; 0x10000].into_boxed_slice();
        for a in x.constcode.ones() {
            guard[a] = x.img[a] as u16;
        }
        let tc = module.target_config();
        let mut ctx = module.make_context();
        let mut fctx = FunctionBuilderContext::new();
        let mut bytes = 0;
        for (fi, &id) in ids.iter().enumerate() {
            ctx.func.signature = sig.clone();
            ctx.func.name = UserFuncName::user(0, id.as_u32());
            let mut b = FunctionBuilder::new(&mut ctx.func, &mut fctx);
            let (_, ops) = span(x, fi);
            let refs: FxHashMap<u32, FuncRef> = x.ops[ops]
                .iter()
                .filter(|o| o.opc == Opc::Call)
                .map(|o| {
                    (
                        o.aux,
                        module.declare_func_in_func(ids[o.aux as usize], b.func),
                    )
                })
                .collect();
            Fb::build(x, fi, &mut b, &refs, ptr, guard.as_ptr());
            b.finalize(tc);
            module
                .define_function(id, &mut ctx)
                .map_err(|e| format!("{e:?}"))?;
            bytes += ctx.compiled_code().map_or(0, |c| c.code_buffer().len());
            module.clear_context(&mut ctx);
        }
        module.finalize_definitions().map_err(|e| e.to_string())?;
        let funcs = ids
            .iter()
            // SAFETY: each id is a finalized function of signature `NFn`.
            .map(|&i| unsafe {
                std::mem::transmute::<*const u8, NFn>(module.get_finalized_function(i))
            })
            .collect();
        Ok(Jit {
            module: Some(module),
            funcs,
            _guard: guard,
            secs: t.elapsed().as_secs_f64(),
            bytes,
        })
    }

    /// Run function `fi` from its entry (its entry checks done) over `run`'s value file;
    /// `flat` is the bus's [`Bus::flat`] memory.
    pub(crate) fn run<B: Bus>(
        &self,
        run: &mut Runner,
        fi: u32,
        (plain, ram): (*mut u8, *mut u8),
        bus: &mut B,
        iters: &mut u64,
    ) -> u32 {
        let bank = run.bank();
        let mut host = Host {
            bus,
            bank,
            frames: &mut run.frames,
        };
        let mut c = Ctx {
            f: run.file().as_mut_ptr(),
            plain,
            ram,
            rom: bank.map_or(std::ptr::null(), |m| m.rom.as_ptr()),
            frames: run.frames.as_mut_ptr(),
            nframes: 0,
            cap: run.frames.capacity(),
            iters: *iters,
            invalid: 0,
            host: &mut host as *mut Host<B> as *mut u8,
            load: load_fn::<B>,
            store: store_fn::<B>,
            grow: grow_fn::<B>,
        };
        // SAFETY: `c` points at memory live for the call, as the compiled code assumes.
        let r = unsafe { (self.funcs[fi as usize])(&mut c, 1) };
        // SAFETY: native code initialized the first `nframes` frames.
        unsafe { run.frames.set_len(c.nframes) };
        run.invalid |= c.invalid != 0;
        *iters = c.iters;
        r
    }
}

/// Builds one function.
struct Fb<'a, 'b> {
    x: &'a Exec,
    b: &'a mut FunctionBuilder<'b>,
    refs: &'a FxHashMap<u32, FuncRef>,
    ptr: types::Type,
    guard: i64,
    vars: FxHashMap<u32, Variable>,
    /// Used and written value-file cells outside the temporaries.
    keep: Vec<(u32, Variable)>,
    dirty: Vec<(u32, Variable)>,
    ctx: Value,
    fp: Value,
    plain: Value,
    ram: Value,
    rom: Value,
    exit: Block,
    body: Vec<Block>,
    entry: Vec<Block>,
    first: usize,
    sigs: [SigRef; 3],
}

const T: MemFlags = MemFlags::trusted();

impl<'a, 'b> Fb<'a, 'b> {
    fn build(
        x: &'a Exec,
        fi: usize,
        b: &'a mut FunctionBuilder<'b>,
        refs: &'a FxHashMap<u32, FuncRef>,
        ptr: types::Type,
        guard: *const u16,
    ) {
        let (blocks, ops) = span(x, fi);
        let (first, end) = (blocks.start, blocks.end);
        let mut used = vec![R::SP as u32];
        let mut written = Vec::new();
        for o in &x.ops[ops] {
            let n = o.opc.arity() + (o.opc == Opc::Load && o.osz == 0) as usize;
            used.extend([o.a, o.b][..n].iter().filter(|&&v| v & IMM == 0));
            if writes(o.opc, o.aux, o.osz) {
                used.push(o.out);
                written.push(o.out);
            }
        }
        used.sort_unstable();
        used.dedup();
        written.sort_unstable();
        written.dedup();
        let vars: FxHashMap<u32, Variable> = used
            .iter()
            .map(|&i| (i, b.declare_var(types::I32)))
            .collect();
        let pick = |s: &[u32]| -> Vec<(u32, Variable)> {
            s.iter()
                .filter(|&&i| i < x.temps)
                .map(|&i| (i, vars[&i]))
                .collect()
        };
        let (keep, dirty) = (pick(&used), pick(&written));
        let start = b.create_block();
        b.append_block_params_for_function_params(start);
        b.switch_to_block(start);
        let (ctx, top) = (b.block_params(start)[0], b.block_params(start)[1]);
        let fld = |b: &mut FunctionBuilder, off: usize| b.ins().load(ptr, T, ctx, off as i32);
        let fp = fld(b, offset_of!(Ctx, f));
        let plain = fld(b, offset_of!(Ctx, plain));
        let ram = fld(b, offset_of!(Ctx, ram));
        let rom = fld(b, offset_of!(Ctx, rom));
        let mut s = |params: &[types::Type], ret: bool| {
            let mut sig = Signature::new(b.func.signature.call_conv);
            sig.params.push(AbiParam::new(ptr));
            sig.params.extend(params.iter().map(|&t| AbiParam::new(t)));
            if ret {
                sig.returns.push(AbiParam::new(types::I32));
            }
            b.import_signature(sig)
        };
        let i = types::I32;
        let sigs = [s(&[i, i], true), s(&[i, i, i], false), s(&[], false)];
        let exit = b.create_block();
        b.append_block_param(exit, types::I32);
        let body: Vec<Block> = (first..end).map(|_| b.create_block()).collect();
        let entry = (first..end)
            .map(|g| {
                let k = x.blocks[g];
                if k.budget || k.check != NOCHECK && k.depth > 0 {
                    b.create_block()
                } else {
                    body[g - first]
                }
            })
            .collect();
        let mut fb = Fb {
            x,
            b,
            refs,
            ptr,
            guard: guard as i64,
            vars,
            keep,
            dirty,
            ctx,
            fp,
            plain,
            ram,
            rom,
            exit,
            body,
            entry,
            first,
            sigs,
        };
        fb.reload();
        let (b0, e0) = (fb.body[0], fb.entry[0]);
        fb.b.ins().brif(top, b0, &[], e0, &[]);
        for g in first..end {
            fb.block(g, end);
        }
        for g in first..end {
            fb.guard_block(g);
        }
        fb.b.switch_to_block(exit);
        fb.flush();
        let code = fb.b.block_params(exit)[0];
        fb.b.ins().return_(&[code]);
        fb.b.seal_all_blocks();
    }

    fn reload(&mut self) {
        for &(i, v) in &self.keep {
            let val = self.b.ins().load(types::I32, T, self.fp, 4 * i as i32);
            self.b.def_var(v, val);
        }
    }

    fn flush(&mut self) {
        for &(i, v) in &self.dirty {
            let val = self.b.use_var(v);
            self.b.ins().store(T, val, self.fp, 4 * i as i32);
        }
    }

    /// A throw-away read: a bus read through `Ctx::load` when it may reach a device and its
    /// predicate holds (memory reads without one have no effect).
    fn throwaway(&mut self, o: &crate::exec::X) {
        if o.aux & (mem::ROM | mem::RAM) != 0 || o.aux & (SPACES | mem::VOLATILE) == 0 {
            return;
        }
        let (rd, cont) = (self.b.create_block(), self.b.create_block());
        match o.b {
            b if b & IMM != 0 && b != IMM => self.b.ins().jump(rd, &[]),
            b if b & IMM != 0 => self.b.ins().jump(cont, &[]),
            b => {
                let c = self.get(b);
                self.b.ins().brif(c, rd, &[], cont, &[])
            }
        };
        self.b.switch_to_block(rd);
        let a = self.addr(o.a);
        let f = self.fnptr(offset_of!(Ctx, load));
        let aux = self.b.ins().iconst(types::I32, o.aux as i64);
        self.b
            .ins()
            .call_indirect(self.sigs[0], f, &[self.ctx, aux, a]);
        self.b.ins().jump(cont, &[]);
        self.b.switch_to_block(cont);
    }

    fn get(&mut self, o: u32) -> Value {
        if o & IMM != 0 {
            self.b.ins().iconst(types::I32, (o & !IMM) as i64)
        } else {
            self.b.use_var(self.vars[&o])
        }
    }

    fn set(&mut self, o: u32, v: Value) {
        self.b.def_var(self.vars[&o], v);
    }

    fn leave(&mut self, kind: i64, pc: Value) {
        let pc = self.b.ins().band_imm_u(pc, 0xffff);
        let code = self.b.ins().bor_imm_u(pc, kind << 16);
        self.b.ins().jump(self.exit, &[BlockArg::Value(code)]);
    }

    /// The pc block `g` deopts to when that is all it does.
    fn deopt_pc(&self, g: u32) -> Option<u16> {
        let x = self.x;
        let (lo, hi) = (
            x.blocks[g as usize].start as usize,
            x.blocks.get(g as usize + 1),
        );
        match &x.ops[lo..hi.map_or(x.ops.len(), |b| b.start as usize)] {
            [o] if o.opc == Opc::CallOther && o.aux == other::DEOPT && o.a & IMM != 0 => {
                Some((o.a & !IMM) as u16)
            }
            _ => None,
        }
    }

    /// Leave with `kind` at `pc` when `c` holds, else continue in `next`.
    fn exit_if(&mut self, c: Value, kind: i64, pc: u16, next: Block) {
        let code = self.b.ins().iconst(types::I32, kind << 16 | pc as i64);
        self.b
            .ins()
            .brif(c, self.exit, &[BlockArg::Value(code)], next, &[]);
    }

    fn target(&self, g: u32) -> Block {
        self.entry[g as usize - self.first]
    }

    /// The block entry's checks: stack depth, then the iteration budget.
    fn guard_block(&mut self, g: usize) {
        let (k, e) = (self.x.blocks[g], self.entry[g - self.first]);
        let body = self.body[g - self.first];
        if e == body {
            return;
        }
        self.b.switch_to_block(e);
        if k.check != NOCHECK && k.depth > 0 {
            let sp = self.b.use_var(self.vars[&(R::SP as u32)]);
            let low = self
                .b
                .ins()
                .icmp_imm_u(IntCC::UnsignedLessThan, sp, k.depth as i64);
            let ok = self.b.create_block();
            self.exit_if(low, 1, k.pc, ok);
            self.b.switch_to_block(ok);
        }
        if k.budget {
            let off = offset_of!(Ctx, iters) as i32;
            let n = self.b.ins().load(types::I64, T, self.ctx, off);
            let (ok, spent) = (
                self.b.create_block(),
                self.b.ins().icmp_imm_u(IntCC::Equal, n, 0),
            );
            self.exit_if(spent, 2, k.pc, ok);
            self.b.switch_to_block(ok);
            let n = self.b.ins().iadd_imm_s(n, -1);
            self.b.ins().store(T, n, self.ctx, off);
        }
        self.b.ins().jump(body, &[]);
    }

    fn block(&mut self, g: usize, end: usize) {
        let x = self.x;
        let ops = x.blocks[g].start as usize
            ..x.blocks
                .get(g + 1)
                .map_or(x.ops.len(), |b| b.start as usize);
        self.b.switch_to_block(self.body[g - self.first]);
        let mut live = true;
        for o in &x.ops[ops] {
            if !live {
                let dead = self.b.create_block();
                self.b.switch_to_block(dead);
            }
            live = self.op(o);
        }
        if live {
            match self.body.get(g + 1 - self.first) {
                Some(&n) if g + 1 < end => {
                    self.b.ins().jump(n, &[]);
                }
                _ => {
                    self.b
                        .ins()
                        .trap(cranelift_codegen::ir::TrapCode::user(1).unwrap());
                }
            }
        }
    }

    fn addr(&mut self, o: u32) -> Value {
        let a = if o & IMM != 0 {
            self.b.ins().iconst(types::I32, (o & 0xffff) as i64)
        } else {
            let v = self.get(o);
            self.b.ins().band_imm_u(v, 0xffff)
        };
        a
    }

    fn at(&mut self, base: Value, a: Value) -> Value {
        let a = self.b.ins().uextend(self.ptr, a);
        self.b.ins().iadd(base, a)
    }

    /// Emit `o`; false when it ends the block.
    fn op(&mut self, o: &crate::exec::X) -> bool {
        let i32t = types::I32;
        match o.opc {
            Opc::Copy | Opc::IntZext => {
                let a = self.get(o.a);
                let v = self.mask(a, o.osz);
                self.set(o.out, v);
            }
            Opc::Load if o.osz == 0 => self.throwaway(o),
            Opc::Load => {
                let a = self.addr(o.a);
                let base = if o.aux & mem::ROM != 0 {
                    Some(self.rom)
                } else if o.aux & mem::RAM != 0 {
                    Some(self.ram)
                } else if o.aux & (SPACES | mem::VOLATILE) == 0 {
                    Some(self.plain)
                } else {
                    None
                };
                let v = match base {
                    Some(base) => {
                        let p = self.at(base, a);
                        let fl = MemFlags::new().with_notrap();
                        match o.osz {
                            2 => self.b.ins().uload16(i32t, fl, p, 0),
                            4 => self.b.ins().load(i32t, fl, p, 0),
                            3 => {
                                let lo = self.b.ins().uload16(i32t, fl, p, 0);
                                let hi = self.b.ins().uload8(i32t, fl, p, 2);
                                let hi = self.b.ins().ishl_imm_u(hi, 16);
                                self.b.ins().bor(lo, hi)
                            }
                            _ => self.b.ins().uload8(i32t, fl, p, 0),
                        }
                    }
                    None => {
                        let f = self.fnptr(offset_of!(Ctx, load));
                        let aux = self.b.ins().iconst(i32t, o.aux as i64);
                        let c = self
                            .b
                            .ins()
                            .call_indirect(self.sigs[0], f, &[self.ctx, aux, a]);
                        self.b.inst_results(c)[0]
                    }
                };
                self.set(o.out, v);
            }
            Opc::Store => {
                let a = self.addr(o.a);
                let v = self.get(o.b);
                let base = if o.aux & mem::RAM != 0 {
                    Some(self.ram)
                } else if o.aux & (SPACES | mem::VOLATILE) == 0 {
                    Some(self.plain)
                } else {
                    None
                };
                match base {
                    Some(base) => {
                        let p = self.at(base, a);
                        let fl = MemFlags::new().with_notrap();
                        match o.osz {
                            2 => {
                                self.b.ins().istore16(fl, v, p, 0);
                            }
                            4 => {
                                self.b.ins().store(fl, v, p, 0);
                            }
                            3 => {
                                self.b.ins().istore16(fl, v, p, 0);
                                let hi = self.b.ins().ushr_imm_u(v, 16);
                                self.b.ins().istore8(fl, hi, p, 2);
                            }
                            _ => {
                                self.b.ins().istore8(fl, v, p, 0);
                            }
                        }
                    }
                    None => {
                        let f = self.fnptr(offset_of!(Ctx, store));
                        let aux = self.b.ins().iconst(i32t, o.aux as i64);
                        self.b
                            .ins()
                            .call_indirect(self.sigs[1], f, &[self.ctx, aux, a, v]);
                    }
                }
            }
            Opc::Branch => {
                let t = self.target(o.aux);
                self.b.ins().jump(t, &[]);
                return false;
            }
            Opc::CBranch => {
                let c = self.get(o.a);
                let (t, next) = (self.target(o.aux), self.b.create_block());
                match self.deopt_pc(o.aux) {
                    Some(pc) => self.exit_if(c, 1, pc, next),
                    None => {
                        self.b.ins().brif(c, t, &[], next, &[]);
                    }
                }
                self.b.switch_to_block(next);
            }
            Opc::BranchInd => {
                let t = self.get(o.a);
                let t = self.b.ins().band_imm_u(t, 0xffff);
                let mut sw = Switch::new();
                for &(a, g) in &self.x.tables[o.aux as usize] {
                    sw.set_entry(a as u128, self.target(g));
                }
                let miss = self.b.create_block();
                sw.emit(self.b, t, miss);
                self.b.switch_to_block(miss);
                self.leave(1, t);
                return false;
            }
            Opc::Call => self.call(o),
            Opc::Return => {
                let pc = self.get(o.a);
                self.leave(0, pc);
                return false;
            }
            Opc::CallOther if o.aux == other::DEOPT => {
                let pc = self.get(o.a);
                self.leave(1, pc);
                return false;
            }
            Opc::CallOther => self.codechk(o),
            opc => {
                let (a, b) = (self.get(o.a), self.get(o.b));
                let v = self.binop(opc, a, b, o.asz);
                let v = self.mask(v, o.osz);
                self.set(o.out, v);
            }
        }
        true
    }

    fn fnptr(&mut self, off: usize) -> Value {
        self.b.ins().load(self.ptr, T, self.ctx, off as i32)
    }

    fn mask(&mut self, v: Value, size: u8) -> Value {
        if size >= 4 {
            v
        } else {
            self.b.ins().band_imm_u(v, mask(size as u32) as i64)
        }
    }

    fn binop(&mut self, opc: Opc, a: Value, b: Value, asz: u8) -> Value {
        let cc = match opc {
            Opc::IntAdd => return self.b.ins().iadd(a, b),
            Opc::IntSub => return self.b.ins().isub(a, b),
            Opc::IntAnd => return self.b.ins().band(a, b),
            Opc::IntOr => return self.b.ins().bor(a, b),
            Opc::IntXor => return self.b.ins().bxor(a, b),
            Opc::IntMult => return self.b.ins().imul(a, b),
            Opc::IntLeft | Opc::IntRight => {
                let s = if opc == Opc::IntLeft {
                    self.b.ins().ishl(a, b)
                } else {
                    self.b.ins().ushr(a, b)
                };
                let ok = self.b.ins().icmp_imm_u(IntCC::UnsignedLessThan, b, 32);
                let z = self.b.ins().iconst(types::I32, 0);
                return self.b.ins().select(ok, s, z);
            }
            Opc::IntEqual => IntCC::Equal,
            Opc::IntNotEqual => IntCC::NotEqual,
            Opc::IntLess => IntCC::UnsignedLessThan,
            Opc::IntLessEqual => IntCC::UnsignedLessThanOrEqual,
            Opc::IntCarry => {
                let a = self.b.ins().uextend(types::I64, a);
                let b = self.b.ins().uextend(types::I64, b);
                let s = self.b.ins().iadd(a, b);
                let m = mask(asz as u32) as i64;
                let c = self.b.ins().icmp_imm_u(IntCC::UnsignedGreaterThan, s, m);
                return self.b.ins().uextend(types::I32, c);
            }
            _ => unreachable!("not a value op"),
        };
        let c = self.b.ins().icmp(cc, a, b);
        self.b.ins().uextend(types::I32, c)
    }

    fn codechk(&mut self, o: &crate::exec::X) {
        let i32t = types::I32;
        let hit = if o.a & IMM != 0 {
            let a = (o.a & !IMM) as usize;
            if a < 0x10000 && self.x.constcode.contains(a) {
                let v = self.get(o.b);
                let c = self
                    .b
                    .ins()
                    .icmp_imm_u(IntCC::NotEqual, v, self.x.img[a] as i64);
                Some(self.b.ins().uextend(i32t, c))
            } else {
                None
            }
        } else {
            let a = self.addr(o.a);
            let a = self.b.ins().ishl_imm_u(a, 1);
            let base = self.b.ins().iconst(self.ptr, self.guard);
            let p = self.at(base, a);
            let g = self.b.ins().uload16(i32t, T.with_readonly(), p, 0);
            let v = self.get(o.b);
            let some = self.b.ins().icmp_imm_u(IntCC::NotEqual, g, NOGUARD);
            let ne = self.b.ins().icmp(IntCC::NotEqual, g, v);
            let c = self.b.ins().band(some, ne);
            Some(self.b.ins().uextend(i32t, c))
        };
        let out = match hit {
            Some(h) => {
                let off = offset_of!(Ctx, invalid) as i32;
                let inv = self.b.ins().load(i32t, T, self.ctx, off);
                let inv = self.b.ins().bor(inv, h);
                self.b.ins().store(T, inv, self.ctx, off);
                h
            }
            None => self.b.ins().iconst(i32t, 0),
        };
        self.set(o.out, out);
    }

    /// A `CALL`: write back, push the frame, call, and either pass a stop up or pop the
    /// frame and reload.
    fn call(&mut self, o: &crate::exec::X) {
        let (i64t, i32t) = (types::I64, types::I32);
        self.flush();
        let (no, co) = (offset_of!(Ctx, nframes) as i32, offset_of!(Ctx, cap) as i32);
        let n = self.b.ins().load(i64t, T, self.ctx, no);
        let cap = self.b.ins().load(i64t, T, self.ctx, co);
        let full = self.b.ins().icmp(IntCC::Equal, n, cap);
        let (grow, push) = (self.b.create_block(), self.b.create_block());
        self.b.ins().brif(full, grow, &[], push, &[]);
        self.b.set_cold_block(grow);
        self.b.switch_to_block(grow);
        let g = self.fnptr(offset_of!(Ctx, grow));
        self.b.ins().call_indirect(self.sigs[2], g, &[self.ctx]);
        self.b.ins().jump(push, &[]);
        self.b.switch_to_block(push);
        let fr = self
            .b
            .ins()
            .load(self.ptr, T, self.ctx, offset_of!(Ctx, frames) as i32);
        let off = self.b.ins().imul_imm_u(n, size_of::<Frame>() as i64);
        let fr = self.b.ins().iadd(fr, off);
        let z = self.b.ins().iconst(i32t, 0);
        self.b.ins().store(T, z, fr, offset_of!(Frame, ip) as i32);
        let callee = self.b.ins().iconst(i32t, o.aux as i64);
        self.b
            .ins()
            .store(T, callee, fr, offset_of!(Frame, callee) as i32);
        let ra = self.get(o.a);
        self.b
            .ins()
            .istore16(T, ra, fr, offset_of!(Frame, ra) as i32);
        let tail = self.get(o.b);
        self.b
            .ins()
            .istore8(T, tail, fr, offset_of!(Frame, tail) as i32);
        let sp = self.b.use_var(self.vars[&(R::SP as u32)]);
        let two = self.b.ins().iconst(i32t, 2);
        let t2 = self.b.ins().ishl_imm_u(tail, 1);
        let up = self.b.ins().isub(two, t2);
        let sp = self.b.ins().iadd(sp, up);
        self.b
            .ins()
            .istore8(T, sp, fr, offset_of!(Frame, sp) as i32);
        let n1 = self.b.ins().iadd_imm_u(n, 1);
        self.b.ins().store(T, n1, self.ctx, no);
        let c = self.b.ins().call(self.refs[&o.aux], &[self.ctx, z]);
        let r = self.b.inst_results(c)[0];
        let stop = self.b.ins().ushr_imm_u(r, 16);
        let (up, back) = (self.b.create_block(), self.b.create_block());
        self.b.ins().brif(stop, up, &[], back, &[]);
        self.b.switch_to_block(up);
        self.b.ins().return_(&[r]);
        self.b.switch_to_block(back);
        self.b.ins().store(T, n, self.ctx, no);
        self.reload();
    }
}

/// Function `fi`'s blocks and ops.
fn span(x: &Exec, fi: usize) -> (std::ops::Range<usize>, std::ops::Range<usize>) {
    let first = x.fentry[fi] as usize;
    let end = x.fentry.get(fi + 1).map_or(x.blocks.len(), |&e| e as usize);
    let op = |b: usize| x.blocks.get(b).map_or(x.ops.len(), |b| b.start as usize);
    (first..end, op(first)..op(end))
}

/// Whether an op of `opc` writes its output.
fn writes(opc: Opc, aux: u32, osz: u8) -> bool {
    match opc {
        Opc::Load => osz != 0,
        Opc::Store | Opc::Branch | Opc::CBranch | Opc::BranchInd | Opc::Call | Opc::Return => false,
        Opc::CallOther => aux != other::DEOPT,
        _ => true,
    }
}
