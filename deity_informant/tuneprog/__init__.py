"""tuneprog -- decompile a SID playroutine into a certified per-tick program.

Front end (trace-driven recovery):

* :mod:`.machine` (S0) -- machine image, entry/cadence discovery, init runner.
* :mod:`.tracevm` / :mod:`.trace` (S1) -- op-level tracing VM and the tracer
  that drives it: sites, edges, logs, inputs, per-tick state hashes.
* :mod:`.lift` (S2a) -- residualised lift (SMC cells become loads).
* :mod:`.cfg` (S2b) -- procedures, clones, tail calls, computed switches.
* :mod:`.regions` (S3) -- storage typing from the exact access relation.
* :mod:`.jumptab` (S2) -- the static closure of a patched ``JMP``'s table;
  :mod:`.closure` (S2) -- the bounded static closure of the branch directions the
  trace never took, as zero-coverage sites the same front end builds.

Middle and back end (the executable program and its certificate):

* :mod:`.ir` -- the IR of design section 4, its JSON form and its algebra;
  :mod:`.interp` -- the machine state and the reference interpreter (the semantics).
* :mod:`.build` -- front end -> IR: one procedure per CFG procedure, one block
  per node, memory ops typed by region and envelope.
* :mod:`.ssa` (S4) -- SSA over registers/flags/uniques, DCE, copy and constant
  propagation; :mod:`.idioms` (S4) -- peepholes that turn the 6510's flag
  algebra back into relational tests; :mod:`.frames` / :mod:`.stack` (S4) -- the
  machine stack as frames, eliminated where every load is its own frame's push.
* :mod:`.emit` (S7) -- Python code generation and the certificate writer.
* :mod:`.verify` (S8) -- per-call differential verification against the trace,
  periodicity, chunked and resumable; :mod:`.period` -- why a subtune that never
  repeated does not (counter, drifting accumulator, or an aperiodic tune).

Presentation over the certified program (it is never edited):

* :mod:`.structure` / :mod:`.loops` (S5) -- loops, if/else, switch, the ``for``
  a recurrence or a family's copies makes, the phase variable.
* :mod:`.inline` / :mod:`.texture` / :mod:`.frame` / :mod:`.word` (S6) -- value
  folding, machine-texture removal, naming a residual program's frames, 16-bit
  views.
* :mod:`.recover` / :mod:`.views` / :mod:`.copyview` (S6) -- struct views, roles
  and names for the storage; a per-copy column as the operand it stands for.
* :mod:`.fold` / :mod:`.tails` / :mod:`.unroll` (S6) -- outlined helpers, shared
  tails as procedures, consecutive isomorphic runs as one ``for``.
* :mod:`.live` (S6) -- the values, arguments and returns a reader must see.
* :mod:`.pseudocode` / :mod:`.printer` (S7 text form) -- the rendered statements
  and the ``tuneprog.md`` document around them.

:mod:`.pipeline` drives all of it; ``tools/tuneprog_certify.py`` and
``deity-informant tuneprog`` are wrappers around it. The stage boundaries are the
module-level entry points ``build.build_ir``, ``ssa.simplify``,
``stack.eliminate``, ``emit.emit_python``, ``verify.verify``, ``structure.structure``,
``recover.recover`` and ``printer.render``. :mod:`.irwalk` and :mod:`.graph` are
the traversals every stage shares, and :mod:`.ghidra_facts` / :mod:`.ghidra_compare`
export the trace's facts to a headless Ghidra and score the two decompilations
against each other. ``docs/tuneprog.md`` is the guide.
"""

from __future__ import annotations

from .machine import CIA, Entry, MachineImage, Refusal, find_entries, init_runner, port_bank
from .trace import Trace, Tracer, run_trace, site_key
from .tracevm import TraceVM, input_kind
from .lift import LiftedSite, lift_site, lift_trace
from .regions import Region, build_regions, index_regions
from .cfg import Proc, build_procs, procs_json
from .interp import Interp, Machine
from .ir import Block, Rgn, TrapError, Tuneprog
from .build import build_ir
from .lower import ops_to_stmts, straightline
from .ssa import simplify
from .stack import eliminate
from .idioms import rewrite
from .emit import PyProgram, certificate, emit_python, write_certificate
from .verify import Reference, Verifier, certify
from .structure import view
from .recover import Names
from .live import needed, wants
from .printer import render

__all__ = [
    "CIA",
    "Entry",
    "MachineImage",
    "Refusal",
    "find_entries",
    "init_runner",
    "port_bank",
    "Trace",
    "TraceVM",
    "Tracer",
    "input_kind",
    "run_trace",
    "site_key",
    "LiftedSite",
    "lift_site",
    "lift_trace",
    "Region",
    "build_regions",
    "index_regions",
    "Proc",
    "build_procs",
    "procs_json",
    "Block",
    "Interp",
    "Machine",
    "Rgn",
    "TrapError",
    "Tuneprog",
    "build_ir",
    "ops_to_stmts",
    "straightline",
    "simplify",
    "eliminate",
    "rewrite",
    "PyProgram",
    "certificate",
    "emit_python",
    "write_certificate",
    "Reference",
    "Verifier",
    "certify",
    "view",
    "Names",
    "needed",
    "wants",
    "render",
]
