"""Frama-C slice of a lifted C program on its bus writes, deopts and memory writes.

``python tools/cslice.py IN.c [-o OUT.c]``: Eva with ``-lib-entry`` and a slice keeping every
``io_write``, ``deopt`` and write to ``M`` (image ``framac/frama-c:29.0``); the stub bodies the
analysis needs are dropped again and the ``_slice_N`` renames undone.
"""

import argparse
import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

IMAGE = "framac/frama-c:29.0"
STUB = """#include <stdint.h>
{m} M[65536];
{m} volatile sid_bus;
{r} io_read({a} a) {{ return sid_bus ^ ({r})a; }}
void io_write({a} a, {r} v) {{ sid_bus = ({r})(a ^ v); }}
void deopt({a} pc) {{ sid_bus = ({r})pc; }}
{r} codechk({a} a, {r} v) {{ (void)a; (void)v; return 0; }}
"""
COMMAND = (
    "frama-c in.c stub.c -main play -lib-entry -eva -eva-verbose 0 -slice-calls io_write,deopt"
    " -slice-wr M -then-last -print -ocode out.c"
)


def stub(src):
    """The stub source matching the types ``src`` declares for ``M`` and the bus functions."""
    m = re.search(r"(\w+) M\[65536\]", src).group(1)
    r, a = re.search(r"(\w+) io_read\((\w+)", src).groups()
    return STUB.format(m=m, r=r, a=a)


def tidy(raw, src):
    """The slice ``raw`` without the stub bodies and renames; globals the harness reads kept."""
    out = re.split(r"^\w+ volatile sid_bus;", raw, flags=re.M)[0]
    out = re.sub(r"_slice_\d+", "", out)
    out = re.sub(r"^(\w+ M\[65536\];)", r"extern \1", out, flags=re.M)
    m = re.search(r"(\w+) M\[65536\]", src).group(1)
    arr = "[1]" if re.search(r"\bA\[1\]", src) else ""
    for reg in ("A", "SP"):
        if not re.search(rf"^[^\n(]*\b{reg}\b[^\n(]*;", out, flags=re.M):
            out += f"{m} {reg}{arr};\n"
    return out


def slice_c(src):
    """The Frama-C slice of the C text ``src``; the files travel as a tar stream."""
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as t:
        for name, text in (("in.c", src), ("stub.c", stub(src))):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    script = f"mkdir /tmp/w && cd /tmp/w && tar x && {COMMAND} >&2 && cat out.c"
    done = subprocess.run(
        ["docker", "run", "--rm", "-i", IMAGE, "sh", "-c", script],
        input=tar.getvalue(),
        capture_output=True,
        check=False,
    )
    if done.returncode:
        raise RuntimeError(f"frama-c: {done.stderr.decode()[-2000:]}")
    return tidy(done.stdout.decode(), src)


def main(argv=None):
    """Command line: slice a C file."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("-o", "--out")
    args = ap.parse_args(argv)
    text = slice_c(Path(args.src).read_text(encoding="utf-8"))
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
