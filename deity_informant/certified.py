"""Proven run facts: each instruction's accesses and edges over every run of the translation.

Play is proven by a jennings certificate per state init leaves, re-checked by ``verify``; init
by its runs over every value its probes may read. Anything else raises :class:`Unproven`
(docs/certified.md)."""

from dataclasses import dataclass, field

from jennings.coverage import certificate as V
from jennings.coverage import components as C
from tqdm import tqdm

from . import masks

#: The calls a certificate may cover at most.
LIMIT = 2_000_000
#: The cycles one call may take.
MAX_CYCLES = 10_000_000
#: How ``verify`` taints the calls: every call, each from its own entry.
TAINT = "calls"
#: What a proven fact rests on, beside the check itself.
ASSUMED = (
    "the native 6502 core is the machine and its flow tables name every input of each output",
    "play has no input: it loads no volatile address, and no excluded location carries state",
    "the host calls each entry but init with A = 0 only",
    "init's probes read only values of the device model's sets",
)


class Unproven(RuntimeError):
    """A run fact asked for that no check proves."""


@dataclass(frozen=True)
class Facts:
    """The accesses, instructions and edges of every run of the entries proven.

    Attributes:
        accesses: ``{(pc, store): frozenset of addresses}`` of each instruction's data
            accesses, as it names them.
        executed: the instructions that run.
        edges: ``(pc, next)`` of consecutive instructions within a call.
        proven: ``{entry pc: "certificate" or "runs"}``: how each entry's facts are proven.
        certificates: per play state, its ``Certificate`` and ``Verdict``.
    """

    accesses: dict = field(default_factory=dict)
    executed: frozenset = frozenset()
    edges: frozenset = frozenset()
    proven: dict = field(default_factory=dict)
    certificates: tuple = ()

    def __or__(self, other):
        acc = dict(self.accesses)
        for k, at in other.accesses.items():
            acc[k] = acc.get(k, frozenset()) | at
        return Facts(
            acc,
            self.executed | other.executed,
            self.edges | other.edges,
            {**self.proven, **other.proven},
            self.certificates + other.certificates,
        )

    def addresses(self, pcs):
        """The addresses the data accesses (loads and stores) of instructions ``pcs`` take."""
        out = set()
        for pc in pcs:
            out |= self.accesses.get((pc, False), frozenset())
            out |= self.accesses.get((pc, True), frozenset())
        return out

    def ran(self, pc):
        """Whether instruction ``pc`` runs."""
        return pc in self.executed

    def summary(self):
        """JSON-ready counts and verdicts, for ``info["certified"]``."""
        return {
            "entries": {f"{pc:04x}": how for pc, how in sorted(self.proven.items())},
            "executed": len(self.executed),
            "sites": len(self.accesses),
            "certificates": [
                {"calls": c.calls, "feed": len(c.feed), "ok": v.ok, "assumed": list(v.assumed)}
                for c, v in self.certificates
            ],
            "assumed": list(ASSUMED),
        }


def _exclude(volatile):
    """The ranges a certificate takes to carry no state: the stack page and ``volatile``."""
    return (C.STACK, *(range(lo, min(hi, 0x10000)) for lo, hi in volatile))


def play(states, pc, volatile, limit=LIMIT):
    """The :class:`Facts` of entry ``pc`` called with ``A = 0`` from each of ``states``
    (64 KiB images), each certified and verified."""
    vol = masks.mask(volatile)
    acc, edges, certs = {}, set(), []
    for state in tqdm(sorted({bytes(s) for s in states}), desc="certify", unit="state"):
        try:
            cert = V.certify(state, pc, limit, max_cycles=MAX_CYCLES, exclude=_exclude(volatile))
        except RuntimeError as e:
            raise Unproven(f"play at ${pc:04X} is not certified: {e}") from e
        if isinstance(cert, V.Open):
            raise Unproven(f"play at ${pc:04X} has no cover point within {limit} calls: {cert}")
        verdict = V.verify(state, cert, taint=TAINT)
        if not verdict.ok:
            raise Unproven(f"play at ${pc:04X}: {verdict.failed}: {verdict.detail}")
        if verdict.assumed:
            raise Unproven(f"play at ${pc:04X} leans on excluded locations {verdict.assumed}")
        for (q, kind), at in cert.accesses.items():
            store = kind == C.STORE
            if not store and vol[list(at)].any():
                raise Unproven(f"play at ${pc:04X} loads a volatile address at ${q:04X}")
            acc[(q, store)] = acc.get((q, store), frozenset()) | frozenset(at)
        edges.update(cert.edges)
        certs.append((cert, verdict))
    executed = {pc} | {a for a, _ in edges} | {b for _, b in edges}
    return Facts(acc, frozenset(executed), frozenset(edges), {pc: "certificate"}, tuple(certs))


def init(runs, pc):
    """The :class:`Facts` of init at ``pc`` from ``runs`` (per argument value, its
    ``postinit.forks``); None where a value has none or one run is not exact."""
    if not runs or any(not rs or not all(r.exact for r in rs) for rs in runs):
        return None
    acc, executed, edges = {}, set(), set()
    for r in (r for rs in runs for r in rs):
        for key, at in r.touched.items():
            acc[key] = acc.get(key, frozenset()) | frozenset(at)
        executed |= r.ran
        edges |= r.edges
    return Facts(acc, frozenset(executed), frozenset(edges), {pc: "runs"})


def left(runs):
    """The 64 KiB images init's ``runs`` (:func:`init`'s) leave."""
    return [bytes(r.mem[0:0x10000]) for rs in runs for r in rs]
