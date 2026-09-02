"""Every internal link in the documents resolves -- the file, and the anchor.

A link a reader cannot follow is a link the tree does not have, and a document
trim is exactly when one breaks.  Hermetic: it reads the tree's own markdown.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import doclinks  # noqa: E402


def test_every_internal_link_resolves():
    assert doclinks.check(doclinks.files()) == []


def test_an_anchor_is_the_headings_own_slug():
    assert (
        doclinks.slug("9.1 The object against the load band")
        == "91-the-object-against-the-load-band"
    )
    assert doclinks.slug("The lift, T0-T3") == "the-lift-t0-t3"
    assert doclinks.slug("`meta.row`, and the **act**") == "metarow-and-the-act"


def test_a_target_that_is_pseudocode_is_not_a_link():
    """``tick0cmd[newfx](A=newparam[X])`` names no file: no separator, no extension."""
    assert doclinks.PATH.search("prototype-trackerprog.md")
    assert doclinks.PATH.search("certificates/x.json")
    assert not doclinks.PATH.search("A=newparam[X]")
