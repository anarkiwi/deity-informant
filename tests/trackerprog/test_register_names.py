"""One register naming, over the thirty cached builds: every register is a name.

Section 3.7's rule checked rather than asserted -- no number and no ``reg.N``
reaches an object.  A ``sets`` target, a ``globals.commit`` column and a
``meta.shadow.registers`` entry name a register the way ``universal.CHIP`` does.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from deity_informant.trackerprog.universal import CHIP, REG  # noqa: E402

import trackerprog_poison as TP  # noqa: E402

pytestmark = pytest.mark.hvsc

SIGILS, PITCH = "@#!", ("pitch", "freq")  # a cell, a global, a flag; the two of §5


def targets(x, out):
    """Every ``sets`` target of an object, wherever the grammar puts a stream."""
    if isinstance(x, dict):
        for k, v in x.items():
            if k == "sets":
                out += [r[0] for r in v]
            targets(v, out)
    elif isinstance(x, list):
        for v in x:
            targets(v, out)
    return out


@pytest.mark.parametrize("build", sorted(TP.BUILD))
def test_every_register_the_object_names_is_a_name(build):
    obj = TP.build_object(build, str(TP.DEFAULT_CACHE))
    for t in targets(obj, []):
        assert isinstance(t, str) and not t.startswith("reg."), t
        assert t[0] in SIGILS or t.startswith("shadow.") or t in CHIP or t in REG or t in PITCH, t
    for c in obj.get("globals", {}).get("commit", ()):
        assert c[0] in CHIP, c[0]
    for e in (obj["meta"].get("shadow") or {}).get("registers", ()):
        assert (e if isinstance(e, str) else e[0]) in CHIP, e
