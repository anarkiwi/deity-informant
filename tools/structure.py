"""Structure the MLIR of ``tools/pcode2mlir.py`` and lower it to C through EmitC.

``python tools/structure.py IN.mlir [-o OUT.c]``: ``mlir-opt`` (``lift-cf-to-scf``, then the
EmitC conversions) and ``mlir-translate``, from ``PATH`` or else the ``di-mlir`` Docker image
(``docker/mlir.Dockerfile``, built when absent).
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

IMAGE = "di-mlir"
DOCKERFILE = Path(__file__).resolve().parents[1] / "docker" / "mlir.Dockerfile"
LIFT = ["--canonicalize", "--cse", "--mem2reg", "--lift-cf-to-scf", "--canonicalize", "--cse"]
LOWER = ["--canonicalize", "--cse", "--convert-to-emitc", "--reconcile-unrealized-casts"]
LOWER += ["--form-expressions"]
HEADER = "#include <stdint.h>\n#include <stddef.h>\n#include <stdbool.h>\n"


def have_docker_image():
    """Whether the ``di-mlir`` image exists."""
    if not shutil.which("docker"):
        return False
    return (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True, check=False
        ).returncode
        == 0
    )


def runner(build=False):
    """The command prefix running the MLIR tools: none when on ``PATH``, else ``docker run``;
    None when neither is available (``build`` builds the image if missing)."""
    if shutil.which("mlir-opt") and shutil.which("mlir-translate"):
        return []
    if not shutil.which("docker"):
        return None
    if not have_docker_image():
        if not build:
            return None
        cmd = ["docker", "build", "-q", "-f", str(DOCKERFILE), "-t", IMAGE, str(DOCKERFILE.parent)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return ["docker", "run", "--rm", "-i", IMAGE]


def run(prefix, argv, text):
    """The stdout of ``argv`` fed ``text``."""
    done = subprocess.run([*prefix, *argv], input=text, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(f"{argv[0]}: {done.stderr[:2000]}")
    return done.stdout


def structure(mlir, prefix):
    """C text for the ``cf`` MLIR ``mlir``: structured by ``lift-cf-to-scf`` and printed by
    EmitC. Undefined (``ub.poison``) values lifting introduces on untaken paths become zero."""
    scf = run(prefix, ["mlir-opt", *LIFT], mlir)
    scf = re.sub(r"ub\.poison : (i\d+)", r"arith.constant 0 : \1", scf)
    emitc = run(prefix, ["mlir-opt", *LOWER], scf)
    cpp = run(prefix, ["mlir-translate", "--mlir-to-cpp"], emitc)
    cpp = re.sub(r"^extern (.*\]) = \{0\};", r"\1;", cpp, flags=re.M)
    protos = "".join(
        f"void {name}(void);\n" for name in re.findall(r"^void (\w+)\(\) \{", cpp, re.M)
    )
    return HEADER + protos + cpp


def main(argv=None):
    """Command line: structure an MLIR file into C."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mlir")
    ap.add_argument("-o", "--out")
    args = ap.parse_args(argv)
    prefix = runner(build=True)
    if prefix is None:
        sys.exit("needs mlir-opt and mlir-translate on PATH, or docker")
    text = structure(Path(args.mlir).read_text(encoding="utf-8"), prefix)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
