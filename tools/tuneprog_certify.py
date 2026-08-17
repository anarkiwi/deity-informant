#!/usr/bin/env python3
"""Certify a SID tune: trace -> lift -> regions -> procedures -> IR -> S4 -> Python -> verify.

A thin wrapper around :mod:`deity_informant.tuneprog.pipeline`, which is also what
``deity-informant tuneprog`` runs. Every stage's artefacts land in ``--out DIR``
and the long stages are chunked: each invocation works for ``--budget`` CPU
seconds and exits 2 when there is more to do, so a long certificate is a handful
of short runs::

    until python3 tools/tuneprog_certify.py TUNE.sid --out out/tune --until-period --resume
    do :; done

``--sid-model`` pins ``$D41B`` bit 0 (the register a tune reads at init to tell a
6581 from an 8580), which certifies the tune under either model.
"""

import os
import sys
from pathlib import Path

for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # keep process_time() a measure of this work only
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
from deity_informant.tuneprog import pipeline  # noqa: E402

MORE = pipeline.MORE


def main(argv=None):
    return pipeline.run(pipeline.parser("tuneprog_certify.py").parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
