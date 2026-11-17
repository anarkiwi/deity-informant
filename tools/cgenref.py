"""Generated C of a deity-informant wheel, kept as a reference and diffed against one.

Covers gravity-inversion's tunes and fuzz players and, with ``--tests``, every
``cgen.translate`` the named test files make: ``.c``, ``.h`` and ``info`` without timings.
"""

import argparse
import concurrent.futures
import difflib
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import numpy as np
from tqdm import tqdm

TUNES = {
    "automatas": "Automatas.sid",
    "commando": "Hubbard_Rob_Commando.asm",
    "emomyst": "Emomyst.sid",
    "ghouls": "Ghouls_n_Ghosts.sid",
    "incoherent": "Incoherent_Nightmare_tune_2.sid",
}
SEEDS = range(3)
DEPS = ["pysidtracker", "vagrant-moses[cpu,cgen]", "tqdm", "pytest-timeout"]
TEST_FILES = ["tests/test_cgen.py", "tests/test_decimal_sleigh.py", "tests/test_liftsweep_tool.py"]
DUMP = "CGENREF_DUMP"
HERE = pathlib.Path(__file__).resolve().parent
USAGE = """
  cgenref.py check main.whl out/ref --gi GI --tests          # reference from main's wheel
  cgenref.py check new.whl out/new --gi GI --tests --ref out/ref
"""


def canonical(obj):
    """JSON-ready ``obj``: mapping keys as strings, sets sorted, arrays as lists."""
    if isinstance(obj, dict):
        return {str(k): canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (set, frozenset)):
        return sorted((canonical(v) for v in obj), key=repr)
    if isinstance(obj, (list, tuple, range)):
        return [canonical(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    return repr(obj)


def digest(h, obj):
    """Feed ``obj`` to the hash ``h`` by value: buffers by their bytes, callables by name."""
    if isinstance(obj, np.ndarray):
        h.update(obj.dtype.str.encode() + obj.tobytes())
    elif isinstance(obj, (bytes, bytearray, memoryview)):
        h.update(b"b" + bytes(obj))
    elif isinstance(obj, dict):
        for k in sorted(obj, key=repr):
            digest(h, k)
            digest(h, obj[k])
    elif isinstance(obj, (list, tuple, range, set, frozenset)):
        h.update(b"(")
        for v in sorted(obj, key=repr) if isinstance(obj, (set, frozenset)) else obj:
            digest(h, v)
        h.update(b")")
    elif callable(obj):
        h.update(f"{getattr(obj, '__module__', '')}.{getattr(obj, '__qualname__', obj)}".encode())
    else:
        h.update(repr(obj).encode())


def write(out, name, c, h, info):
    """``out/name.{c,h,json}``; ``info`` without its timings."""
    out.mkdir(parents=True, exist_ok=True)
    info = {k: v for k, v in info.items() if k != "times"}
    (out / f"{name}.c").write_text(c, encoding="utf-8")
    (out / f"{name}.h").write_text(h, encoding="utf-8")
    text = json.dumps(canonical(info), indent=1, sort_keys=True)
    (out / f"{name}.json").write_text(text + "\n", encoding="utf-8")


def _gi_tools(gi):
    if str(gi / "tools") not in sys.path:
        sys.path.insert(0, str(gi / "tools"))


def acme():
    """The ``jennings-acme`` of the running interpreter's environment, else PATH's."""
    own = pathlib.Path(sys.executable).with_name("jennings-acme")
    return str(own) if own.exists() else "jennings-acme"


def psid_of(case, gi, fixtures, tmp):
    """PSID bytes of a tune or a ``fuzz:style:seed`` player."""
    tmp = pathlib.Path(tmp)
    if case.startswith("fuzz:"):
        _gi_tools(gi)
        import fuzzplayer  # pylint: disable=import-outside-toplevel,import-error

        _, style, seed = case.split(":")
        src = tmp / "p.asm"
        src.write_text(fuzzplayer.player(style, int(seed)), encoding="ascii")
    elif TUNES[case].endswith(".asm"):
        src = fixtures / TUNES[case]
    else:
        return (fixtures / TUNES[case]).read_bytes()
    subprocess.run([acme(), "-o", tmp / "p.sid", src], check=True, capture_output=True)
    return (tmp / "p.sid").read_bytes()


def gen_one(case, gi, fixtures, out):
    """Translate one case with gravity-inversion's ``transliterate`` into ``out``."""
    _gi_tools(gi)
    import transliterate  # pylint: disable=import-outside-toplevel,import-error

    name = case.replace(":", "_")
    with tempfile.TemporaryDirectory() as tmp:
        psid = psid_of(case, gi, fixtures, tmp)
    write(out, name, *transliterate.transliterate(psid, f"{name}.h"))
    return case


def cases(gi):
    _gi_tools(gi)
    import fuzzplayer  # pylint: disable=import-outside-toplevel,import-error

    return [*TUNES, *(f"fuzz:{s}:{n}" for s in fuzzplayer.STYLES for n in SEEDS)]


def gen(args):
    out = pathlib.Path(args.out).resolve()
    gi = pathlib.Path(args.gi).resolve()
    fixtures = pathlib.Path(args.fixtures or gi / "fixtures").resolve()
    todo = cases(gi)
    with concurrent.futures.ProcessPoolExecutor(args.jobs) as pool:
        futs = [pool.submit(gen_one, c, gi, fixtures, out / "gi") for c in todo]
        for f in tqdm(concurrent.futures.as_completed(futs), total=len(futs), desc="translate"):
            f.result()
    if args.tests:
        env = {**os.environ, DUMP: str(out / "tests"), "PYTHONPATH": str(HERE)}
        root = pathlib.Path(args.tests_root).resolve()
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "cgenref", "-p", "no:cacheprovider"]
        cmd += ["-c", str(root / "pyproject.toml"), "--rootdir", str(root)]
        cmd += ["-n", str(args.jobs), *(str(root / t) for t in args.tests)]
        print(" ".join(cmd), flush=True)
        subprocess.run(cmd, env=env, cwd=out, check=False)
    return 0


def diff(ref, new, limit=40):
    """Number of files differing between output directories ``ref`` and ``new``; a test
    translation made by only one of them is not counted."""
    ref, new = pathlib.Path(ref), pathlib.Path(new)
    names = {p.relative_to(d) for d in (ref, new) for p in d.glob("[gt]*/*") if p.is_file()}
    names = sorted(n for n in names if n.suffix in (".c", ".h", ".json"))
    bad = common = 0
    for n in tqdm(names, desc="diff"):
        a, b = ref / n, new / n
        if not (a.exists() and b.exists()):
            if n.parts[0] != "tests":
                tqdm.write(f"{n}: only in {ref if a.exists() else new}")
                bad += 1
            continue
        common += 1
        if a.read_bytes() != b.read_bytes():
            bad += 1
            lines = difflib.unified_diff(
                a.read_text().splitlines(), b.read_text().splitlines(), str(a), str(b), lineterm=""
            )
            tqdm.write("\n".join(list(lines)[:limit]))
    print(f"{bad} differing of {common} common files")
    return bad


def check(args):
    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    py = out / "venv" / "bin" / "python"
    if not py.exists():
        subprocess.run([sys.executable, "-m", "venv", out / "venv"], check=True)
    pins = []
    if args.ref:
        frozen = (pathlib.Path(args.ref) / "freeze.txt").read_text().splitlines()
        cons = out / "constraints.txt"
        cons.write_text("\n".join(ln for ln in frozen if "deity" not in ln) + "\n")
        pins = ["-c", str(cons)]
    whl = str(pathlib.Path(args.wheel).resolve())
    pip = [py, "-m", "pip", "install", "-q", "--index-url", args.index_url, *pins]
    subprocess.run([*pip, f"{whl}[dev]", *DEPS], check=True)
    subprocess.run([*pip, "--force-reinstall", "--no-deps", whl], check=True)
    freeze = subprocess.run([py, "-m", "pip", "freeze"], check=True, capture_output=True)
    (out / "freeze.txt").write_bytes(freeze.stdout)
    rest = ["--gi", args.gi, "-j", str(args.jobs), "--tests-root", args.tests_root]
    rest += ["--fixtures", args.fixtures] if args.fixtures else []
    rest += ["--tests", *args.tests] if args.tests else []
    subprocess.run([py, __file__, "gen", str(out), *rest], check=True, cwd=out)
    return min(diff(args.ref, out), 1) if args.ref else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, epilog=USAGE, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("gen", "check"):
        p = sub.add_parser(name)
        if name == "check":
            p.add_argument("wheel")
            p.add_argument("--ref", help="reference output directory to diff against")
            p.add_argument("--index-url", default="https://pypi.org/simple")
        p.add_argument("out")
        p.add_argument("--gi", required=True, help="gravity-inversion tree")
        p.add_argument("--fixtures", help="its fixtures (default GI/fixtures)")
        p.add_argument("-j", "--jobs", type=int, default=8)
        p.add_argument("--tests", nargs="*", help=f"test files to dump (bare: {TEST_FILES})")
        p.add_argument("--tests-root", default=str(HERE.parent))
    p = sub.add_parser("diff")
    p.add_argument("ref")
    p.add_argument("new")
    args = ap.parse_args(argv)
    if getattr(args, "tests", None) is not None and not args.tests:
        args.tests = TEST_FILES
    if args.cmd == "diff":
        return min(diff(args.ref, args.new), 1)
    return gen(args) if args.cmd == "gen" else check(args)


def pytest_configure(config):
    """Plugin (``-p cgenref`` with ``$CGENREF_DUMP`` set): each ``cgen.translate`` result
    written under ``$CGENREF_DUMP``, named by the digest of its arguments."""
    out = os.environ.get(DUMP)
    if not out:
        return
    from deity_informant import cgen  # pylint: disable=import-outside-toplevel

    inner = cgen.translate

    def translate(*a, **kw):
        result = inner(*a, **kw)
        h = hashlib.sha256()
        digest(h, (a, kw))
        write(pathlib.Path(out), h.hexdigest()[:24], *result)
        return result

    cgen.translate = translate
    config.add_cleanup(lambda: setattr(cgen, "translate", inner))


if __name__ == "__main__":
    sys.exit(main())
