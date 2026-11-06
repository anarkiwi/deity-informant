"""Code-size attribution of a cgen translation compiled for a 6502 target (docs/cgen.md).

``python tools/csize.py OUT.c OUT.prg.elf --info info.json --image IMAGE --org ADDR``: per
function its ``.text`` bytes, the original instruction bytes it was emitted from, and its
bytes by origin (trap, dispatch, cell, arith16, other) from the ELF line table (``-g``).
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from elftools.elf.elffile import ELFFile

from deity_informant.lifter import MODE_LEN, OPS

ORIGINS = ("trap", "dispatch", "cell", "arith16", "other")
RUNTIME = re.compile(r"(?:rd|wr|oob_rd|oob_wr|codechk|rdC\d+)$")
DISPATCH = re.compile(r"\b(?:rd|wr|oob_rd|oob_wr|codechk|rdC\d+)\(")
ELEMENT = re.compile(r"\b(R\d+|C\d+)\[(0x[0-9A-F]+|\d+)\]\s*[!=]=\s*(?:0x)?[0-9A-F]+\b")
ORG = re.compile(r"#define (R\d+|C\d+)_ORG 0x([0-9A-F]+)")
HEAD = re.compile(r"^(?:static )?(?:__attribute__\(\(\w+\)\) )?\w[\w ]*?\b(\w+)\(.*\{$")


def line_rows(elf, source):
    """``[(start, end, line)]`` ranges the line table gives the lines of file ``source``,
    sorted; a row of line 0 (code merged from several lines) takes the line before it."""
    out = []
    dwarf = elf.get_dwarf_info()
    for cu in dwarf.iter_CUs():
        prog = dwarf.line_program_for_CU(cu)
        files = [Path(e.name.decode()).name for e in prog["file_entry"]]
        prev, line = None, 0
        for e in prog.get_entries():
            st = e.state
            if st is None:
                continue
            if prev is not None and st.address > prev[0]:
                out.append((prev[0], st.address, prev[1]))
            # DWARF 5 numbers files from 0, earlier versions from 1.
            idx = st.file if prog.header.version >= 5 else st.file - 1
            mine = 0 <= idx < len(files) and files[idx] == Path(source).name
            line = (st.line or line) if mine else 0
            prev = None if st.end_sequence or not mine else (st.address, line)
    return sorted(out)


def line_origins(text, cells, origins):
    """``({line: origin}, {line: C function})`` of C ``text`` (1-based lines); ``cells`` the
    code cells' addresses, ``origins`` the arrays' origins by name."""
    lines = text.splitlines()
    out, func, owner = {}, None, {}
    for n, line in enumerate(lines, 1):
        m = HEAD.match(line)
        func = m.group(1) if m else func
        owner[n] = func
        if RUNTIME.match(func or "") or DISPATCH.search(line):
            out[n] = "dispatch"
        elif any(origins.get(a, -0x10000) + int(o, 0) in cells for a, o in ELEMENT.findall(line)):
            out[n] = "cell"
        elif "uint16_t" in line or "<< 8" in line:
            out[n] = "arith16"
        else:
            out[n] = "other"
    for n, line in enumerate(lines, 1):
        if re.match(r"\s*trap\(", line):
            k = n - 1
            while k > 1 and not re.match(r"\s*(?:\} else )?if \(", lines[k - 1]):
                k -= 1
            out.update({j: "trap" for j in range(k, n + 1)})
    return out, owner


def original_bytes(image, org, pcs):
    """Instruction bytes of the instructions at ``pcs`` in ``image`` loaded at ``org``."""
    lens = (
        MODE_LEN[OPS[image[pc - org]][1]] if image[pc - org] in OPS else 1
        for pc in pcs
        if 0 <= pc - org < len(image)
    )
    return sum(lens)


def attribute(c_path, elf_path, info, image, org, header=None):
    """``{C function: {"text", "original", origin: bytes, ...}}`` of a compiled translation:
    each byte goes to the C line the line table gives it (inlined code to the function it
    came from); bytes of other files are left out."""
    text = Path(c_path).read_text(encoding="utf-8")
    h = Path(header).read_text(encoding="utf-8") if header else ""
    origins = {n: int(a, 16) for n, a in ORG.findall(h)}
    prov = info["provenance"]
    kinds, owner = line_origins(text, set(prov.get("cells", ())), origins)
    with open(elf_path, "rb") as f:
        rows = line_rows(ELFFile(f), c_path)
    split = {}
    for lo, hi, line in rows:
        split.setdefault(owner.get(line), Counter())[kinds.get(line, "other")] += hi - lo
    out = {}
    for name, c in split.items():
        pcs = prov["functions"].get(name, {}).get("pcs", ())
        out[name] = {"text": sum(c.values()), "original": original_bytes(image, org, pcs), **c}
    return out


def report(per, top=10):
    """A Markdown table of the ``top`` functions by ``.text`` and the totals by origin."""
    rows = [f"| function | .text | original | ratio | {' | '.join(ORIGINS)} |"]
    rows.append(f"|---|---|---|---|{'---|' * len(ORIGINS)}")
    total = Counter()
    for name, r in sorted(per.items(), key=lambda kv: -kv[1]["text"]):
        total.update(r)
        if len(rows) - 2 < top:
            ratio = f"{r['text'] / r['original']:.1f}" if r["original"] else "-"
            cols = " | ".join(str(r.get(o, 0)) for o in ORIGINS)
            rows.append(f"| {name} | {r['text']} | {r['original']} | {ratio} | {cols} |")
    cols = " | ".join(str(total.get(o, 0)) for o in ORIGINS)
    rows.append(f"| total | {total['text']} | {total['original']} | - | {cols} |")
    return "\n".join(rows)


def main(argv=None):
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("c")
    ap.add_argument("elf")
    ap.add_argument("--info", required=True, help="cgen info (its provenance) as JSON")
    ap.add_argument("--header", help="the .h: array origins, for tests of code cells")
    ap.add_argument("--image", required=True, help="the program's bytes")
    ap.add_argument("--org", type=lambda s: int(s, 0), required=True)
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args(argv)
    info = json.loads(Path(args.info).read_text(encoding="utf-8"))
    image = Path(args.image).read_bytes()
    per = attribute(args.c, args.elf, info, image, args.org, args.header)
    print(report(per, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
