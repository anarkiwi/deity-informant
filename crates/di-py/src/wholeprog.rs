//! Whole-program lifting (`di_lift`) for Python: `lift_program`, `lift_many`, `Program`
//! and the reference `reference_call` with the same bus.

use di_lift::ir::{mem, other, Program as LProgram};
use di_lift::{Back, Backend, BankMap, Banked, Entry, Exec, Facts, Job, Opts, Outcome, Runner};
use di_pcode::{reg as R, Bus, Cpu, Halt, Lead, Opc, Space, Vn, RETURN};
use indicatif::{ProgressBar, ProgressStyle};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyByteArray, PyBytes, PyDict, PyList};
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// A full-address-space bytearray as the bus: volatile ranges optionally logged as
/// `(addr, value, cycle, is_write)`; the `n`th volatile read gives `reads[n]` when replaying
/// (0 past its end; volatile writes then reach only the log), else `(cycle ^ n ^ addr) & 0xff`
/// with `synth`, else the device byte.
/// With `dev` (banked memory) device accesses reach that register file and `ram_*` the
/// bytearray.
struct PyBus<'a> {
    mem: &'a mut [u8; 0x10000],
    dev: Option<Box<[u8; 0x10000]>>,
    volatile: &'a [(u32, u32)],
    log: Option<Vec<(u16, u8, u64, bool)>>,
    synth: bool,
    reads: Option<&'a [u8]>,
    read_addrs: Option<&'a [u16]>,
    now: u64,
    n: u64,
    r: usize,
    pending: VecDeque<Interrupt>,
    nest: Option<Nest<'a>>,
    retired: Vec<u64>,
    taken: usize,
    boundary_bad: Option<usize>,
}

/// An interrupt entry the machine took inside a call: entry `entry` runs as an interrupt with
/// registers `reg` and `a` after `icount` instructions of the context it interrupts (the call,
/// or the handler `depth` levels down) have retired, and after `pos` volatile accesses of the
/// call (those of the interrupt handlers it took included, in the order the machine made them).
#[derive(Clone, Copy)]
struct Interrupt {
    icount: u64,
    pos: u64,
    depth: usize,
    entry: usize,
    a: u8,
    reg: [u8; 16],
}

/// What runs the pending interrupts of a bus: the lifted program (`exec`) or the reference
/// interpreter (an `Interrupt`'s `entry` is then the entry's address), its return address and
/// budget, and what they left behind.
struct Nest<'a> {
    exec: Option<&'a Exec>,
    bank: Option<&'a BankMap>,
    clocked: bool,
    ret: Option<u16>,
    max_cycles: u64,
    backend: Backend,
    invalid: bool,
    deopts: HashMap<u16, u64>,
    bad: Option<Halt>,
}

impl<'a> PyBus<'a> {
    fn new(mem: &'a mut [u8; 0x10000], volatile: &'a [(u32, u32)], log: bool, synth: bool) -> Self {
        PyBus {
            mem,
            dev: None,
            volatile,
            log: log.then(Vec::new),
            synth,
            reads: None,
            read_addrs: None,
            now: 0,
            n: 0,
            r: 0,
            pending: VecDeque::new(),
            nest: None,
            retired: vec![0],
            taken: 0,
            boundary_bad: None,
        }
    }
    /// The call returned: interrupts taken after its last instruction run, any other not yet
    /// taken never found its boundary.
    fn finish(&mut self, cpu: &mut Cpu) {
        let n = self.retired[0];
        while (self.pending.front()).is_some_and(|e| e.depth == 0 && e.icount == n) {
            self.take(cpu);
        }
        if !self.pending.is_empty() {
            self.boundary_bad.get_or_insert(self.taken);
        }
    }
    /// Takes the front interrupt, its handler running on the lifted program (or the reference
    /// interpreter) as an interrupt with the machine's registers. At an instruction boundary
    /// (`at`) the registers and volatile access count must be the machine's.
    fn take(&mut self, at: &mut Cpu) {
        let (Some(ev), Some(nest)) = (self.pending.pop_front(), self.nest.as_ref()) else {
            return;
        };
        let (exec, max_cycles, ret, bank) = (nest.exec, nest.max_cycles, nest.ret, nest.bank);
        let (clocked, backend, invalid) = (nest.clocked, nest.backend, nest.invalid);
        if ev.pos != self.n || differs(at, &ev.reg) {
            self.boundary_bad.get_or_insert(self.taken);
        }
        self.taken += 1;
        let mut cpu = cpu_of(Some(ev.reg), self.now);
        let inner = self.pending.front().is_some_and(|e| e.depth > ev.depth);
        self.retired.push(0);
        let Some(exec) = exec else {
            let to = (ev.entry as u16, ev.a, ret, true);
            let h = reference_run(&mut cpu, self, bank, clocked, to, max_cycles);
            self.retired.pop();
            if let Some(nest) = self.nest.as_mut() {
                nest.bad = nest.bad.or((h != Halt::Sp).then_some(h));
            }
            cpu.pc = at.pc;
            *at = cpu;
            return;
        };
        let mut run = Runner::new(exec);
        (run.backend, run.invalid, run.precise) = (backend, invalid, inner);
        (run.ret, run.irq) = (ret, true);
        let o = run.call(ev.entry, ev.a, &mut cpu, self, max_cycles);
        self.retired.pop();
        if let Some(nest) = self.nest.as_mut() {
            nest.invalid |= run.invalid;
            for (k, v) in run.deopts {
                *nest.deopts.entry(k).or_default() += v;
            }
            nest.bad = nest.bad.or((o.halt != Halt::Sp).then_some(o.halt));
        }
        cpu.pc = at.pc;
        *at = cpu;
    }
    fn device(&mut self) -> &mut [u8; 0x10000] {
        self.dev.as_deref_mut().unwrap_or(self.mem)
    }
    fn volatile(&self, a: u16) -> bool {
        self.volatile
            .iter()
            .any(|&(lo, hi)| (lo..hi).contains(&(a as u32)))
    }
}

/// Whether the registers of `cpu` (the flags bar B) differ from the register file `reg`.
fn differs(cpu: &Cpu, reg: &[u8; 16]) -> bool {
    let mine = cpu.reg();
    [0, 1, 2, 3, 8, 9, 10, 11, 13, 14]
        .iter()
        .any(|&i| mine[i] != reg[i])
}

impl Bus for PyBus<'_> {
    #[inline]
    fn boundary(&mut self, cpu: &mut Cpu) -> Option<Halt> {
        if self.retired.len() == 1
            && (self.nest.as_ref())
                .is_some_and(|n| n.ret.is_some_and(|r| cpu.pc == r.wrapping_add(1)))
        {
            return Some(Halt::Sp);
        }
        if self.pending.is_empty() {
            return None;
        }
        let d = self.retired.len() - 1;
        let n = self.retired[d];
        while self
            .pending
            .front()
            .is_some_and(|e| e.depth == d && e.icount == n)
        {
            self.take(cpu);
            if let Some(h) = self.nest.as_ref().and_then(|n| n.bad) {
                return Some(h);
            }
        }
        self.retired[d] = n + 1;
        None
    }
    #[inline]
    fn read(&mut self, a: u16) -> u8 {
        if self.volatile(a) {
            self.n += 1;
            let v = if let Some(r) = self.reads {
                self.r += 1;
                r.get(self.r - 1).copied().unwrap_or(0)
            } else if self.synth {
                (self.now ^ self.n) as u8 ^ a as u8
            } else {
                self.device()[a as usize]
            };
            if let Some(l) = &mut self.log {
                l.push((a, v, self.now, false));
            }
            v
        } else {
            self.device()[a as usize]
        }
    }
    #[inline]
    fn write(&mut self, a: u16, v: u8) {
        let vol = self.volatile(a);
        self.n += vol as u64;
        if let (Some(l), true) = (self.log.as_mut(), vol) {
            l.push((a, v, self.now, true));
        }
        if !(vol && self.reads.is_some()) {
            self.device()[a as usize] = v;
        }
    }
    #[inline]
    fn ram_read(&mut self, a: u16) -> u8 {
        self.mem[a as usize]
    }
    #[inline]
    fn ram_write(&mut self, a: u16, v: u8) {
        self.mem[a as usize] = v;
    }
    #[inline]
    fn fetch(&mut self, a: u16) -> u8 {
        self.mem[a as usize]
    }
    fn lead(&mut self, pc: u16) -> Lead {
        let ahead = |k: usize| self.read_addrs?.get(self.r + k).copied();
        match (self.volatile(pc), ahead(0), ahead(1)) {
            (true, Some(a), b) if a == pc.wrapping_add(1) => {
                if b == Some(pc) {
                    Lead::Implied
                } else {
                    Lead::Brk
                }
            }
            _ => Lead::Fetch,
        }
    }
    #[inline]
    fn now(&mut self, c: u64) {
        self.now = c;
    }
    fn flat(&mut self) -> Option<(*mut u8, *mut u8)> {
        let ram = self.mem.as_mut_ptr();
        Some((self.dev.as_mut().map_or(ram, |d| d.as_mut_ptr()), ram))
    }
}

/// A bus that ignores cycles.
struct Unclocked<'a, 'b>(&'a mut PyBus<'b>);

impl Bus for Unclocked<'_, '_> {
    fn read(&mut self, a: u16) -> u8 {
        self.0.read(a)
    }
    fn write(&mut self, a: u16, v: u8) {
        self.0.write(a, v)
    }
    fn fetch(&mut self, a: u16) -> u8 {
        self.0.fetch(a)
    }
    fn lead(&mut self, pc: u16) -> Lead {
        self.0.lead(pc)
    }
    fn boundary(&mut self, cpu: &mut Cpu) -> Option<Halt> {
        self.0.boundary(cpu)
    }
    fn ram_read(&mut self, a: u16) -> u8 {
        self.0.ram_read(a)
    }
    fn ram_write(&mut self, a: u16, v: u8) {
        self.0.ram_write(a, v)
    }
    fn flat(&mut self) -> Option<(*mut u8, *mut u8)> {
        self.0.flat()
    }
}

/// Volatile reads to replay: their values (bytes), or ``(address, value)`` pairs, whose
/// addresses let the interpreter place an opcode fetch among the machine's throw-away reads.
struct Replay {
    vals: Vec<u8>,
    addrs: Option<Vec<u16>>,
}

#[derive(FromPyObject)]
pub enum ReplayArg {
    Values(Vec<u8>),
    Entries(Vec<(u16, u8)>),
}

impl From<ReplayArg> for Replay {
    fn from(a: ReplayArg) -> Self {
        match a {
            ReplayArg::Values(vals) => Replay { vals, addrs: None },
            ReplayArg::Entries(e) => {
                let (addrs, vals) = e.into_iter().unzip();
                Replay {
                    vals,
                    addrs: Some(addrs),
                }
            }
        }
    }
}

impl<'a> PyBus<'a> {
    fn replay(&mut self, r: &'a Option<Replay>) {
        self.reads = r.as_ref().map(|r| &r.vals[..]);
        self.read_addrs = r.as_ref().and_then(|r| r.addrs.as_deref());
    }
}

/// A bank map from its dict: ``dir``, ``data``, ``mask``, ``pull``, ``lut`` (configuration per
/// effective port value), ``configs`` (per configuration 256 page kinds: 0 RAM, 1 ROM,
/// 2 I/O) and ``rom`` (ROM bytes at their addresses over the whole address space).
fn bank_of(d: Option<&Bound<'_, PyDict>>) -> PyResult<Option<BankMap>> {
    let Some(d) = d else { return Ok(None) };
    let get = |k: &str| {
        d.get_item(k)?
            .ok_or_else(|| PyValueError::new_err(format!("bank map lacks {k}")))
    };
    let configs: Vec<Vec<u8>> = get("configs")?.extract()?;
    let configs = configs
        .into_iter()
        .map(|c| {
            let c: [u8; 256] = c
                .try_into()
                .map_err(|_| PyValueError::new_err("a configuration maps 256 pages"))?;
            c.map(|b| match b {
                0 => Ok(Back::Ram),
                1 => Ok(Back::Rom),
                2 => Ok(Back::Io),
                _ => Err(PyValueError::new_err("page kind must be 0, 1 or 2")),
            })
            .into_iter()
            .collect::<PyResult<Vec<Back>>>()
            .map(|v| v.try_into().unwrap())
        })
        .collect::<PyResult<Vec<[Back; 256]>>>()?;
    let lut: Vec<u8> = get("lut")?.extract()?;
    if lut.iter().any(|&c| c as usize >= configs.len()) {
        return Err(PyValueError::new_err("lut names a missing configuration"));
    }
    let mask: u8 = get("mask")?.extract()?;
    if lut.len() <= mask as usize {
        return Err(PyValueError::new_err(
            "lut must cover every masked port value",
        ));
    }
    let rom: Vec<u8> = get("rom")?.extract()?;
    Ok(Some(BankMap {
        dir: get("dir")?.extract()?,
        data: get("data")?.extract()?,
        mask,
        pull: get("pull")?.extract()?,
        lut,
        configs,
        rom: image(&rom)?,
    }))
}

fn image(b: &[u8]) -> PyResult<Box<[u8; 0x10000]>> {
    let v: Box<[u8]> = b.into();
    v.try_into()
        .map_err(|_| PyValueError::new_err("image must be 65536 bytes"))
}

fn facts(d: Option<&Bound<'_, PyDict>>) -> PyResult<Facts> {
    let Some(d) = d else {
        return Ok(Facts::default());
    };
    let get = |k: &str| d.get_item(k);
    let mut f = Facts::default();
    if let Some(s) = get("starts")? {
        f.starts = s.extract()?;
    }
    if let Some(e) = get("edges")? {
        f.edges = e
            .extract::<Vec<[u16; 2]>>()?
            .into_iter()
            .map(|[p, q]| (p, q))
            .collect();
    }
    if let Some(w) = get("writes")? {
        f.writes = match w.cast::<PyDict>() {
            Ok(m) => m.extract::<HashMap<u16, Vec<u8>>>()?.into_iter().collect(),
            Err(_) => w.extract()?,
        };
    }
    if let Some(p) = get("page1")? {
        f.page1 = match p.cast::<PyDict>() {
            Ok(m) => m
                .extract::<HashMap<u16, [u16; 2]>>()?
                .into_iter()
                .map(|(a, [lo, hi])| (a, lo, hi))
                .collect(),
            Err(_) => p.extract()?,
        };
        f.page1.sort_unstable();
    }
    if let Some(h) = get("heights")? {
        f.heights = match h.cast::<PyDict>() {
            Ok(m) => m.extract::<HashMap<u16, Vec<i16>>>()?.into_iter().collect(),
            Err(_) => h.extract()?,
        };
        f.heights.sort_unstable_by_key(|e| e.0);
    }
    if let Some(s) = get("entry_sp")? {
        f.entry_sp = match s.cast::<PyDict>() {
            Ok(m) => m.extract::<HashMap<u16, Vec<u8>>>()?.into_iter().collect(),
            Err(_) => s.extract()?,
        };
        f.entry_sp.sort_unstable_by_key(|e| e.0);
    }
    if let Some(b) = get("banks")? {
        f.banks = match b.cast::<PyDict>() {
            Ok(m) => m.extract::<HashMap<u16, Vec<u8>>>()?.into_iter().collect(),
            Err(_) => b.extract()?,
        };
        f.banks.sort_unstable_by_key(|e| e.0);
    }
    if let Some(p) = get("entry_port")? {
        f.entry_port = match p.cast::<PyDict>() {
            Ok(m) => m
                .extract::<HashMap<u16, Vec<[u8; 2]>>>()?
                .into_iter()
                .map(|(a, v)| (a, v.into_iter().map(|[d, x]| (d, x)).collect()))
                .collect(),
            Err(_) => p.extract()?,
        };
        f.entry_port.sort_unstable_by_key(|e| e.0);
    }
    Ok(f)
}

type PyEntry = (u16, Vec<(usize, Vec<u8>)>);
type PyJob<'py> = (
    Vec<u8>,
    Vec<PyEntry>,
    Vec<(u32, u32)>,
    Option<Bound<'py, PyDict>>,
);

fn entries(e: Vec<PyEntry>) -> Vec<Entry> {
    e.into_iter().map(|(pc, regs)| Entry { pc, regs }).collect()
}

fn vn<'py>(py: Python<'py>, v: Vn) -> PyResult<Bound<'py, PyAny>> {
    if v.is_none() {
        return Ok(py.None().into_bound(py));
    }
    let sp = match v.space() {
        Space::Const => "c",
        Space::Reg => "r",
        Space::Unique => "u",
        Space::Ram => "m",
    };
    Ok(PyList::new(
        py,
        [
            sp.into_pyobject(py)?.into_any(),
            v.off().into_pyobject(py)?.into_any(),
            v.size().into_pyobject(py)?.into_any(),
        ],
    )?
    .into_any())
}

/// A lifted program. ``functions`` lists each function's blocks of P-Code ops as
/// ``[mnemonic, out, [inputs], aux]``; ``call``/``run`` execute it over a full-address-space bytearray.
#[pyclass(module = "deity_informant._native", frozen)]
pub struct Program {
    prog: Arc<LProgram>,
    exec: Arc<Exec>,
    state: std::sync::Mutex<(bool, HashMap<u16, u64>)>,
    interp: AtomicBool,
}

impl Program {
    fn new(p: LProgram) -> Self {
        let exec = Arc::new(Exec::new(&p));
        Program {
            prog: Arc::new(p),
            exec,
            state: Default::default(),
            interp: AtomicBool::new(false),
        }
    }
}

fn backend_of(name: &str) -> PyResult<Backend> {
    match name {
        "jit" => Ok(Backend::Jit),
        "interp" => Ok(Backend::Interp),
        _ => Err(PyValueError::new_err("backend must be 'jit' or 'interp'")),
    }
}

fn halt_name(h: Halt) -> &'static str {
    match h {
        Halt::Sp => "return",
        Halt::Cycles => "cycles",
        Halt::Jam => "jam",
    }
}

fn result<'py>(
    py: Python<'py>,
    cpu: &Cpu,
    halt: Halt,
    deopt: Option<u16>,
    log: Option<Vec<(u16, u8, u64, bool)>>,
) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("reg", cpu.reg().to_vec())?;
    d.set_item("pc", cpu.pc)?;
    d.set_item("cycles", cpu.cycles)?;
    d.set_item("halt", halt_name(halt))?;
    d.set_item("deopt", deopt)?;
    d.set_item("log", log)?;
    Ok(d)
}

/// An interrupt entry as Python gives it: `(icount, pos, entry, a, reg, depth)`; `entry` is the
/// lifted program's entry number, or the entry's address for the reference interpreter.
type NestedArg = (u64, u64, usize, u8, [u8; 16], usize);

fn interrupts_of(nested: Option<Vec<NestedArg>>) -> VecDeque<Interrupt> {
    nested
        .into_iter()
        .flatten()
        .map(|(icount, pos, entry, a, reg, depth)| Interrupt {
            icount,
            pos,
            depth,
            entry,
            a,
            reg,
        })
        .collect()
}

/// The register file of ``a, x, y, sp, p``: the status ``p`` as flags.
fn start_reg(r: &[u8]) -> [u8; 16] {
    let mut c = Cpu::new();
    for (i, &v) in [R::A, R::X, R::Y, R::SP].iter().zip(r) {
        c.set_r(*i, v);
    }
    c.set_status(r[4]);
    c.reg()
}

fn cpu_of(reg: Option<[u8; 16]>, cycles: u64) -> Cpu {
    let mut c = Cpu::new();
    if let Some(r) = reg {
        c.set_reg(r);
    }
    c.cycles = cycles;
    c
}

fn with_mem<R>(
    mem: &Bound<'_, PyByteArray>,
    f: impl FnOnce(&mut [u8; 0x10000]) -> R,
) -> PyResult<R> {
    // SAFETY: the GIL is held and `f` runs no Python code while the slice is borrowed.
    let bytes = unsafe { mem.as_bytes_mut() };
    let img = bytes
        .try_into()
        .map_err(|_| PyValueError::new_err("memory must be a 65536-byte bytearray"))?;
    Ok(f(img))
}

#[pymethods]
impl Program {
    /// Functions: ``{"pc", "entry", "blocks": [{"pc", "ops"}], "tables"}``; an op is
    /// ``[name, out, ins, aux, pc]``, varnodes ``[space, offset, size]``; ``aux`` is the
    /// target block, table, callee, ``CALLOTHER`` name or ``"volatile"``, ``pc`` the
    /// instruction the op was emitted for (0: made by the optimizer).
    #[getter]
    fn functions<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let fs = PyList::empty(py);
        for f in &self.prog.functions {
            let blocks = PyList::empty(py);
            for b in &f.blocks {
                let ops = PyList::empty(py);
                for op in &b.ops {
                    let ins = PyList::empty(py);
                    for &v in op.inputs() {
                        ins.append(vn(py, v)?)?;
                    }
                    let (name, aux): (&str, Bound<'py, PyAny>) = match op.opc {
                        Opc::CallOther => (
                            op.opc.name(),
                            other::NAMES[op.aux as usize].into_pyobject(py)?.into_any(),
                        ),
                        Opc::Load | Opc::Store => (
                            op.opc.name(),
                            if op.aux & mem::VOLATILE != 0 {
                                "volatile".into_pyobject(py)?.into_any()
                            } else {
                                py.None().into_bound(py)
                            },
                        ),
                        Opc::Branch | Opc::CBranch | Opc::BranchInd | Opc::Call => {
                            (op.opc.name(), op.aux.into_pyobject(py)?.into_any())
                        }
                        _ => (op.opc.name(), py.None().into_bound(py)),
                    };
                    ops.append(PyList::new(
                        py,
                        [
                            name.into_pyobject(py)?.into_any(),
                            vn(py, op.out)?,
                            ins.into_any(),
                            aux,
                            op.pc.into_pyobject(py)?.into_any(),
                        ],
                    )?)?;
                }
                let d = PyDict::new(py);
                d.set_item("pc", b.pc)?;
                d.set_item("ops", ops)?;
                blocks.append(d)?;
            }
            let d = PyDict::new(py);
            d.set_item("pc", f.pc)?;
            d.set_item("entry", f.top)?;
            d.set_item("recursive", f.recursive)?;
            d.set_item("copies", f.copies.clone())?;
            d.set_item("copy_pcs", f.copy_pcs.clone())?;
            d.set_item("blocks", blocks)?;
            d.set_item("tables", f.tables.clone())?;
            d.set_item("closed", f.closed.ones().collect::<Vec<_>>())?;
            fs.append(d)?;
        }
        Ok(fs)
    }

    /// Lift counts: instructions, raw template ops, optimized ops, cells, guards, calls.
    #[getter]
    fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let s = &self.prog.stats;
        let d = PyDict::new(py);
        d.set_item("insns", s.insns)?;
        d.set_item("raw_ops", s.raw_ops)?;
        d.set_item("emitted_ops", s.emitted_ops)?;
        d.set_item("ops", s.ops)?;
        d.set_item("blocks", s.blocks)?;
        d.set_item("functions", self.prog.functions.len())?;
        d.set_item("cells", s.cells)?;
        d.set_item("opcode_cells", s.opcode_cells)?;
        d.set_item("code_bytes", s.code_bytes)?;
        d.set_item("code_guards", s.code_guards)?;
        d.set_item("calls", s.calls)?;
        d.set_item("instances", s.instances)?;
        d.set_item("instance_copies", s.instance_copies)?;
        d.set_item("stack_private", s.stack_private)?;
        d.set_item("stack_guards", s.stack_guards)?;
        d.set_item("ptr_guards", s.ptr_guards)?;
        d.set_item("zone_guards", s.zone_guards)?;
        d.set_item("guard_evals", self.exec.guard_evals())?;
        d.set_item("page1_sites", s.page1_sites)?;
        d.set_item("slot_ops", s.slot_ops)?;
        d.set_item("stack_ops", s.stack_ops)?;
        d.set_item("unreached", s.unreached)?;
        d.set_item("unknown_heights", s.unknown_heights)?;
        d.set_item("recursive", s.recursive)?;
        d.set_item("split_nodes", s.split_nodes)?;
        d.set_item("sp_dispatch", s.sp_dispatch)?;
        d.set_item("sp_guards", s.sp_guards)?;
        d.set_item("bank_dispatch", s.bank_dispatch)?;
        d.set_item("kept_bytes", s.kept_bytes)?;
        d.set_item("budget_checks", s.budget_checks)?;
        d.set_item("chains", s.chains)?;
        d.set_item("fused_chains", s.fused_chains)?;
        Ok(d)
    }

    /// Multi-byte carry chains: ``{"function", "pc", "kind", "fused", "x", "y", "r"}`` with
    /// ``kind`` ``add``/``sub``/``shl``/``shr``, ``pc`` the start of the instruction block, and
    /// per byte (least significant first) of each operand and the stored result its place:
    /// ``["abs", addr]``, ``["x"|"y", base]`` (indexed), ``["v", base, op]`` (indexed by the
    /// value of the block's op ``op``) or ``None``.
    #[getter]
    fn chains<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        use di_lift::wide::{Kind, Place};
        let place = |p: &Place| -> PyResult<Bound<'py, PyAny>> {
            Ok(match *p {
                Place::Abs(a) => ("abs", a).into_pyobject(py)?.into_any(),
                Place::Indexed(r, a) => (["a", "x", "y"][r as usize], a)
                    .into_pyobject(py)?
                    .into_any(),
                Place::Value(i, a) => ("v", a, i).into_pyobject(py)?.into_any(),
                Place::Other => py.None().into_bound(py),
            })
        };
        let out = PyList::empty(py);
        for (fi, c) in &self.prog.chains {
            let d = PyDict::new(py);
            d.set_item("function", fi)?;
            d.set_item("pc", c.pc)?;
            let kind = match c.kind {
                Kind::Add => "add",
                Kind::Sub => "sub",
                Kind::Shl => "shl",
                Kind::Shr => "shr",
            };
            d.set_item("kind", kind)?;
            d.set_item("fused", c.fused)?;
            for (name, ps) in [("x", &c.x), ("y", &c.y), ("r", &c.r)] {
                let l = PyList::empty(py);
                for p in ps {
                    l.append(place(p)?)?;
                }
                d.set_item(name, l)?;
            }
            out.append(d)?;
        }
        Ok(out)
    }

    /// Induction variables of the loops: ``{"head", "reg", "addr", "lo", "hi", "step",
    /// "trips"}`` with ``reg`` ``a``/``x``/``y`` (``addr`` the byte's address otherwise),
    /// ``lo``/``hi`` its interval entering the loop, ``step`` its signed change per iteration
    /// and ``trips`` the most head visits per entry (``None``: unbounded).
    #[getter]
    fn induction<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        use di_lift::scev::Place;
        let out = PyList::empty(py);
        let mut ivs: Vec<_> = self.prog.induction.iter().collect();
        ivs.sort_by_key(|iv| (iv.head, iv.var));
        for iv in ivs {
            let d = PyDict::new(py);
            d.set_item("head", iv.head)?;
            let (r, a) = match iv.var {
                Place::Reg(r) => (Some(["a", "x", "y"][r as usize]), None),
                Place::Mem(a) => (None, Some(a)),
            };
            d.set_item("reg", r)?;
            d.set_item("addr", a)?;
            d.set_item("lo", iv.start.lo)?;
            d.set_item("hi", iv.start.hi)?;
            d.set_item("step", iv.step as i8)?;
            d.set_item("trips", iv.trips)?;
            out.append(d)?;
        }
        Ok(out)
    }

    /// Whether the program counts cycles.
    #[getter]
    fn cycles(&self) -> bool {
        self.prog.cycles
    }

    /// The code bytes the program treats as constant, ascending.
    #[getter]
    fn code(&self) -> Vec<u16> {
        self.prog.code.iter().map(|e| e.0).collect()
    }

    /// The closed cells' value sets: ``{address: [value, ...]}``.
    #[getter]
    fn sets(&self) -> HashMap<u16, Vec<u8>> {
        self.prog.sets.iter().cloned().collect()
    }

    /// The instruction starts the functions plan, ascending.
    #[getter]
    fn starts(&self) -> Vec<u16> {
        self.prog.starts.clone()
    }

    /// Runs ``[(lo, hi)]`` (``hi`` exclusive) of the bytes stores with address sets may write.
    #[getter]
    fn written(&self) -> Vec<(u32, u32)> {
        self.prog.written.clone()
    }

    /// Address sets of data accesses through pointers or with several addresses:
    /// ``{(pc, store): [(lo, hi), ...]}`` runs, ``hi`` exclusive.
    #[getter]
    fn addr_sets(&self) -> HashMap<(u16, bool), Vec<(u32, u32)>> {
        self.prog
            .addr_sets
            .iter()
            .map(|(pc, st, r)| ((*pc, *st), r.clone()))
            .collect()
    }

    /// Bytes every store into which has an exact address and a bounded value:
    /// ``{address: (lo, hi)}``, the hull of the entry byte and the stored values.
    #[getter]
    fn bounds(&self) -> HashMap<u16, (u8, u8)> {
        self.prog
            .bounds
            .iter()
            .map(|&(a, lo, hi)| (a, (lo, hi)))
            .collect()
    }

    /// The computed transfers whose targets come from the code: ``{pc: [target, ...]}``.
    #[getter]
    fn jumps(&self) -> HashMap<u16, Vec<u16>> {
        self.prog.jumps.iter().cloned().collect()
    }

    /// Entry PCs in request order.
    #[getter]
    fn entries(&self) -> Vec<u16> {
        self.prog.entries.iter().map(|e| e.0).collect()
    }

    /// The engine ``call`` runs lifted code on by default: ``"jit"`` (native code without
    /// cycle counting, the interpreter otherwise) or ``"interp"``.
    #[getter]
    fn backend(&self) -> &'static str {
        if self.interp.load(Ordering::Relaxed) {
            "interp"
        } else {
            "jit"
        }
    }
    #[setter]
    fn set_backend(&self, name: &str) -> PyResult<()> {
        let b = backend_of(name)?;
        self.interp.store(b == Backend::Interp, Ordering::Relaxed);
        Ok(())
    }

    /// Compile the native code now (otherwise on the first ``"jit"`` call): its compile
    /// seconds, or None when there is none (programs counting cycles).
    fn compile(&self, py: Python<'_>) -> Option<f64> {
        py.detach(|| self.exec.jit().map(|j| j.secs))
    }

    /// Guards the interpreter backend has evaluated so far (conditional branches to a deopt).
    #[getter]
    fn guard_evals(&self) -> u64 {
        self.exec.guard_evals()
    }

    /// Deopts so far per PC.
    #[getter]
    fn deopts(&self) -> HashMap<u16, u64> {
        self.state.lock().unwrap().1.clone()
    }

    /// ``run_to_rts`` of entry ``entry`` with ``A = a``: push the return address (``ret``,
    /// else ``$0001``), run until it returns (SP above its start; with ``ret``, at ``ret + 1``
    /// as a host trapping that address sees it), ``max_cycles`` elapse, or a JAM.
    /// ``reg``/``cycles`` set the starting state.
    /// Returns ``{"reg", "pc", "cycles", "halt", "deopt", "log"}``. Without cycle counting
    /// the lifted code runs to its return and ``cycles`` counts only the reference
    /// interpreter's part after a deopt. A banked program's non-volatile devices read and
    /// write a copy of ``mem``; ``reads`` (bytes) replays the volatile reads in order.
    /// ``backend`` overrides :attr:`backend` for this call. With ``irq`` the call enters as
    /// an interrupt: it pushes the PC ``ret + 1``, the status (B clear) and sets I, and returns
    /// by ``RTI`` to ``ret + 1``. ``nested`` lists the interrupt entries the machine took inside
    /// the call in its order, ``(icount, pos, entry, a, reg, depth)``: entry ``entry`` runs as
    /// an interrupt (as ``irq``, returning to ``ret + 1``) with registers ``reg`` and ``A = a``
    /// at the boundary after ``icount`` instructions of the context it interrupts (``depth``
    /// 0: the call, ``n``: the handler of the last interrupt taken at depth ``n - 1``) have
    /// retired. A call with interrupts runs on the reference interpreter, instruction by
    /// instruction, and its handlers on the lifted program (a handler interrupted in turn
    /// on the reference too). The result's ``boundary`` is None, or the index of the first
    /// interrupt whose boundary was not the machine's: ``reg`` differs from the registers the
    /// interpreter has there or ``pos`` from the volatile accesses it has made (those of the
    /// handlers it ran included), or it never came. A handler that does not return ends the
    /// call with its halt.
    #[pyo3(signature = (mem, entry=0, a=0, reg=None, cycles=0, max_cycles=10_000_000, log=false, synth=false, reads=None, backend=None, ret=None, irq=false, nested=None))]
    #[allow(clippy::too_many_arguments)]
    fn call<'py>(
        &self,
        py: Python<'py>,
        mem: &Bound<'py, PyByteArray>,
        entry: usize,
        a: u8,
        reg: Option<[u8; 16]>,
        cycles: u64,
        max_cycles: u64,
        log: bool,
        synth: bool,
        reads: Option<ReplayArg>,
        backend: Option<&str>,
        ret: Option<u16>,
        irq: bool,
        nested: Option<Vec<NestedArg>>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let backend = self.backend_or(backend)?;
        self.entry(entry)?;
        for e in nested.iter().flatten() {
            self.entry(e.2)?;
        }
        let reads: Option<Replay> = reads.map(Into::into);
        let mut cpu = cpu_of(reg, cycles);
        let dev = devices(self.prog.bank.is_some(), mem)?;
        let (o, lg, bad) = with_mem(mem, |m| {
            let mut bus = PyBus::new(m, &self.prog.volatile, log, synth);
            bus.dev = dev;
            bus.replay(&reads);
            bus.pending = interrupts_of(nested);
            let o = self.run(
                &mut bus,
                &mut cpu,
                (entry, a, ret, irq),
                max_cycles,
                backend,
            );
            (o, bus.log, bus.boundary_bad)
        })?;
        let d = result(py, &cpu, o.halt, o.deopt, lg)?;
        d.set_item("boundary", bad)?;
        Ok(d)
    }

    /// ``deity_informant.c64.check`` over packed machine records (``vagrant_moses.smc.Records``
    /// fields): entry 0 runs once per record from ``first`` on, from ``mem`` and ``reg``, with A
    /// from ``accs`` (0 past its end), ``port`` (when given) stored to $01 first and the
    /// record's device reads replayed; the machine's RAM is the records' ``origin`` plus each
    /// record's changes (``mem`` when there are none); ``ret`` as :meth:`call`. ``(bad, reg)``:
    /// ``[call, *mismatch]`` of the first record the lift differs from or reaches the halt of,
    /// or None, and the lift's registers after its last call, so the records of one run checked
    /// in pieces check as one.
    ///
    /// With ``starts`` (7 bytes per record: entry u16, then a, x, y, sp, p) each record is an
    /// interrupt entered as :meth:`call` with ``irq`` from those registers (``reg`` and ``accs``
    /// are unused; page 1 is not compared, the interrupted code's stack being its own), and ``nested`` (20 bytes per interrupt taken inside a record:
    /// record u32, icount u32, pos u32, entry u16, a, x, y, sp, p, depth, in record order)
    /// lists the entries taken inside it as :meth:`call`'s ``nested`` does.
    #[pyo3(signature = (mem, reg, records, first, port=None, max_cycles=10_000_000, accs=None, ret=None, starts=None, nested=None))]
    #[allow(clippy::too_many_arguments)]
    fn check<'py>(
        &self,
        py: Python<'py>,
        mem: &Bound<'py, PyByteArray>,
        reg: [u8; 16],
        records: Records<'py>,
        first: usize,
        port: Option<u8>,
        max_cycles: u64,
        accs: Option<Vec<u8>>,
        ret: Option<u16>,
        starts: Option<Vec<u8>>,
        nested: Option<Vec<u8>>,
    ) -> PyResult<(Option<Bound<'py, PyList>>, [u8; 16])> {
        let backend = self.backend_or(None)?;
        let r = records.view()?;
        let starts = starts.as_deref().map(|b| b.as_chunks::<7>().0);
        if starts.is_some_and(|s| s.len() != r.len()) {
            return Err(PyValueError::new_err("one start per record"));
        }
        let mut events = nested
            .as_deref()
            .unwrap_or_default()
            .as_chunks::<20>()
            .0
            .iter();
        let mut event = events.next();
        let entries = starts.into_iter().flatten().map(|s| [s[0], s[1]]);
        let entries = entries.chain(events.clone().map(|e| [e[12], e[13]]));
        for e in entries.chain(event.map(|e| [e[12], e[13]])) {
            self.entry(u16::from_le_bytes(e) as usize)?;
        }
        let banked = self.prog.bank.is_some();
        let origin = <&[u8; 0x10000]>::try_from(records.origin.as_bytes()).ok();
        let mut reg = reg;
        let bad = with_mem(mem, |m| {
            let mut refm = Box::new(*origin.unwrap_or(m));
            for k in 0..first.min(r.len()) {
                for (a, v) in r.delta(k) {
                    refm[a as usize] = v;
                }
            }
            let (mut vals, mut addrs) = (Vec::new(), Vec::new());
            for k in first..r.len() {
                let n = k - first;
                for (a, v) in r.delta(k) {
                    refm[a as usize] = v;
                }
                if let Some(p) = port {
                    m[1] = p;
                }
                let a = accs.as_ref().and_then(|v| v.get(n).copied()).unwrap_or(0);
                vals.clear();
                addrs.clear();
                for (a, v, _) in r.log(k).filter(|e| !e.2) {
                    vals.push(v);
                    addrs.push(a);
                }
                let (entry, a, irq) = match starts {
                    Some(s) => {
                        let s = &s[k];
                        reg = start_reg(&s[2..7]);
                        (u16::from_le_bytes([s[0], s[1]]) as usize, s[2], true)
                    }
                    None => (0, a, false),
                };
                let mut cpu = cpu_of(Some(reg), 0);
                let mut events_of_k = VecDeque::new();
                let dev = banked.then(|| Box::new(*m));
                let mut bus = PyBus::new(m, &self.prog.volatile, true, false);
                bus.dev = dev;
                (bus.reads, bus.read_addrs) = (Some(&vals[..]), Some(&addrs[..]));
                while let Some(e) =
                    event.filter(|e| u32::from_le_bytes([e[0], e[1], e[2], e[3]]) as usize <= k)
                {
                    if u32::from_le_bytes([e[0], e[1], e[2], e[3]]) as usize == k {
                        events_of_k.push_back(Interrupt {
                            icount: u32::from_le_bytes([e[4], e[5], e[6], e[7]]) as u64,
                            pos: u32::from_le_bytes([e[8], e[9], e[10], e[11]]) as u64,
                            entry: u16::from_le_bytes([e[12], e[13]]) as usize,
                            a: e[14],
                            reg: start_reg(&e[14..19]),
                            depth: e[19] as usize,
                        });
                    }
                    event = events.next();
                }
                bus.pending = std::mem::take(&mut events_of_k);
                let o = self.run(
                    &mut bus,
                    &mut cpu,
                    (entry, a, ret, irq),
                    max_cycles,
                    backend,
                );
                let out = bus.log.take().unwrap_or_default();
                if let Some(i) = bus.boundary_bad {
                    return Some((n, Bad::Boundary(("boundary", i))));
                }
                if let Some(bad) = mismatch(
                    &r,
                    k,
                    (o.halt, starts.is_some()),
                    &out,
                    &cpu.reg(),
                    m,
                    &refm,
                ) {
                    return Some((n, bad));
                }
                reg = cpu.reg();
            }
            None
        })?;
        let bad = bad
            .map(|(n, b)| {
                let l = PyList::empty(py);
                l.append(n)?;
                for x in b.into_pyobject(py)?.try_iter()? {
                    l.append(x?)?;
                }
                Ok::<_, PyErr>(l)
            })
            .transpose()?;
        Ok((bad, reg))
    }

    /// A :class:`Session` over ``mem``: repeated ``call``s from one CPU state (``reg``),
    /// cheaper than ``Program.call`` per call. A banked program's devices start as a copy of
    /// ``mem`` and persist across the session's calls.
    #[pyo3(signature = (mem, max_cycles=10_000_000, reg=None, backend=None))]
    fn session(
        slf: &Bound<'_, Self>,
        mem: &Bound<'_, PyByteArray>,
        max_cycles: u64,
        reg: Option<[u8; 16]>,
        backend: Option<&str>,
    ) -> PyResult<Session> {
        let p = slf.get();
        with_mem(mem, |_| ())?;
        Ok(Session {
            prog: slf.clone().unbind(),
            mem: mem.clone().unbind(),
            dev: devices(p.prog.bank.is_some(), mem)?,
            cpu: cpu_of(reg, 0),
            max_cycles,
            backend: p.backend_or(backend)?,
        })
    }

    /// True when every byte the program treats as constant code holds its lifted value in
    /// ``mem`` (a host that writes code between calls checks this or re-lifts).
    fn matches(&self, mem: &Bound<'_, PyByteArray>) -> PyResult<bool> {
        with_mem(mem, |m| self.exec.matches(m))
    }

    /// The program as text.
    fn __str__(&self) -> String {
        self.prog.to_string()
    }
}

impl Program {
    fn backend_or(&self, name: Option<&str>) -> PyResult<Backend> {
        match name {
            Some(b) => backend_of(b),
            None if self.interp.load(Ordering::Relaxed) => Ok(Backend::Interp),
            None => Ok(Backend::Jit),
        }
    }
    fn entry(&self, entry: usize) -> PyResult<()> {
        if entry >= self.prog.entries.len() {
            return Err(PyValueError::new_err("no such entry"));
        }
        Ok(())
    }
    /// ``run_to_rts`` of `entry` with A `a` and return address `ret` over `bus` from `cpu`,
    /// keeping the invalidation and deopts.
    fn run<'a>(
        &'a self,
        bus: &mut PyBus<'a>,
        cpu: &mut Cpu,
        (entry, a, ret, irq): (usize, u8, Option<u16>, bool),
        max_cycles: u64,
        backend: Backend,
    ) -> Outcome {
        bus.now = cpu.cycles;
        let invalid = self.state.lock().unwrap().0;
        let mut run = Runner::new(&self.exec);
        run.backend = backend;
        run.invalid = invalid;
        run.ret = ret;
        run.irq = irq;
        run.precise = !bus.pending.is_empty();
        if !bus.pending.is_empty() {
            bus.nest = Some(Nest {
                exec: Some(&self.exec),
                bank: None,
                clocked: false,
                ret,
                max_cycles,
                backend,
                invalid,
                deopts: HashMap::new(),
                bad: None,
            });
        }
        let mut o = run.call(entry, a, cpu, bus, max_cycles);
        if o.halt == Halt::Sp {
            bus.finish(cpu);
        }
        let mut st = self.state.lock().unwrap();
        st.0 = run.invalid;
        for (k, v) in run.deopts {
            *st.1.entry(k).or_default() += v;
        }
        if let Some(nest) = bus.nest.take() {
            st.0 |= nest.invalid;
            for (k, v) in nest.deopts {
                *st.1.entry(k).or_default() += v;
            }
            if let (Some(h), Halt::Sp) = (nest.bad, o.halt) {
                o.halt = h;
            }
        }
        o
    }
}

/// ``Program.session``: ``run_to_rts`` calls on one memory and CPU state.
#[pyclass(module = "deity_informant._native")]
pub struct Session {
    prog: Py<Program>,
    mem: Py<PyByteArray>,
    dev: Option<Box<[u8; 0x10000]>>,
    cpu: Cpu,
    max_cycles: u64,
    backend: Backend,
}

#[pymethods]
impl Session {
    /// ``run_to_rts`` of entry ``entry`` with ``A = a``; the halt (``return``, ``cycles``
    /// or ``jam``).
    #[pyo3(signature = (entry=0, a=0))]
    fn call(&mut self, py: Python<'_>, entry: usize, a: u8) -> PyResult<&'static str> {
        let p = self.prog.get();
        p.entry(entry)?;
        let (cpu, dev, mc, b) = (&mut self.cpu, &mut self.dev, self.max_cycles, self.backend);
        let o = with_mem(self.mem.bind(py), |m| {
            let mut bus = PyBus::new(m, &p.prog.volatile, false, false);
            bus.dev = dev.take();
            let o = p.run(&mut bus, cpu, (entry, a, None, false), mc, b);
            *dev = bus.dev;
            o
        })?;
        Ok(halt_name(o.halt))
    }
    /// The registers.
    #[getter]
    fn reg(&self) -> Vec<u8> {
        self.cpu.reg().to_vec()
    }
}

/// The device register file of a banked run: a copy of ``mem``.
fn devices(banked: bool, mem: &Bound<'_, PyByteArray>) -> PyResult<Option<Box<[u8; 0x10000]>>> {
    if !banked {
        return Ok(None);
    }
    with_mem(mem, |d| Some(Box::new(*d)))
}

/// ``run_to_rts`` on the reference interpreter with the same bus as ``Program.call``;
/// ``clocked=False`` gives volatile accesses no cycle, as a program lifted without cycles.
/// ``bank`` (a bank map dict, see ``lift_program``) sees memory through it; ``reads`` as
/// ``Program.call``, ``ret`` and ``irq`` as ``Program.call``.
#[pyfunction]
#[pyo3(signature = (mem, pc, a=0, reg=None, cycles=0, max_cycles=10_000_000, volatile=vec![], log=false, synth=false, clocked=true, bank=None, reads=None, ret=None, irq=false, nested=None))]
#[allow(clippy::too_many_arguments)]
pub fn reference_call<'py>(
    py: Python<'py>,
    mem: &Bound<'py, PyByteArray>,
    pc: u16,
    a: u8,
    reg: Option<[u8; 16]>,
    cycles: u64,
    max_cycles: u64,
    volatile: Vec<(u32, u32)>,
    log: bool,
    synth: bool,
    clocked: bool,
    bank: Option<&Bound<'py, PyDict>>,
    reads: Option<ReplayArg>,
    ret: Option<u16>,
    irq: bool,
    nested: Option<Vec<NestedArg>>,
) -> PyResult<Bound<'py, PyDict>> {
    let reads: Option<Replay> = reads.map(Into::into);
    let mut cpu = cpu_of(reg, cycles);
    let bank = bank_of(bank)?;
    let banked = bank.is_some();
    let (h, lg, bad) = with_mem(mem, |m| {
        let mut bus = PyBus::new(m, &volatile, log, synth);
        bus.now = cycles;
        bus.dev = banked.then(|| Box::new(*bus.mem));
        bus.replay(&reads);
        bus.pending = interrupts_of(nested);
        if !bus.pending.is_empty() {
            bus.nest = Some(Nest {
                exec: None,
                bank: bank.as_ref(),
                clocked,
                ret,
                max_cycles,
                backend: Backend::Interp,
                invalid: false,
                deopts: HashMap::new(),
                bad: None,
            });
        }
        let mut h = reference_run(
            &mut cpu,
            &mut bus,
            bank.as_ref(),
            clocked,
            (pc, a, ret, irq),
            max_cycles,
        );
        if h == Halt::Sp {
            bus.finish(&mut cpu);
        }
        h = bus
            .nest
            .take()
            .and_then(|n| n.bad)
            .filter(|_| h == Halt::Sp)
            .unwrap_or(h);
        (h, bus.log, bus.boundary_bad)
    })?;
    let d = result(py, &cpu, h, None, lg)?;
    d.set_item("boundary", bad)?;
    Ok(d)
}

/// ``run_to_rts`` of ``pc`` with ``A = a`` and return address ``ret`` (as ``Program.call``)
/// on the reference interpreter over ``bus``.
fn reference_run(
    cpu: &mut Cpu,
    bus: &mut PyBus<'_>,
    bank: Option<&BankMap>,
    clocked: bool,
    (pc, a, ret, irq): (u16, u8, Option<u16>, bool),
    max_cycles: u64,
) -> Halt {
    let start = cpu.r(R::SP);
    cpu.push_frame(bus, ret.unwrap_or(RETURN), irq);
    cpu.set_r(R::A, a);
    cpu.pc = pc;
    let end = cpu.cycles.saturating_add(max_cycles).saturating_add(1);
    let above = start as i32 - 1;
    match (bank, clocked) {
        (Some(b), true) => cpu.run_clocked(&mut Banked::new(b, bus), above, end, ret),
        (Some(b), false) => {
            cpu.run_clocked(&mut Banked::new(b, &mut Unclocked(bus)), above, end, ret)
        }
        (None, true) => cpu.run_clocked(bus, above, end, ret),
        (None, false) => cpu.run_clocked(&mut Unclocked(bus), above, end, ret),
    }
}

/// ``n`` :func:`reference_call` s of ``pc`` (A 0) on ``mem``, each from the registers the last
/// left (the first from ``reg``) and ``port`` (when given) stored to $01 before it:
/// ``(calls, cycles, halt)``, stopping after the first that does not return.
#[pyfunction]
#[pyo3(signature = (mem, pc, n, reg=None, max_cycles=10_000_000, volatile=vec![], bank=None, port=None))]
#[allow(clippy::too_many_arguments)]
pub fn reference_calls(
    mem: &Bound<'_, PyByteArray>,
    pc: u16,
    n: u64,
    reg: Option<[u8; 16]>,
    max_cycles: u64,
    volatile: Vec<(u32, u32)>,
    bank: Option<&Bound<'_, PyDict>>,
    port: Option<u8>,
) -> PyResult<(u64, u64, &'static str)> {
    let bank = bank_of(bank)?;
    with_mem(mem, |m| {
        let (mut reg, mut cycles) = (reg, 0);
        for i in 1..=n {
            if let Some(p) = port {
                m[1] = p;
            }
            let mut cpu = cpu_of(reg, 0);
            let dev = bank.is_some().then(|| Box::new(*m));
            let mut bus = PyBus::new(m, &volatile, false, false);
            bus.dev = dev;
            let h = reference_run(
                &mut cpu,
                &mut bus,
                bank.as_ref(),
                true,
                (pc, 0, None, false),
                max_cycles,
            );
            (reg, cycles) = (Some(cpu.reg()), cycles + cpu.cycles);
            if h != Halt::Sp {
                return (i, cycles, halt_name(h));
            }
        }
        (n, cycles, "return")
    })
}

/// Lift ``entries`` of the program in ``image`` (the whole address space at entry): ``entries`` as
/// ``[(pc, [(reg, values)])]``, ``volatile`` as ``[(lo, hi)]``, ``facts`` as a dict with
/// ``starts``, ``edges``, ``writes``, ``page1`` and ``heights``; ``optimize=False`` keeps the emitted
/// P-Code; ``cycles=True`` counts cycles; ``entry_sp`` declares the SP every entry is called
/// at (a calling convention: pointer windows also keep pointers out of the volatile pages,
/// stack checks against the stack's extent); ``sensitive`` the volatile addresses whose reads
/// have effects (a throw-away read reaching none of them is not made);
/// ``loaded`` the ``(lo, hi)`` the program was loaded to, where tables end at the latest;
/// ``fold=True`` lifts copies of one routine as one function of the copy's index.
/// ``bank`` is a bank map: ``dir``/``data`` port
/// cells, ``mask`` and ``pull`` bits, ``lut`` (configuration per effective port value),
/// ``configs`` (per configuration 256 page kinds: 0 RAM, 1 ROM, 2 I/O) and ``rom``.
#[pyfunction]
#[pyo3(signature = (image, entries, volatile=vec![], facts=None, optimize=true, cycles=false, bank=None, entry_sp=None, loaded=None, fold=false, sensitive=None))]
#[allow(clippy::too_many_arguments)]
pub fn lift_program(
    py: Python<'_>,
    image: &[u8],
    entries: Vec<PyEntry>,
    volatile: Vec<(u32, u32)>,
    facts: Option<&Bound<'_, PyDict>>,
    optimize: bool,
    cycles: bool,
    bank: Option<&Bound<'_, PyDict>>,
    entry_sp: Option<u8>,
    loaded: Option<(u32, u32)>,
    fold: bool,
    sensitive: Option<Vec<u16>>,
) -> PyResult<Program> {
    let img = self::image(image)?;
    let es = self::entries(entries);
    let f = self::facts(facts)?;
    let bank = bank_of(bank)?;
    let opts = Opts {
        optimize,
        cycles,
        entry_sp,
        sensitive,
        loaded,
        fold,
    };
    Ok(Program::new(py.detach(|| {
        di_lift::lift_banked(&img, &es, &volatile, &f, opts, bank.as_ref())
    })))
}

/// The function plans the lift starts from: ``[{"pc", "top", "balanced", "heights"}]`` with
/// ``heights`` mapping each instruction start to the stack heights it is planned at relative
/// to the function entry (pushes negative), ``None`` where unknown.
#[pyfunction]
#[pyo3(signature = (image, entries, facts=None, bank=None))]
pub fn plan_program<'py>(
    py: Python<'py>,
    image: &[u8],
    entries: Vec<PyEntry>,
    facts: Option<&Bound<'py, PyDict>>,
    bank: Option<&Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyList>> {
    let img = self::image(image)?;
    let es = self::entries(entries);
    let f = self::facts(facts)?;
    let bank = bank_of(bank)?;
    let g = di_lift::analysis::discover(&img, &es, &f, bank.as_ref(), true, None);
    let pcs: Vec<u16> = es.iter().map(|e| e.pc).collect();
    let out = PyList::empty(py);
    for p in di_lift::plan(&g.img, &g, &pcs, &f, bank.as_ref(), &[], false) {
        let d = PyDict::new(py);
        d.set_item("pc", p.pc)?;
        d.set_item("top", p.top)?;
        d.set_item("balanced", p.balanced)?;
        let mut hs: BTreeMap<u16, Vec<Option<i32>>> = BTreeMap::new();
        for &(pc, h, _) in p.nodes.keys() {
            hs.entry(pc).or_default().push(match h {
                di_lift::func::H::Known(x) => Some(x),
                di_lift::func::H::Unknown => None,
            });
        }
        let h = PyDict::new(py);
        for (pc, v) in hs {
            h.set_item(pc, v)?;
        }
        d.set_item("heights", h)?;
        out.append(d)?;
    }
    Ok(out)
}

/// Lift ``[(image, entries, volatile, facts)]`` in parallel (rayon), with a progress bar on
/// stderr unless ``progress`` is false.
#[pyfunction]
#[pyo3(signature = (jobs, progress=true, optimize=true, cycles=false))]
pub fn lift_many(
    py: Python<'_>,
    jobs: Vec<PyJob<'_>>,
    progress: bool,
    optimize: bool,
    cycles: bool,
) -> PyResult<Vec<Program>> {
    let jobs: Vec<Job> = jobs
        .into_iter()
        .map(|(img, e, v, f)| {
            Ok(Job {
                img: image(&img)?,
                entries: entries(e),
                volatile: v,
                facts: facts(f.as_ref())?,
            })
        })
        .collect::<PyResult<_>>()?;
    let bar = if progress {
        ProgressBar::new(jobs.len() as u64)
    } else {
        ProgressBar::hidden()
    };
    bar.set_style(
        ProgressStyle::with_template("lift {bar:40} {pos}/{len} [{elapsed}<{eta}, {per_sec}]")
            .unwrap(),
    );
    let opts = Opts {
        optimize,
        cycles,
        ..Opts::default()
    };
    let progs = py.detach(|| di_lift::lift_many(&jobs, opts, || bar.inc(1)));
    bar.finish();
    Ok(progs.into_iter().map(Program::new).collect())
}

/// Packed machine records (``vagrant_moses.smc.Records``) as Python passes them.
#[derive(FromPyObject)]
pub struct Records<'py> {
    origin: Bound<'py, PyBytes>,
    meta: Bound<'py, PyBytes>,
    log: Bound<'py, PyBytes>,
    log_end: Bound<'py, PyBytes>,
    idx: Bound<'py, PyBytes>,
    vals: Bound<'py, PyBytes>,
    delta_end: Bound<'py, PyBytes>,
}

struct RecView<'a> {
    meta: &'a [u8],
    log: &'a [u8],
    log_end: Vec<usize>,
    idx: &'a [u8],
    vals: &'a [u8],
    delta_end: Vec<usize>,
}

fn ends(b: &[u8]) -> Vec<usize> {
    b.as_chunks::<8>()
        .0
        .iter()
        .map(|&c| u64::from_le_bytes(c) as usize)
        .collect()
}

impl Records<'_> {
    fn view(&self) -> PyResult<RecView<'_>> {
        let r = RecView {
            meta: self.meta.as_bytes(),
            log: self.log.as_bytes(),
            log_end: ends(self.log_end.as_bytes()),
            idx: self.idx.as_bytes(),
            vals: self.vals.as_bytes(),
            delta_end: ends(self.delta_end.as_bytes()),
        };
        let n = r.meta.len() / 7;
        let ok = r.meta.len() == 7 * n
            && r.log_end.len() == n
            && r.delta_end.len() == n
            && r.log_end.last().map_or(0, |&e| 4 * e) == r.log.len()
            && r.delta_end.last().map_or(0, |&e| e) == r.vals.len()
            && 2 * r.vals.len() == r.idx.len()
            && r.log_end.windows(2).all(|w| w[0] <= w[1])
            && r.delta_end.windows(2).all(|w| w[0] <= w[1]);
        if !ok {
            return Err(PyValueError::new_err("malformed records"));
        }
        Ok(r)
    }
}

impl RecView<'_> {
    fn len(&self) -> usize {
        self.meta.len() / 7
    }
    fn span(ends: &[usize], k: usize) -> std::ops::Range<usize> {
        (if k > 0 { ends[k - 1] } else { 0 })..ends[k]
    }
    fn log(&self, k: usize) -> impl Iterator<Item = (u16, u8, bool)> + '_ {
        Self::span(&self.log_end, k).map(|i| {
            let e = &self.log[4 * i..4 * i + 4];
            (u16::from_le_bytes([e[0], e[1]]), e[2], e[3] != 0)
        })
    }
    fn delta(&self, k: usize) -> impl Iterator<Item = (u16, u8)> + '_ {
        Self::span(&self.delta_end, k).map(|i| {
            (
                u16::from_le_bytes([self.idx[2 * i], self.idx[2 * i + 1]]),
                self.vals[i],
            )
        })
    }
    /// The ``Program.call`` register file of record ``k``.
    fn reg(&self, k: usize) -> [u8; 16] {
        let m = &self.meta[7 * k..7 * k + 7];
        let mut r = [0; 16];
        r[..4].copy_from_slice(&m[2..6]);
        for (i, bit) in FLAGS {
            r[i] = m[6] >> bit & 1;
        }
        r
    }
}

/// ``(register index, P bit)`` of the flags ``Program.call`` keeps one per register.
const FLAGS: [(usize, u8); 6] = [(8, 0), (9, 1), (10, 2), (11, 3), (13, 6), (14, 7)];
/// The register-file indices ``c64.mismatch`` compares: A X Y SP and C Z I D V N.
const REGS: [usize; 10] = [0, 1, 2, 3, 8, 9, 10, 11, 13, 14];
const HALTS: [&str; 3] = ["return", "cycles", "jam"];

/// A mismatch's fields after its call index, as ``c64.mismatch`` gives them.
#[derive(IntoPyObject)]
enum Bad {
    Halted((&'static str, &'static str)),
    Halt((&'static str, &'static str, &'static str)),
    Log((&'static str, usize)),
    Reg((&'static str, Vec<u32>, Vec<u32>)),
    Memory((&'static str, usize)),
    Boundary((&'static str, usize)),
}

/// ``c64.mismatch`` of a lifted call (halt, log, registers, memory ``m``) against record
/// ``k`` with machine RAM ``refm``.
fn mismatch(
    r: &RecView<'_>,
    k: usize,
    (halt, frame): (Halt, bool),
    log: &[(u16, u8, u64, bool)],
    reg: &[u8; 16],
    m: &[u8; 0x10000],
    refm: &[u8; 0x10000],
) -> Option<Bad> {
    let (want, got) = (HALTS[r.meta[7 * k + 1] as usize], halt_name(halt));
    let n = r.log_end[k] - RecView::span(&r.log_end, k).start;
    let same = r
        .log(k)
        .zip(log)
        .take_while(|(e, &(a, v, _, w))| *e == (a, v, w));
    let i = same.count();
    if want == "cycles" {
        return Some(if i < n.min(log.len()) {
            Bad::Log(("log", i))
        } else if log.len() > n || got == "cycles" {
            Bad::Halted(("halted", want))
        } else {
            Bad::Halt(("halt", want, got))
        });
    }
    if got != want {
        return Some(Bad::Halt(("halt", want, got)));
    }
    if i < n.max(log.len()) {
        return Some(Bad::Log(("log", i)));
    }
    let want_reg = r.reg(k);
    if REGS.iter().any(|&i| want_reg[i] != reg[i]) {
        let pick = |x: &[u8; 16]| REGS.iter().map(|&i| x[i] as u32).collect();
        return Some(Bad::Reg(("reg", pick(&want_reg), pick(reg))));
    }
    let dead = if frame {
        0x200
    } else {
        0x101 + want_reg[3] as usize
    };
    [0..0x100, dead..0x10000]
        .into_iter()
        .find(|s| m[s.clone()] != refm[s.clone()])
        .and_then(|s| s.into_iter().find(|&a| m[a] != refm[a]))
        .map(|a| Bad::Memory(("memory", a)))
        .or((want != "return").then_some(Bad::Halted(("halted", want))))
}
