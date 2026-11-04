"""Running ``mlir-opt`` and reading its generic-form output.

The tools come from ``PATH``, else the ``di-mlir`` Docker image (``docker/mlir.Dockerfile``);
:func:`parse` reads ``--mlir-print-op-generic`` text into :class:`Op` trees.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

IMAGE = "di-mlir"
DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "mlir.Dockerfile"
#: The optimization pipeline: promote cells, inline the callees not marked ``no_inline``, fold.
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
    "--mlir-print-op-generic",
    "--mlir-print-debuginfo",
    "--mlir-print-local-scope",
)
_CALLEE = re.compile(r"callee = @(\w+)(, no_inline)?\}")


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
    """The generic-form text of MLIR ``text`` after :data:`PIPELINE`, rerun while a callee
    marked ``no_inline`` is left with a single call site, which is then inlined."""
    out = run(prefix, ["mlir-opt", *PIPELINE], text)
    while True:
        sites = Counter(m.group(1) for m in _CALLEE.finditer(out))
        single = {m.group(1) for m in _CALLEE.finditer(out) if m.group(2)} & {
            n for n, k in sites.items() if k == 1
        }
        if not single:
            return out
        out = _CALLEE.sub(
            lambda m: f"callee = @{m.group(1)}}}" if m.group(1) in single else m.group(0), out
        )
        out = run(prefix, ["mlir-opt", *PIPELINE], out)


@dataclass
class Block:
    """A block: its label, arguments ``[(name, type)]`` and operations."""

    args: list = field(default_factory=list)
    ops: list = field(default_factory=list)
    name: str = ""


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
    succs: list = field(default_factory=list)
    pc: int | None = None

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

    def ints(self, key):
        """The integers of an ``array<i32: ...>`` or ``dense<...>`` property ``key``."""
        inner = (self.prop(key) or "<>").split("<", 1)[1].split(">")[0]
        body = inner.split(":", 1)[1] if inner.startswith("i") else inner
        return [int(x) for x in body.strip(" []").split(",") if x.strip()]

    def edges(self):
        """``[(successor label, [operand index], case)]`` of a ``cf`` terminator: ``case`` is
        True or False for ``cond_br``, a ``switch`` case value (None for its default) and None
        for ``br``."""
        if self.name == "cf.br":
            return [(self.succs[0], list(range(len(self.args))), None)]
        if self.name == "cf.cond_br":
            _, n, _ = self.ints("operandSegmentSizes")
            rest = range(1 + n, len(self.args))
            return [
                (self.succs[0], list(range(1, 1 + n)), True),
                (self.succs[1], list(rest), False),
            ]
        if self.name == "cf.switch":
            _, n, _ = self.ints("operandSegmentSizes")
            out, at = [(self.succs[0], list(range(1, 1 + n)), None)], 1 + n
            for succ, k, v in zip(
                self.succs[1:], self.ints("case_operand_segments"), self.ints("case_values")
            ):
                out.append((succ, list(range(at, at + k)), v))
                at += k
            return out
        return []


_HEAD = re.compile(
    r'^\s*(?:(%[\w$.-]+)(?::(\d+))? = )?"([\w.]+)"\(([^)]*)\)'
    r"(?:\[([^\]]*)\])?\s*(<\{.*?\}>)?\s*(.*)$"
)
_TAIL = re.compile(r"^(?:\{.*\}\s*)?:\s*\((.*?)\)\s*->\s*(.*)$")
_BLOCK = re.compile(r"^\s*\^([\w]+)(?:\((.*)\))?:\s*(?://.*)?$")


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


_PC = re.compile(r'"(0x[0-9a-f]+)"')


def _strip_locs(s):
    """``(s without its ``loc(...)`` locations, the instruction address of the last one or
    None)``: an inlined operation's location names its own instruction first."""
    out, pc, at = [], None, 0
    while (i := s.find(" loc(", at)) >= 0:
        out.append(s[at:i])
        depth, j = 0, i + 4
        while True:
            depth += (s[j] == "(") - (s[j] == ")")
            j += 1
            if not depth:
                break
        m = _PC.search(s, i, j)
        pc = int(m.group(1), 16) if m else None
        at = j
    out.append(s[at:])
    return "".join(out), pc


def _close(op, rest):
    m = _TAIL.match(rest.strip())
    assert m, rest
    op.types = (_types(m.group(1)), _types(m.group(2)))


def parse(text):
    """The top-level :class:`Op` of generic-form MLIR ``text``."""
    stack, top = [], None
    for line in text.splitlines():
        s, pc = _strip_locs(line.strip())
        if not s or s.startswith("//") or s.startswith("#"):
            continue
        if s.startswith("^"):
            m = _BLOCK.match(s)
            assert m, s
            args = []
            for a in filter(None, (x.strip() for x in (m.group(2) or "").split(","))):
                n, t = a.split(":", 1)
                args.append((n.strip(), t.strip()))
            region = stack[-1].regions[-1]
            if region and not region[-1].ops and not region[-1].args and not region[-1].name:
                region[-1].args, region[-1].name = args, m.group(1)
            else:
                region.append(Block(args, name=m.group(1)))
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
        res, count, name, args, succ, props, rest = m.groups()
        op = Op(
            name,
            _results(res, count),
            [a.strip() for a in args.split(",") if a.strip()],
            (props or "<{}>")[2:-2],
            [],
            succs=[x.strip()[1:] for x in (succ or "").split(",") if x.strip()],
            pc=pc,
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
