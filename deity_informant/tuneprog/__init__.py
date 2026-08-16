"""tuneprog -- decompile a SID playroutine into a certified per-tick program.

Front end (this package's first stages):

* :mod:`.machine` (S0) -- machine image, entry/cadence discovery, init runner.
* :mod:`.trace` (S1) -- op-level tracer: sites, edges, logs, inputs, hashes.
* :mod:`.lift` (S2a) -- residualised lift (SMC cells become loads).
* :mod:`.cfg` (S2b) -- procedures, clones, tail calls, computed switches.
* :mod:`.regions` (S3) -- storage typing from the exact access relation.
"""

from __future__ import annotations

from .machine import CIA, Entry, MachineImage, Refusal, find_entries, init_runner, port_bank
from .trace import Trace, TraceVM, Tracer, input_kind, run_trace, site_key
from .lift import LiftedSite, lift_site, lift_trace
from .regions import Region, build_regions, index_regions
from .cfg import Proc, build_procs, procs_json

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
]
