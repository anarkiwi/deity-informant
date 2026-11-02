"""The ``di-vtools`` image (SIDId, sidreloc, gt2reloc): build, availability and runs.

``python tools/vtools.py build|check`` builds the image or reports whether it exists.
Bind mounts must come from a host path the Docker socket proxy allows (``/scratch``, not
``/home``, ``/root`` or ``/proc``); ``out/`` inside a worktree under ``/scratch`` qualifies.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "di-vtools"
DOCKERFILE = ROOT / "docker" / "vtools.Dockerfile"
CONTEXT = ROOT / "docker" / "vtools"
BUILD_TIMEOUT = 3600


def available(image=IMAGE):
    """Whether Docker answers and ``image`` exists locally."""
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def build(image=IMAGE):
    """Build ``image`` from ``docker/vtools.Dockerfile`` (each tool's stage is cached)."""
    cmd = ["docker", "build", "-f", str(DOCKERFILE), "-t", image, str(CONTEXT)]
    subprocess.run(cmd, check=True, timeout=BUILD_TIMEOUT)


def docker_cmd(args, mounts=(), env=None, image=IMAGE, interactive=False):
    """The ``docker run`` argv for ``args`` with ``(host, container, ro)`` ``mounts``."""
    cmd = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}"]
    cmd += ["-i"] if interactive else []
    for host, cont, ro in mounts:
        cmd += ["-v", f"{Path(host).resolve()}:{cont}" + (":ro" if ro else "")]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    return cmd + [image, *args]


def run(args, mounts=(), env=None, image=IMAGE, stdin=None, timeout=600):
    """Run ``args`` in ``image``; the completed process (text output, no check)."""
    cmd = docker_cmd(args, mounts, env, image, interactive=stdin is not None)
    return subprocess.run(
        cmd, input=stdin, capture_output=True, encoding="latin-1", timeout=timeout, check=False
    )


def sid_body(data):
    """``(load, offset, body)`` of a PSID/RSID file: the C64 load address, the file offset of
    the first loaded byte, and the loaded bytes."""
    if data[:4] not in (b"PSID", b"RSID") or len(data) < 0x76:
        raise ValueError("not a PSID/RSID file")
    off, load = struct.unpack(">HH", data[6:10])
    if load == 0:
        return data[off] | data[off + 1] << 8, off + 2, data[off + 2 :]
    return load, off, data[off:]


def workdir(name):
    """A fresh directory under ``out/`` (bind-mountable from the worktree) named ``name-*``."""
    base = ROOT / "out"
    base.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{name}-", dir=base))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["build", "check"])
    ap.add_argument("--image", default=IMAGE)
    args = ap.parse_args(argv)
    if args.command == "build":
        build(args.image)
    print(f"{args.image}: {'available' if available(args.image) else 'missing'}")
    return 0 if available(args.image) else 1


if __name__ == "__main__":
    sys.exit(main())
