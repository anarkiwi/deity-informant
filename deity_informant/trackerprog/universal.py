"""T3 -- the universal player over the trackerprog document alone: no program.

A render steps each accumulator a producer names (T1's records, ``accs``) and
applies the producers over the score's rows. The accumulators are not yet
executable records, so the player refuses each one it would step, by name, and
renders nothing: the certificate names the refusals and does not emit.
"""

from __future__ import annotations

import hashlib

from .document import from_json, text
from .refuse import Refusal


class DataPlayer:
    """Read a tagged trackerprog document; ``digest`` names its text."""

    def __init__(self, doc):
        self.digest = hashlib.sha256(text(doc).encode()).hexdigest()
        self.tp = from_json(doc)
        self.obs = []
        self.refusals = list(self.check())

    def check(self):
        accs = self.tp["accs"]
        seen = set()
        for p in self.tp["producers"]:
            for a in p.get("accs") or ():
                if a in seen:
                    continue
                seen.add(a)
                r = accs.get(a) or {}
                why = "%s %s over %s" % (r.get("kind"), r.get("register"), r.get("cell"))
                yield Refusal("acc not executable", a, p["site"]["pc"], why.strip())

    def render(self, ticks):
        """``(observable, trap)``: empty until the accumulators execute."""
        assert ticks >= 0
        return self.obs, None
