"""S1 -- the control-flow record: edges, call/return sites and the shadow stack.

Split from :mod:`.tracevm`, which owns memory attribution and the step loop. Each
record is a cell a site holds a reference to, so counting one costs an increment;
the shadow stack pairs JSR with RTS and sums each procedure's register summary.
"""

from __future__ import annotations

from collections import Counter

from .tracesite import S_E0, S_EK


class FlowRecorder:
    """Edge, call and return records, the shadow stack, procedure register summaries."""

    edges = calls = rets = summaries = None
    shadow = top = None
    unmatched_rts = max_depth = 0

    def init_flow(self):
        """The empty record; a mixin's ``__init__`` is the VM's."""
        self.edges = {}
        self.calls = {}
        self.rets = {}
        self.summaries = {}
        self.shadow = []
        self.top = None
        self.unmatched_rts = 0
        self.max_depth = 0

    def edge_slot(self, ek, target, kind):
        """The ``[kind, count]`` cell of one edge, created on first sight."""
        k = (ek[0], ek[1], target)
        e = self.edges.get(k)
        if e is None:
            e = self.edges[k] = [kind, 0]
        return e

    def call_slot(self, ek, ret):
        """The call record of a JSR site, created on first sight."""
        c = self.calls.get(ek)
        if c is None:
            c = self.calls[ek] = {"targets": Counter(), "ret_pc": ret, "count": 0}
        return c

    def ret_slot(self, ek):
        """The return record of an RTS/RTI site, created on first sight."""
        r = self.rets.get(ek)
        if r is None:
            r = self.rets[ek] = {
                "matched": Counter(),
                "unmatched": 0,
                "targets": Counter(),
                "loose": Counter(),
            }
        return r

    def push_frame(self, site, ret, target):
        """Push a shadow frame (``site`` is ``None`` for a driver's dummy return)."""
        self.top = f = [site, ret, target, 0, 0]
        self.shadow.append(f)
        if len(self.shadow) > self.max_depth:
            self.max_depth = len(self.shadow)

    def clear_frames(self):
        """Drop every shadow frame: a driver's entry and exit boundaries."""
        self.shadow.clear()
        self.top = None

    def _varying_edge(self, t, nxt, kind):
        """Count an edge whose target is computed, memoised per site."""
        seen = t[S_E0]
        e = seen.get(nxt)
        if e is None:
            e = seen[nxt] = self.edge_slot(t[S_EK], nxt, kind)
        e[1] += 1

    def _return(self, r, nxt):
        """Pair one RTS/RTI with the frame it unwinds, and summarise that procedure."""
        r["targets"][nxt] += 1
        top = self.top
        if top is not None and top[1] == nxt:
            site, _ret, target, frd, fwr = self.shadow.pop()
            p = self.shadow[-1] if self.shadow else None
            self.top = p
            r["matched"][site if site is not None else -1] += 1
            if p is not None:
                p[3] |= frd & ~p[4]
                p[4] |= fwr
            s = self.summaries.get(target)
            if s is None:
                s = self.summaries[target] = {"rd": 0, "wr": 0, "count": 0}
            s["rd"] |= frd
            s["wr"] |= fwr
            s["count"] += 1
        else:
            r["unmatched"] += 1
            r["loose"][nxt] += 1
            self.unmatched_rts += 1
