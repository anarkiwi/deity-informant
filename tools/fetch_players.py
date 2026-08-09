"""Resolve and cache the canonical player sources docs/idiom-catalog.md cites.

Stage 1 of docs/register-model-lift-impl.md derives idioms from player source, so a
row must cite a file a reader gets byte-identically. The manifest pins a sha256 per
file; a run verifies it, ``--pin`` prints a new hash, ``--list`` shows provenance.
"""

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".oracle-cache" / "players"
TIMEOUT = 180

# SourceForge URLs carry a revision and GitHub URLs a commit: no branch moves under a pin.
PLAYERS = {
    "hubbard": {
        "title": "Rob Hubbard driver, commented disassembly (Anthony McSweeney)",
        "source": "https://www.1xn.org/text/C64/rob_hubbards_music.txt",
        "files": [
            (
                "rob_hubbards_music.txt",
                "https://www.1xn.org/text/C64/rob_hubbards_music.txt",
                "2ac4a7b5bfe5326042218fa1222df224d0cf95fc3952fc1ac0155bc0b6f89386",
            ),
        ],
    },
    "galway": {
        "title": "Martin Galway drivers, the composer's own sources (1st-generation player)",
        "source": "https://github.com/MartinGalway/C64_music",
        "note": (
            "author-published, not a community disassembly. The repo README dates the "
            "1st-generation player 1984-mid-1987 (Wizball, Green Beret, Arkanoid, Rambo) and "
            "names Athena as the first 2nd-generation player -- two players, both in the "
            "corpus, and the seven carry only a 1st-generation exemplar"
        ),
        "files": [
            (
                "wizball.asm",
                "https://raw.githubusercontent.com/MartinGalway/C64_music/"
                "a458a3687e27a63647bbe41968094f0e6d0965a0/wizball.asm",
                "b1510837750dcd05106be51ec71c50454c1d291293631e1b15de391b1c929dc7",
            ),
            (
                "rambload.asm",
                "https://raw.githubusercontent.com/MartinGalway/C64_music/"
                "a458a3687e27a63647bbe41968094f0e6d0965a0/rambload.asm",
                "78e7927e6c2e8f7d42c18d2e02bec764a2911d2d8180cec789020bdd60cdbb75",
            ),
            (
                "ocean_assembler_directives.txt",
                "https://raw.githubusercontent.com/MartinGalway/C64_music/"
                "a458a3687e27a63647bbe41968094f0e6d0965a0/ocean_assembler_directives.txt",
                "e61fa3676fa515dd871c139502eaaa37593777174a7f06038735125be108c0ee",
            ),
        ],
    },
    "defmon": {
        "title": "defMON player, round-tripped annotated disassembly",
        "source": "https://github.com/anarkiwi/undefmon",
        "note": (
            "defMON (https://defmon.vandervecken.com) ships binaries only; undefmon's "
            "defmon.asm reassembles byte-for-byte to the shipped image"
        ),
        "files": [
            (
                "defmon.asm",
                "https://raw.githubusercontent.com/anarkiwi/undefmon/"
                "ea029d087ae7880b7fc80f6cf0d3f8a697db6d64/defmon.asm",
                "0da40e1a874f02cc61832a41173547aa61810c66b39e97437e9b2de04adb6792",
            ),
        ],
    },
    "goattracker": {
        "title": "GoatTracker 2 player (Cadaver), SourceForge trunk r172",
        "source": "https://sourceforge.net/projects/goattracker2/",
        "files": [
            (
                "player.s",
                "https://svn.code.sf.net/p/goattracker2/code/!svn/bc/172"
                "/goattrk2/trunk/src/player.s",
                "5e6b87b206ba3f1dc1b30714a7b1cf3343e35a14ac0b84e48a5c508313176f60",
            ),
            (
                "altplayer.s",
                "https://svn.code.sf.net/p/goattracker2/code/!svn/bc/172"
                "/goattrk2/trunk/src/altplayer.s",
                "28a07203fda36cf3d70fc7300d46c9302af9f5606a9b1789673235c6e570aba2",
            ),
        ],
    },
    "sidwizard": {
        "title": "SID-Wizard player (Hermit), SourceForge trunk r398",
        "source": "https://sourceforge.net/projects/sid-wizard/",
        "note": (
            "upstream, not the anarkiwi/sid-wizard copy: the trunk serves the driver "
            "directly and carries the revision that copy was taken from"
        ),
        "files": [
            (
                "player.asm",
                "https://svn.code.sf.net/p/sid-wizard/code/!svn/bc/398"
                "/trunk/sources/include/player.asm",
                "5d59c0a6174ba6285238a5c4595b56e7cbe41a3d5cf89f582ec83c0a29bb3517",
            ),
            (
                "playadapter.inc",
                "https://svn.code.sf.net/p/sid-wizard/code/!svn/bc/398"
                "/trunk/sources/include/playadapter.inc",
                "7478ba2e08c63680ea593f92a8f1ed644d418f57435f230a11d3d9b0e5c078d2",
            ),
        ],
    },
    "follin": {
        "title": "Follin script interpreter, in-repo grammar",
        "source": "docs/follin-dispatch-study.md",
        "note": (
            "nothing to fetch: the reference is in this repository. Study section 3 is the "
            "operator grammar -- op byte, handler address, arity, semantics -- read off the "
            "handler code and validated against instrumented dispatch counts, so the family "
            "is anchored by handler address where the others need alignment"
        ),
        "files": [],
    },
}


def sha256(data):
    """Content hash of one fetched or cached file."""
    return hashlib.sha256(data).hexdigest()


def fetch(url):
    """Bytes at ``url``; network faults propagate."""
    with urllib.request.urlopen(url, timeout=TIMEOUT) as fh:
        return fh.read()


def pinned(want):
    """Whether a manifest hash names content: an all-zero hash is a placeholder."""
    return bool(want) and set(want) != {"0"}


def acquire(family, name, url, want, pin=False):
    """``(path, sha256)`` of the cached file, fetched when absent or stale.

    A file already matching its pin is not refetched; anything else is fetched and
    hashed, and ``--pin`` reports that hash where a run demands it equal the pin.
    Verification on every path is what makes the citation reproducible."""
    dst = CACHE / family / name
    if dst.is_file():
        got = sha256(dst.read_bytes())
        if got == want or (pin and not pinned(want)):
            return dst, got
    if not pinned(want) and not pin:
        sys.exit("%s/%s: unpinned; rerun with --pin and update the manifest" % (family, name))
    data = fetch(url)
    got = sha256(data)
    if pinned(want) and got != want:
        sys.exit(
            "%s/%s: sha256 mismatch, manifest %s but fetched %s (%d bytes)"
            % (family, name, want, got, len(data))
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return dst, got


def resolve(family, pin=False):
    """Cache every file of one family, a line per file."""
    spec = PLAYERS[family]
    if not spec["files"]:
        print("%-12s no source: %s" % (family, spec["note"]), flush=True)
        return
    for name, url, want in spec["files"]:
        path, got = acquire(family, name, url, want, pin)
        state = "pinned" if got == want else "PIN -> %s" % got
        print("%-12s %-24s %8d  %s" % (family, name, path.stat().st_size, state), flush=True)


def status(path, want):
    """One file's pin state: absent, ok, unpinned with its hash, or MISMATCH."""
    if not path.is_file():
        return "absent"
    got = sha256(path.read_bytes())
    if got == want:
        return "ok"
    return "MISMATCH %s" % got if pinned(want) else "unpinned %s" % got


def listing(families):
    """Provenance rows: family, upstream, cached path, and whether the pin holds."""
    for family in families:
        spec = PLAYERS[family]
        print("%s  %s" % (family, spec["title"]))
        print("  source: %s" % (spec["source"] or "none -- %s" % spec["note"]))
        for name, url, want in spec["files"]:
            path = CACHE / family / name
            print("  %-24s %s" % (name, url))
            print("  %-24s %s  %s" % ("", path.relative_to(ROOT), status(path, want)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("family", nargs="*", choices=sorted(PLAYERS) + [[]], help="default: all")
    ap.add_argument("--pin", action="store_true", help="fetch and print sha256 of unpinned files")
    ap.add_argument("--list", action="store_true", help="print the provenance table")
    args = ap.parse_args(argv)
    families = args.family or sorted(PLAYERS)
    if args.list:
        listing(families)
        return
    for family in families:
        resolve(family, args.pin)


if __name__ == "__main__":
    main()
