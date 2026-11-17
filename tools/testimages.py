"""Provision the Docker images the tests use, so no test spends its timeout building one.

Builds ``di-mlir`` (``mlir-opt``) when missing and pulls the Frama-C slicer and the llvm-mos
SDK; a test whose image is absent skips or provisions it itself.
"""

import subprocess
import sys

from tqdm import tqdm

import cslice  # pylint: disable=import-error
from deity_informant.cgen import mlir

MOS = "ghcr.io/anarkiwi/docker-mos-llvm-sdk:v23.4.0"
PULLS = (cslice.IMAGE, MOS)


def main():
    steps = [("build " + mlir.IMAGE, lambda: mlir.runner(build=True, local=False))]
    steps += [
        (f"pull {i}", lambda i=i: subprocess.run(["docker", "pull", "-q", i], check=True))
        for i in PULLS
    ]
    for name, step in (progress := tqdm(steps, desc="images")):
        progress.set_postfix_str(name)
        step()
    return 0


if __name__ == "__main__":
    sys.exit(main())
