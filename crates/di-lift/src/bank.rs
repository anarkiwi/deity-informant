//! A host's bank map: a port (direction and data cells, both also backed by RAM) selects a
//! configuration that maps each 256-byte page to RAM, a constant ROM image or I/O.

use di_pcode::Bus;

/// What backs a page in one configuration.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Back {
    Ram,
    Rom,
    Io,
}

/// The bank map. The effective port value is the data cell with undriven (input) bits read
/// high; its `mask` bits index `lut`, the configuration. A read of the data cell sees driven
/// bits as written and undriven ones as `pull`.
#[derive(Clone, Debug)]
pub struct BankMap {
    pub dir: u16,
    pub data: u16,
    pub mask: u8,
    pub pull: u8,
    pub lut: Vec<u8>,
    pub configs: Vec<[Back; 256]>,
    pub rom: Box<[u8; 0x10000]>,
}

impl BankMap {
    /// The configuration for port registers `dir`, `data`.
    pub fn config(&self, dir: u8, data: u8) -> u8 {
        self.lut[((data | !dir) & self.mask) as usize]
    }
    /// The effective port values (`lut` indices) selecting configuration `c`.
    pub fn selectors(&self, c: u8) -> impl Iterator<Item = u8> + '_ {
        (0..=self.mask).filter(move |&e| e & !self.mask == 0 && self.lut[e as usize] == c)
    }
    /// The data cell as read with registers `dir`, `data`.
    pub fn port_read(&self, dir: u8, data: u8) -> u8 {
        (data & dir) | (!dir & self.pull)
    }
    /// The backing of `addr` in configuration `c`.
    pub fn back(&self, c: u8, addr: u16) -> Back {
        self.configs[c as usize][(addr >> 8) as usize]
    }
    /// Whether `addr` is a port cell.
    pub fn port(&self, addr: u16) -> bool {
        addr == self.dir || addr == self.data
    }
    /// Read `addr` in configuration `c` over `bus` (whose `ram_*` is RAM and `read`/`write`
    /// the devices).
    pub fn read<B: Bus>(&self, c: u8, bus: &mut B, addr: u16) -> u8 {
        if addr == self.data {
            let (d, v) = (bus.ram_read(self.dir), bus.ram_read(self.data));
            return self.port_read(d, v);
        }
        match self.back(c, addr) {
            Back::Ram => bus.ram_read(addr),
            Back::Rom => self.rom[addr as usize],
            Back::Io => bus.read(addr),
        }
    }
    /// Write `addr` in configuration `c`: I/O pages reach the device only, others RAM.
    pub fn write<B: Bus>(&self, c: u8, bus: &mut B, addr: u16, v: u8) {
        match self.back(c, addr) {
            Back::Io => bus.write(addr, v),
            _ => bus.ram_write(addr, v),
        }
    }
}

/// `inner` seen through a bank map, the configuration following writes to the port cells.
pub struct Banked<'a, B> {
    pub map: &'a BankMap,
    pub inner: &'a mut B,
    cfg: u8,
}

impl<'a, B: Bus> Banked<'a, B> {
    pub fn new(map: &'a BankMap, inner: &'a mut B) -> Self {
        let cfg = map.config(inner.ram_read(map.dir), inner.ram_read(map.data));
        Banked { map, inner, cfg }
    }
    /// The current configuration.
    pub fn config(&self) -> u8 {
        self.cfg
    }
}

impl<B: Bus> Bus for Banked<'_, B> {
    fn read(&mut self, addr: u16) -> u8 {
        self.map.read(self.cfg, self.inner, addr)
    }
    fn write(&mut self, addr: u16, v: u8) {
        self.map.write(self.cfg, self.inner, addr, v);
        if self.map.port(addr) {
            let m = self.map;
            self.cfg = m.config(self.inner.ram_read(m.dir), self.inner.ram_read(m.data));
        }
    }
    fn fetch(&mut self, addr: u16) -> u8 {
        self.read(addr)
    }
    fn now(&mut self, c: u64) {
        self.inner.now(c)
    }
    fn ram_read(&mut self, addr: u16) -> u8 {
        self.inner.ram_read(addr)
    }
    fn ram_write(&mut self, addr: u16, v: u8) {
        self.inner.ram_write(addr, v)
    }
}
