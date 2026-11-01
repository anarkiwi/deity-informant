//! `deity_informant._native`: the di-pcode IR and reference interpreter for Python.

use di_pcode::{instantiate_bytes, Ctrl, Insn, Op, Pen, Slot, Space, Vn};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyByteArray, PyDict, PyList, PyString, PyTuple};

fn vn<'py>(py: Python<'py>, v: Vn) -> PyResult<Bound<'py, PyAny>> {
    if v.is_none() {
        return Ok(py.None().into_bound(py));
    }
    let sp = match v.space() {
        Space::Const => intern!(py, "c"),
        Space::Reg => intern!(py, "r"),
        Space::Unique => intern!(py, "u"),
        Space::Ram => intern!(py, "m"),
    };
    Ok((sp, v.off(), v.size())
        .into_pyobject(py)?
        .to_list()
        .into_any())
}

fn slot<'py>(py: Python<'py>, s: Slot) -> PyResult<Option<Bound<'py, PyTuple>>> {
    let (srcs, f): (&[u8], _) = match s {
        Slot::Fixed => return Ok(None),
        Slot::B1 => (&[1], intern!(py, "id")),
        Slot::Word => (&[1, 2], intern!(py, "word")),
        Slot::B1Inc => (&[1], intern!(py, "hi1")),
        Slot::B2Inc => (&[2], intern!(py, "hi1")),
    };
    Ok(Some(PyTuple::new(
        py,
        [PyList::new(py, srcs)?.into_any(), f.clone().into_any()],
    )?))
}

fn record<'py>(py: Python<'py>, insn: &Insn, ops: &[Op]) -> PyResult<Bound<'py, PyDict>> {
    let t = insn.template();
    let pops = PyList::empty(py);
    let prov = PyDict::new(py);
    for (i, op) in ops.iter().enumerate() {
        let ins = PyList::empty(py);
        for (j, &v) in op.inputs().iter().enumerate() {
            ins.append(vn(py, v)?)?;
            if let Some(p) = slot(py, v.slot())? {
                prov.set_item((i, j), p)?;
            }
        }
        pops.append(PyList::new(
            py,
            [
                PyString::intern(py, op.opc.name()).into_any(),
                vn(py, op.out)?,
                ins.into_any(),
            ],
        )?)?;
    }
    let kind = PyString::intern(py, t.ctrl.name()).into_any();
    let (ctrl, ctrl_prov) = match insn.ctrl() {
        Ctrl::Br {
            flag,
            pol,
            target,
            fall,
        } => (
            (
                kind,
                vn(py, di_pcode::ir::r(flag as u16))?,
                pol,
                target,
                fall,
            )
                .into_pyobject(py)?
                .into_any(),
            Some(
                ([1], intern!(py, "rel"), target)
                    .into_pyobject(py)?
                    .into_any(),
            ),
        ),
        Ctrl::Jmp(w) | Ctrl::JmpInd(w) | Ctrl::Jsr(w) => (
            (kind, w).into_pyobject(py)?.into_any(),
            Some(
                ([1, 2], intern!(py, "word"), w)
                    .into_pyobject(py)?
                    .into_any(),
            ),
        ),
        _ => ((kind,).into_pyobject(py)?.into_any(), None),
    };
    let pen = match insn.pen() {
        Pen::None => None,
        Pen::Branch => Some((intern!(py, "branch"),).into_pyobject(py)?.into_any()),
        Pen::Ax(w) => Some((intern!(py, "ax"), w).into_pyobject(py)?.into_any()),
        Pen::Ay(w) => Some((intern!(py, "ay"), w).into_pyobject(py)?.into_any()),
        Pen::Iy { zp, .. } => Some((intern!(py, "iy"), zp).into_pyobject(py)?.into_any()),
    };
    let p = PyDict::new(py);
    p.set_item("op0", insn.opcode)?;
    p.set_item("ops", prov)?;
    p.set_item("ctrl", ctrl_prov)?;
    let d = PyDict::new(py);
    d.set_item("ops", pops)?;
    d.set_item("len", t.len)?;
    d.set_item("cyc", t.cyc)?;
    d.set_item("pen", pen)?;
    d.set_item("ctrl", ctrl)?;
    d.set_item("prov", p)?;
    d.set_item("stk", t.stk().map(|k| k.name()))?;
    Ok(d)
}

/// ``lift(mem, pc)`` from the native templates: the same record as
/// ``deity_informant.lifter.lift``. ``mem`` is anything indexable by address.
#[pyfunction]
fn lift<'py>(py: Python<'py>, mem: &Bound<'py, PyAny>, pc: u16) -> PyResult<Bound<'py, PyDict>> {
    let byte = |a: u16| mem.get_item(a)?.extract::<u8>();
    let (op, b1, b2) = (
        byte(pc)?,
        byte(pc.wrapping_add(1))?,
        byte(pc.wrapping_add(2))?,
    );
    let mut arena = Vec::with_capacity(di_pcode::MAX_OPS);
    let insn = instantiate_bytes(pc, op, b1, b2, &mut arena);
    record(py, &insn, &arena)
}

fn with_image<R>(
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

/// The reference interpreter's state: ``reg`` (the 16-entry ``lift()`` register file),
/// ``pc`` and ``cycles``; ``step``/``run`` execute over a 64 KiB ``bytearray``.
#[pyclass(module = "deity_informant._native")]
struct Cpu(di_pcode::Cpu);

#[pymethods]
impl Cpu {
    #[new]
    #[pyo3(signature = (reg=None, pc=0, cycles=0))]
    fn new(reg: Option<[u8; 16]>, pc: u16, cycles: u64) -> Self {
        let mut c = di_pcode::Cpu::new();
        if let Some(r) = reg {
            c.set_reg(r);
        }
        c.pc = pc;
        c.cycles = cycles;
        Cpu(c)
    }

    #[getter]
    fn reg<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        PyList::new(py, self.0.reg())
    }
    #[setter]
    fn set_reg(&mut self, reg: [u8; 16]) {
        self.0.set_reg(reg);
    }
    #[getter]
    fn pc(&self) -> u16 {
        self.0.pc
    }
    #[setter]
    fn set_pc(&mut self, pc: u16) {
        self.0.pc = pc;
    }
    #[getter]
    fn cycles(&self) -> u64 {
        self.0.cycles
    }
    #[setter]
    fn set_cycles(&mut self, cycles: u64) {
        self.0.cycles = cycles;
    }

    /// The status byte (bit 5 set, B clear).
    fn status(&self) -> u8 {
        self.0.status(false)
    }

    /// Execute one instruction; the new PC. A JAM raises ``RuntimeError`` (PC unchanged).
    fn step(&mut self, mem: &Bound<'_, PyByteArray>) -> PyResult<u16> {
        let cpu = &mut self.0;
        with_image(mem, |m| cpu.step(m))?
            .map_err(|j| PyRuntimeError::new_err(format!("JAM at {:04X}", j.pc)))?;
        Ok(self.0.pc)
    }

    /// Execute up to ``max_steps`` instructions: ``(executed, jammed)``; a JAM ends the run
    /// with PC on it.
    fn run(&mut self, mem: &Bound<'_, PyByteArray>, max_steps: u64) -> PyResult<(u64, bool)> {
        let cpu = &mut self.0;
        let (n, jam) = with_image(mem, |m| cpu.run(m, max_steps))?;
        Ok((n, jam.is_some()))
    }

    fn __repr__(&self) -> String {
        format!(
            "Cpu(reg={:?}, pc=0x{:04X}, cycles={})",
            self.0.reg(),
            self.0.pc,
            self.0.cycles
        )
    }
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(lift, m)?)?;
    m.add_class::<Cpu>()?;
    m.add("UNIQUE_SIZE", di_pcode::UNIQUE_SIZE)?;
    Ok(())
}
