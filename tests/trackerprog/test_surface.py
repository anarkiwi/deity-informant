"""The L0 to L6 surface over the families, as a cell count CI holds to.

What is asserted is the level each family reaches from planes this test builds
itself, so a level that stops reaching a tune fails the build and one that starts
reaching it fails too: this expectation and `prototype-passes.md` §6 move together.
"""

import sys
from pathlib import Path
from tempfile import mkdtemp

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tests" / "tuneprog"))
sys.path.insert(0, str(ROOT / "tools"))

from deity_informant.tuneprog import pipeline  # noqa: E402
from trackerprog_passes import from_l0  # noqa: E402

from _hvsc import (  # noqa: E402
    AUTOMATAS,
    CHAMELEON,
    COMMANDO,
    EMOMYST,
    GNG,
    GULDKORN,
    LINUS,
    tune_file,
)

pytestmark = pytest.mark.hvsc
CALLS = 1200

REACHES = {  # the level each family's own planes reach: L1 holds on all, L2 on none
    COMMANDO: "L2",
    GULDKORN: "L2",
    GNG: "L2",
    LINUS: "L2",
    EMOMYST: "L2",
    AUTOMATAS: "L2",
    CHAMELEON: "L2",
}

UNMEASURED = {  # left to ``tools/trackerprog_surface.py``, and why
    "Comic_Bakery.sid": "its levels take minutes where every other family takes seconds",
    "Quintessence.sid": "traps at L1 on $1301, a read its own trace records under no input kind",
}


def _stops(rel):
    """``(the level this family stops at, why)``, from planes built for this run."""
    out = Path(mkdtemp()) / "lift"
    assert pipeline.main([str(tune_file(rel)), "--out", str(out), "--calls", str(CALLS)]) == 0
    _levels, rep = from_l0(out)
    for name, got in rep["levels"].items():
        if got.get("pass") != "ok":
            return name, got.get("why", "")
    return "L6", ""


@pytest.mark.parametrize("rel", sorted(REACHES))
def test_each_family_stops_where_the_surface_says_it_does(rel):
    """One family, one cell: the level it reaches, and the level it does not."""
    stopped, why = _stops(rel)
    assert stopped == REACHES[rel], (
        "%s now stops at %s, not %s (%s). Move this expectation and "
        "docs/prototype-passes.md section 6 together." % (rel, stopped, REACHES[rel], why[:160])
    )


def test_the_structuring_reaches_every_family_and_the_phases_reach_none():
    """The count the surface is: L1 on every family measured, L2 on none of them."""
    assert set(REACHES.values()) == {"L2"}, REACHES
    assert not set(REACHES) & set(UNMEASURED)
