"""Running ``mlir-opt`` and reading its generic-form output.

The tools come from ``PATH``, else the ``di-mlir`` Docker image (``docker/mlir.Dockerfile``);
:func:`parse` reads ``--mlir-print-op-generic`` text into :class:`Op` trees.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

IMAGE = "di-mlir"
DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "mlir.Dockerfile"
#: The structuring pipeline: promote cells, inline callees, fold, then ``cf`` to ``scf``.
PIPELINE = (
    "--canonicalize",
    "--mem2reg",
    "--inline",
    "--symbol-dce",
    "--canonicalize",
    "--cse",
    "--sccp",
    "--canonicalize",
    "--cse",
    "--lift-cf-to-scf",
    "--canonicalize",
    "--cse",
    "--mlir-print-op-generic",
)


def have_docker_image():
    """Whether Docker has the ``di-mlir`` image."""
    if not shutil.which("docker"):
        return False
    done = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    return done.returncode == 0


def runner(build=False, local=True):
    """The command prefix running ``mlir-opt``: none when on ``PATH`` (and ``local``), else
    ``docker run``; None when neither is available (``build`` builds the image if missing)."""
    if local and shutil.which("mlir-opt"):
        return []
    if not shutil.which("docker"):
        return None
    if not have_docker_image():
        if not build or not DOCKERFILE.exists():
            return None
        cmd = ["docker", "build", "-q", "-f", str(DOCKERFILE), "-t", IMAGE, str(DOCKERFILE.parent)]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return ["docker", "run", "--rm", "-i", IMAGE]


def run(prefix, argv, text):
    """The stdout of ``argv`` (under ``prefix``) fed ``text``."""
    done = subprocess.run([*prefix, *argv], input=text, capture_output=True, text=True, check=False)
    if done.returncode:
        raise RuntimeError(f"{argv[0]}: {done.stderr[:4000]}")
    return done.stdout


def optimize(text, prefix):
    """The generic-form text of MLIR ``text`` after :data:`PIPELINE`."""
    return run(prefix, ["mlir-opt", *PIPELINE], text)


@dataclass
class Block:
    """A block: its arguments ``[(name, type)]`` and operations."""

    args: list = field(default_factory=list)
    ops: list = field(default_factory=list)


@dataclass
class Op:  # pylint: disable=too-many-instance-attributes
    """A generic-form operation."""

    name: str
    res: list
    args: list
    props: str
    regions: list
    types: tuple = ((), ())
    parent: Block | None = None

    def prop(self, key):
        """The text of property ``key`` (quotes stripped), or None."""
        for item in _types(self.props.replace("->", "\u2192")):
            k, _, v = item.partition(" = ")
            if k.strip() == key:
                return v.strip().replace("\u2192", "->").strip('"')
        return None

    def int_prop(self, key):
        """The integer value of property ``key`` (``value = 5 : i8``)."""
        return int(self.prop(key).split(":")[0].strip().replace("true", "1").replace("false", "0"))


_HEAD = re.compile(
    r'^\s*(?:(%[\w$.-]+)(?::(\d+))? = )?"([\w.]+)"\(([^)]*)\)'
    r"(?:\[([^\]]*)\])?\s*(<\{.*?\}>)?\s*(.*)$"
)
_TAIL = re.compile(r"^(?:\{.*\}\s*)?:\s*\((.*?)\)\s*->\s*(.*)$")
_BLOCK = re.compile(r"^\s*\^[\w]+(?:\((.*)\))?:\s*$")


def _types(text):
    """A type list ``(i8, i16)``, ``i8`` or ``()``, split at top-level commas."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch in "<([") - (ch in ">)]")
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return [t for t in [*out, cur.strip()] if t]


def _results(name, count):
    if name is None:
        return []
    if count is None:
        return [name]
    return [f"{name}#{i}" for i in range(int(count))]


def _close(op, rest):
    m = _TAIL.match(rest.strip())
    assert m, rest
    op.types = (_types(m.group(1)), _types(m.group(2)))


def parse(text):
    """The top-level :class:`Op` of generic-form MLIR ``text``."""
    stack, top = [], None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("//") or s.startswith("#"):
            continue
        if s.startswith("^"):
            m = _BLOCK.match(s)
            args = []
            for a in filter(None, (x.strip() for x in (m.group(1) or "").split(","))):
                n, t = a.split(":", 1)
                args.append((n.strip(), t.strip()))
            region = stack[-1].regions[-1]
            if region and not region[-1].ops and not region[-1].args:
                region[-1].args = args
            else:
                region.append(Block(args))
            continue
        if s.startswith("}"):
            op = stack[-1]
            if s.startswith("}, {"):
                op.regions.append([Block()])
                continue
            assert s.startswith("})"), s
            stack.pop()
            _close(op, s[2:])
            continue
        m = _HEAD.match(s)
        assert m, s
        res, count, name, args, _succ, props, rest = m.groups()
        op = Op(
            name,
            _results(res, count),
            [a.strip() for a in args.split(",") if a.strip()],
            (props or "<{}>")[2:-2],
            [],
        )
        if stack:
            block = stack[-1].regions[-1][-1]
            op.parent = block
            block.ops.append(op)
        else:
            top = op
        if rest.startswith("({"):
            op.regions.append([Block()])
            stack.append(op)
            if rest.strip() != "({":
                raise ValueError(f"unexpected region head: {s}")
        else:
            _close(op, rest)
    return top
