"""Apply source mutations one at a time, rebuild, run checks and report which ones they catch.

python tools/mutsweep.py muts.json --build "maturin develop --release" --check "pytest -x t.py"

``muts.json`` maps a name to ``[file, old, new]`` edits (each ``old`` occurring exactly once);
files are restored after each mutation.
"""

import argparse
import json
import subprocess
import sys

from tqdm import tqdm


def apply(edits):
    """Apply ``[file, old, new]`` edits in order; the original contents by file."""
    saved = {}
    for path, old, new in edits:
        with open(path, encoding="utf-8") as f:
            cur = f.read()
        saved.setdefault(path, cur)
        if cur.count(old) != 1:
            restore(saved)
            raise ValueError(f"{path}: {old!r} occurs {cur.count(old)} times")
        with open(path, "w", encoding="utf-8") as f:
            f.write(cur.replace(old, new))
    return saved


def restore(saved):
    """Write back the contents ``apply`` saved."""
    for path, text in saved.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def run(cmd, timeout):
    """Exit code and the last lines of standard output (else error) of shell command ``cmd``."""
    try:
        p = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout, check=False
        )
        return p.returncode, (p.stdout.strip() or p.stderr.strip()).splitlines()[-3:]
    except subprocess.TimeoutExpired:
        return -1, ["timeout"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec")
    ap.add_argument("--build", required=True)
    ap.add_argument("--check", action="append", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args(argv)
    with open(a.spec, encoding="utf-8") as f:
        muts = json.load(f)
    results = {}
    for name in tqdm([n for n in muts if not a.only or n in a.only], desc="mutations"):
        saved = apply(muts[name])
        try:
            code, tail = run(a.build, a.timeout)
            results[name] = {"build": tail} if code else {c: run(c, a.timeout) for c in a.check}
        finally:
            restore(saved)
        tqdm.write(json.dumps({name: results[name]}))
    survived = [
        n for n, r in results.items() if "build" not in r and all(c == 0 for c, _ in r.values())
    ]
    print(json.dumps(results, indent=1))
    print("survived:", survived)
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
