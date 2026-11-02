//! Whole-program lifting (`di_lift`) for Python: `lift_program`, `lift_many`, `Program`
//! and the reference `reference_call` with the same bus.

use di_lift::ir::{mem, other, Program as LProgram};
use di_lift::{Back, Backend, BankMap, Banked, Entry, Exec, Facts, Job, Opts, Outcome, Runner};
use di_pcode::{reg as R, Bus, Cpu, Halt, Opc, Space, Vn};
use indicatif::{ProgressBar, ProgressStyle};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyByteArray, PyDict, PyList};
use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// A 64 KiB bytearray as the bus: volatile ranges optionally logged as
/// `(addr, value, cycle, is_write)` and optionally read as `(cycle ^ n ^ addr) & 0xff` for
/// the `n`th volatile access. With `dev` (banked memory) device accesses reach that register
/// file and `ram_*` the bytearray.
struct PyBus<'a> {
    mem: &'a mut [u8; 0x10000],
    dev: Option<Box<[u8; 0x10000]>>,
    volatile: &'a [(u32, u32)],
    log: Option<Vec<(u16, u8, u64, bool)>>,
    synth: bool,
    now: u64,
    n: u64,
}

impl<'a> PyBus<'a> {
    fn new(mem: &'a mut [u8; 0x10000], volatile: &'a [(u32, u32)], log: bool, synth: bool) -> Self {
        PyBus {
            mem,
            dev: None,
            volatile,
            log: log.then(Vec::new),
            synth,
            now: 0,
            n: 0,
        }
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

impl Bus for PyBus<'_> {
    #[inline]
    fn read(&mut self, a: u16) -> u8 {
        if self.volatile(a) {
            self.n += 1;
            let v = if self.synth {
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
        self.device()[a as usize] = v;
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

/// A bank map from its dict: ``dir``, ``data``, ``mask``, ``pull``, ``lut`` (configuration per
/// effective port value), ``configs`` (per configuration 256 page kinds: 0 RAM, 1 ROM,
/// 2 I/O) and ``rom`` (64 KiB of ROM bytes at their addresses).
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
/// ``[mnemonic, out, [inputs], aux]``; ``call``/``run`` execute it over a 64 KiB bytearray.
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
    /// Functions: ``{"pc", "entry", "blocks": [{"pc", "ops"}], "tables"}``; varnodes are
    /// ``[space, offset, size]``; ``aux`` is the target block, table, callee, ``CALLOTHER``
    /// name or ``"volatile"``.
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
            d.set_item("blocks", blocks)?;
            d.set_item("tables", f.tables.clone())?;
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
        d.set_item("stack_private", s.stack_private)?;
        d.set_item("stack_guards", s.stack_guards)?;
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
        Ok(d)
    }

    /// Whether the program counts cycles.
    #[getter]
    fn cycles(&self) -> bool {
        self.prog.cycles
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

    /// Deopts so far per PC.
    #[getter]
    fn deopts(&self) -> HashMap<u16, u64> {
        self.state.lock().unwrap().1.clone()
    }

    /// ``run_to_rts`` of entry ``entry`` with ``A = a``: push the dummy return, run until it
    /// returns, ``max_cycles`` elapse, or a JAM. ``reg``/``cycles`` set the starting state.
    /// Returns ``{"reg", "pc", "cycles", "halt", "deopt", "log"}``. Without cycle counting
    /// the lifted code runs to its return and ``cycles`` counts only the reference
    /// interpreter's part after a deopt. A banked program's devices read and write ``io``
    /// (a 64 KiB register file, updated in place; a copy of ``mem`` when absent).
    #[pyo3(signature = (mem, entry=0, a=0, reg=None, cycles=0, max_cycles=10_000_000, log=false, synth=false, io=None, backend=None))]
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
        io: Option<&Bound<'py, PyByteArray>>,
        backend: Option<&str>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let backend = self.backend_or(backend)?;
        self.entry(entry)?;
        let mut cpu = cpu_of(reg, cycles);
        let mut dev = devices(self.prog.bank.is_some(), mem, io)?;
        let (o, lg) = with_mem(mem, |m| {
            let mut bus = PyBus::new(m, &self.prog.volatile, log, synth);
            bus.dev = dev.take();
            let o = self.run(&mut bus, &mut cpu, entry, a, max_cycles, backend);
            dev = bus.dev;
            (o, bus.log)
        })?;
        store_devices(io, dev)?;
        result(py, &cpu, o.halt, o.deopt, lg)
    }

    /// A :class:`Session` over ``mem``: repeated ``call``s from one CPU state (``reg``),
    /// cheaper than ``Program.call`` per call. A banked program's devices start as ``io``
    /// (a copy of ``mem`` when absent) and persist across the session's calls.
    #[pyo3(signature = (mem, max_cycles=10_000_000, reg=None, io=None, backend=None))]
    fn session(
        slf: &Bound<'_, Self>,
        mem: &Bound<'_, PyByteArray>,
        max_cycles: u64,
        reg: Option<[u8; 16]>,
        io: Option<&Bound<'_, PyByteArray>>,
        backend: Option<&str>,
    ) -> PyResult<Session> {
        let p = slf.get();
        with_mem(mem, |_| ())?;
        Ok(Session {
            prog: slf.clone().unbind(),
            mem: mem.clone().unbind(),
            dev: devices(p.prog.bank.is_some(), mem, io)?,
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
    /// ``run_to_rts`` of `entry` over `bus` from `cpu`, keeping the invalidation and deopts.
    fn run(
        &self,
        bus: &mut PyBus<'_>,
        cpu: &mut Cpu,
        entry: usize,
        a: u8,
        max_cycles: u64,
        backend: Backend,
    ) -> Outcome {
        bus.now = cpu.cycles;
        let mut st = self.state.lock().unwrap();
        let mut run = Runner::new(&self.exec);
        run.backend = backend;
        run.invalid = st.0;
        let o = run.call(entry, a, cpu, bus, max_cycles);
        st.0 = run.invalid;
        for (k, v) in run.deopts {
            *st.1.entry(k).or_default() += v;
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
            let o = p.run(&mut bus, cpu, entry, a, mc, b);
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

/// The device register file of a banked run: ``io`` or a copy of ``mem``.
fn devices(
    banked: bool,
    mem: &Bound<'_, PyByteArray>,
    io: Option<&Bound<'_, PyByteArray>>,
) -> PyResult<Option<Box<[u8; 0x10000]>>> {
    if !banked {
        return Ok(None);
    }
    with_mem(io.unwrap_or(mem), |d| Some(Box::new(*d)))
}

/// Write a banked run's device register file back to ``io``.
fn store_devices(
    io: Option<&Bound<'_, PyByteArray>>,
    dev: Option<Box<[u8; 0x10000]>>,
) -> PyResult<()> {
    if let (Some(io), Some(d)) = (io, dev) {
        with_mem(io, |m| *m = *d)?;
    }
    Ok(())
}

/// ``run_to_rts`` on the reference interpreter with the same bus as ``Program.call``;
/// ``clocked=False`` gives volatile accesses no cycle, as a program lifted without cycles.
/// ``bank`` (a bank map dict, see ``lift_program``) sees memory through it, devices on ``io``.
#[pyfunction]
#[pyo3(signature = (mem, pc, a=0, reg=None, cycles=0, max_cycles=10_000_000, volatile=vec![], log=false, synth=false, clocked=true, bank=None, io=None))]
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
    io: Option<&Bound<'py, PyByteArray>>,
) -> PyResult<Bound<'py, PyDict>> {
    let mut cpu = cpu_of(reg, cycles);
    let bank = bank_of(bank)?;
    let dev = devices(bank.is_some(), mem, io)?;
    let (h, lg, dev) = with_mem(mem, |m| {
        let mut bus = PyBus::new(m, &volatile, log, synth);
        bus.now = cycles;
        bus.dev = dev;
        let start = cpu.r(R::SP);
        for v in [0u8, 1] {
            let sp = cpu.r(R::SP);
            bus.ram_write(0x100 | sp as u16, v);
            cpu.set_r(R::SP, sp.wrapping_sub(1));
        }
        cpu.set_r(R::A, a);
        cpu.pc = pc;
        let end = cpu.cycles.saturating_add(max_cycles).saturating_add(1);
        let above = start as i32 - 1;
        let h = match (&bank, clocked) {
            (Some(b), true) => cpu.run_clocked(&mut Banked::new(b, &mut bus), above, end),
            (Some(b), false) => {
                cpu.run_clocked(&mut Banked::new(b, &mut Unclocked(&mut bus)), above, end)
            }
            (None, true) => cpu.run_clocked(&mut bus, above, end),
            (None, false) => cpu.run_clocked(&mut Unclocked(&mut bus), above, end),
        };
        (h, bus.log, bus.dev)
    })?;
    store_devices(io, dev)?;
    result(py, &cpu, h, None, lg)
}

/// Lift ``entries`` of the program in ``image`` (64 KiB entry state): ``entries`` as
/// ``[(pc, [(reg, values)])]``, ``volatile`` as ``[(lo, hi)]``, ``facts`` as a dict with
/// ``starts``, ``edges``, ``writes``, ``page1`` and ``heights``; ``optimize=False`` keeps the emitted
/// P-Code; ``cycles=True`` counts cycles. ``bank`` is a bank map: ``dir``/``data`` port
/// cells, ``mask`` and ``pull`` bits, ``lut`` (configuration per effective port value),
/// ``configs`` (per configuration 256 page kinds: 0 RAM, 1 ROM, 2 I/O) and ``rom``.
#[pyfunction]
#[pyo3(signature = (image, entries, volatile=vec![], facts=None, optimize=true, cycles=false, bank=None))]
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
) -> PyResult<Program> {
    let img = self::image(image)?;
    let es = self::entries(entries);
    let f = self::facts(facts)?;
    let bank = bank_of(bank)?;
    let opts = Opts { optimize, cycles };
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
    let g = di_lift::analysis::discover(&img, &es, &f, bank.as_ref());
    let pcs: Vec<u16> = es.iter().map(|e| e.pc).collect();
    let out = PyList::empty(py);
    for p in di_lift::plan(&g.img, &g, &pcs, &f, bank.as_ref()) {
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
    let opts = Opts { optimize, cycles };
    let progs = py.detach(|| di_lift::lift_many(&jobs, opts, || bar.inc(1)));
    bar.finish();
    Ok(progs.into_iter().map(Program::new).collect())
}
