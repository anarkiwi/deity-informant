"""Cross-frame state-machine lift: per-frame recorder artifacts to one canonical tune model.

:func:`lift_kernel` partitions memory into constant seed tables ``T`` and
persistent state ``S``, then dedups the ``N`` frames to their ``K`` distinct
control paths; :meth:`Kernel.verify` re-iterates from ``S0`` alone. See docs/kernel.md.
"""

from __future__ import annotations

from . import expr as E

_REG_NAMES = {0: "A", 1: "X", 2: "Y", 3: "SP", 8: "C", 9: "Z", 10: "I", 11: "D", 13: "V", 14: "N"}
_OP_SYM = {"INT_ADD": "+", "INT_SUB": "-", "INT_OR": "|", "INT_AND": "&", "INT_XOR": "^"}


def _entry_pure(n):
    """Whether ``n`` is evaluable at frame entry (no within-frame ``cur`` leaf)."""
    k = n[0]
    if k == "cur":
        return False
    if k == "op":
        return all(_entry_pure(c) for c in n[2])
    if k == "mem":
        return _entry_pure(n[1])
    return True


def _iter_mem(n, out):
    """Collect constant addresses of every ``mem`` leaf reachable in ``n``.

    ``mem`` leaves are cross-frame (entry) reads; within-frame reads are ``cur``.
    Descends through ``cur`` fallbacks (entry-pure) and op children.
    """
    k = n[0]
    if k == "mem":
        a = n[1]
        if E.is_const(a):
            out.add(a[1])
        _iter_mem(a, out)
    elif k == "cur":
        _iter_mem(n[1], out)
        _iter_mem(n[4], out)
    elif k == "op":
        for c in n[2]:
            _iter_mem(c, out)


class Variant:
    """One distinct control path: its guard, transition, and symbolic store list."""

    __slots__ = ("frames", "guard", "transition", "sslog")

    def __init__(self, frames, guard, transition, sslog):
        self.frames = frames  # frame indices sharing this path
        self.guard = guard  # [(site, kind, expr, observed)] control folds (branch/target/opcode)
        self.transition = transition  # {addr: entry-pure expr(S,T)} for state cells
        self.sslog = sslog  # [(saddr_expr, val_expr, sz, is_output)] ordered stores


class Kernel:
    """Canonical state-machine model of a recorded tune.

    ``(tables, s0, variants)`` is the compact form: iterating the variants from
    ``s0`` reproduces the observable write stream byte-exact (:meth:`verify`).
    """

    def __init__(self, rec):
        self.rec = rec
        self.outputs = set(rec.outputs)
        self._build()

    # ---- construction ------------------------------------------------------
    def _build(self):
        rec = self.rec
        entry0 = rec.entry[0][0] if rec.entry else b"\x00" * 0x10000
        written = set()
        for slog in rec.slog:
            for _pos, addr, _ex, _sz in slog:
                written.add(addr)
        leaves = set()
        for i, slog in enumerate(rec.slog):
            for _p, _a, ex, _s in slog:
                _iter_mem(ex, leaves)
            for _s, _k, ex, _o in rec.facts[i]:
                _iter_mem(ex, leaves)
            for expr, _sz in rec.F[i].values():
                _iter_mem(expr, leaves)
        self.written = written
        self.state = leaves & written
        self.tables = {a: entry0[a] for a in sorted(leaves - written)}
        self.s0 = {a: entry0[a] for a in sorted(self.state)}
        self.inputs = self._declare_inputs()
        self.variants = self._group()

    def _declare_inputs(self):
        rec = self.rec
        regs = [rec.entry[i][1] for i in range(len(rec.entry))]
        reg_const = all(r == regs[0] for r in regs) if regs else True
        uni = any(rec._uni[i] for i in range(len(rec._uni)))
        return {"regs_constant": reg_const, "env_reads": bool(uni)}

    @staticmethod
    def _place_map(facts):
        """``{store_addr: symbolic_addr_expr}`` for state-dependent store placements.

        A store placement records ``site == observed == addr``; a load placement
        keys on the load ``pc`` instead and is excluded here.
        """
        return {obs: ex for site, kind, ex, obs in facts if kind == "place" and site == obs}

    def _key(self, i):
        """Structural path key: expressions kept, state-dependent addresses abstracted.

        Two frames of one control path differ only in the concrete addresses of
        their placement stores/loads (data), so those are dropped; branch/target/
        opcode outcomes are kept, since a differing outcome is a different path.
        """
        rec = self.rec
        pm = self._place_map(rec.facts[i])
        fk = []
        for site, kind, ex, obs in rec.facts[i]:
            if kind != "place":
                fk.append((site, kind, ex, obs))
            elif site == obs:
                fk.append(("place", ex))
            else:
                fk.append((site, "place", ex))
        sk = tuple((None if addr in pm else addr, ex, sz) for _p, addr, ex, sz in rec.slog[i])
        return (tuple(fk), sk)

    def _symbolic_slog(self, i):
        pm = self._place_map(self.rec.facts[i])
        return [
            (pm.get(addr, E.konst(addr, 2)), ex, sz, addr in self.outputs, addr)
            for _p, addr, ex, sz in self.rec.slog[i]
        ]

    def _group(self):
        rec = self.rec
        groups = {}
        order = []
        for i in range(len(rec.slog)):
            key = self._key(i)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(i)
        out = []
        for key in order:
            frames = groups[key]
            rep = frames[0]
            guard = [f for f in rec.facts[rep] if f[1] != "place"]
            trans = {a: ex for a, (ex, _sz) in rec.F[rep].items() if a in self.state}
            out.append(Variant(frames, guard, trans, self._symbolic_slog(rep)))
        if out:
            common = set(out[0].guard).intersection(*(set(v.guard) for v in out[1:]))
            for v in out:
                v.guard = [f for f in v.guard if f not in common]
        return out

    # ---- verification: closed-loop re-iteration ----------------------------
    def verify(self, self_driving=False):
        """Re-iterate from ``S0`` carrying the image forward; return ``(ok, div)``.

        Tier A (default) drives with the recorded templates and asserts closure
        plus byte-exact outputs. Tier B (``self_driving``) selects each variant by
        its guard -- best-effort, so a miss reports a reason, not a wrong model.
        """
        rec = self.rec
        n = len(rec.slog)
        if n == 0:
            return (True, None)
        image = bytearray(rec.entry[0][0])
        for i in range(n):
            frozen = bytes(image)
            regs, uni = rec.entry[i][1], rec._uni[i]
            if self_driving:
                v = self._select(frozen, regs, uni)
                if v is None:
                    return (False, ("no-variant", i))
                out = self._apply_sym(v.sslog, frozen, regs, image, uni)
            else:
                if frozen != rec.entry[i][0]:
                    return (False, ("closure", i))
                out = self._apply(rec.slog[i], frozen, regs, image, uni)
            if out != rec.replay(i):
                return (False, ("output", i))
        return (True, None)

    def _apply(self, slog, frozen, regs, image, uni):
        out = []
        for _pos, addr, ex, sz in slog:
            v = E.evaluate(ex, frozen, regs, image, uni)
            for k in range(sz):
                image[(addr + k) & 0xFFFF] = (v >> (8 * k)) & 0xFF
            if addr in self.outputs:
                out.append((addr, v))
        return out

    def _apply_sym(self, sslog, frozen, regs, image, uni):
        """Apply a variant's symbolic store list, evaluating each address per frame."""
        out = []
        for saddr, ex, sz, _is_out, _rep in sslog:
            addr = E.evaluate(saddr, frozen, regs, image, uni) & 0xFFFF
            v = E.evaluate(ex, frozen, regs, image, uni)
            for k in range(sz):
                image[(addr + k) & 0xFFFF] = (v >> (8 * k)) & 0xFF
            if addr in self.outputs:
                out.append((addr, v))
        return out

    def _select(self, frozen, regs, uni):
        """Pick the variant whose control guard matches the current state.

        Evaluated on the frame-entry image only; enough to split branch/target/
        opcode-dispatched paths. ``None`` if not uniquely determined.
        """
        if len(self.variants) == 1:
            return self.variants[0]
        scratch = bytearray(frozen)
        hits = [
            v
            for v in self.variants
            if all(
                E.evaluate(g[2], frozen, regs, scratch, uni) == g[3]
                for g in v.guard
                if _entry_pure(g[2])
            )
        ]
        return hits[0] if len(hits) == 1 else None

    # ---- readable canonical dump -------------------------------------------
    def _s(self, n):
        k = n[0]
        if k == "const":
            return "$%X" % n[1]
        if k == "reg":
            return _REG_NAMES.get(n[1], "r%d" % n[1])
        if k == "uni":
            return "U%d" % n[1]
        if k == "mem":
            a = n[1]
            if E.is_const(a):
                addr = a[1]
                if addr in self.state:
                    return "S[$%04X]" % addr
                if addr in self.tables:
                    return "$%X" % self.tables[addr]
                return "M[$%04X]" % addr
            return "M[%s]" % self._s(a)
        if k == "cur":
            return self._s(E.to_entry(n))
        sym = _OP_SYM.get(n[1])
        if sym:
            return "(" + (" %s " % sym).join(self._s(c) for c in n[2]) + ")"
        return "%s(%s)" % (n[1], ", ".join(self._s(c) for c in n[2]))

    def pretty(self):
        """Textual canonical dump: tables, state, ``S0``, and per-variant logic."""
        lines = [
            "tables: %d cells" % len(self.tables),
            "state:  %s" % (", ".join("$%04X" % a for a in sorted(self.state)) or "(none)"),
            "S0:     %s" % ", ".join("$%04X=$%02X" % (a, v) for a, v in self.s0.items()),
            "inputs: regs_constant=%s env_reads=%s"
            % (self.inputs["regs_constant"], self.inputs["env_reads"]),
        ]
        for vi, v in enumerate(self.variants):
            lines.append("variant %d  (%d frames):" % (vi, len(v.frames)))
            for site, kind, ex, obs in v.guard:
                lines.append("  guard %s@%04X: %s == $%X" % (kind, site, self._s(ex), obs))
            for a in sorted(v.transition):
                lines.append("  S[$%04X]' = %s" % (a, self._s(E.to_entry(v.transition[a]))))
            for saddr, ex, _sz, is_out, _rep in v.sslog:
                if is_out:
                    lines.append("  OUT[%s] = %s" % (self._s(saddr), self._s(E.to_entry(ex))))
        return "\n".join(lines)


def lift_kernel(rec):
    """Lift a :class:`~deity_informant.recorder.Recording` to a :class:`Kernel`."""
    return Kernel(rec)
