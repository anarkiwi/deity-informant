#!/usr/bin/env python3
"""Every internal link in the documents, resolved: the file, and the anchor.

A link a reader cannot follow is a link the tree does not have.  This walks
``docs/*.md`` and ``README.md``, takes every ``[text](target)`` naming a file of
the tree or an anchor, and checks that the file exists and that a ``#anchor``
names a heading of it --
GitHub's own slug: lower case, punctuation dropped, spaces to hyphens,
duplicates suffixed ``-1``, ``-2``.  A target with neither a path separator nor
a file extension is pseudocode in a document, not a link.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\[[^\]^]*?\]\(([^)\s]+)\)")
HEAD = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.M)
SKIP = re.compile(r"^(https?:|mailto:)")
PATH = re.compile(r"/|\.(md|json|py|yml|yaml|sh|txt|toml)$")


def slug(text):
    """A heading's GitHub anchor: its text, lowered, punctuation out, spaces in."""
    text = re.sub(r"[`*_]", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # a link in a heading
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"\s+", "-", text.strip())


def anchors(path):
    """Every anchor a file offers, duplicates numbered as GitHub numbers them."""
    seen, out = {}, set()
    for head in HEAD.findall(path.read_text()):
        s = slug(head)
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(s if not n else "%s-%d" % (s, n))
    return out


def check(files):
    """One line per broken link: the file, the link and what is missing."""
    bad = []
    for src in files:
        for target in LINK.findall(src.read_text()):
            if SKIP.match(target):
                continue
            name, _, anchor = target.partition("#")
            if name and not PATH.search(name):
                continue  # `tick0cmd[newfx](A=newparam[X])` is a program, not a link
            dest = (src.parent / name).resolve() if name else src
            if not dest.is_file():
                bad.append("%s -> %s: no such file" % (src.relative_to(ROOT), target))
            elif anchor and anchor not in anchors(dest):
                bad.append("%s -> %s: no such anchor" % (src.relative_to(ROOT), target))
    return bad


def files():
    """The documents this repository publishes: ``docs/*.md`` and the README."""
    return sorted((ROOT / "docs").glob("*.md")) + [ROOT / "README.md"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="doclinks.py", description=__doc__.splitlines()[0])
    ap.parse_args(argv)
    got = files()
    bad = check(got)
    for line in bad:
        print(line)
    print("%d documents, %d broken links" % (len(got), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
