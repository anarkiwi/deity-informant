"""Section 9.1's measurement: the object against the tune's own load band.

Section 9 asked whether the score compresses better than the program that played
it, and measured against ``tuneprog.md`` -- a pretty-printed decompilation, which
section 3.4 rules is not a measurement.  The program that played the tune is the
binary, so the denominator here is the PSID load band with its header stripped.

A tune's band holds *every* subtune, so the numerator is every certified
subtune's object concatenated, and the row states the coverage: a ratio over a
fraction of a tune's subtunes is not a comparison.
"""

from __future__ import annotations

import json
import lzma

from ..c64 import _psid_body, psid_songs

PRESET = 9 | lzma.PRESET_EXTREME


def xz(data):
    """``xz -9e`` of some bytes, section 8.3's own unit."""
    return len(lzma.compress(data, preset=PRESET))


def compact(obj):
    """The object as the bytes a document measures: one line, no spaces."""
    return json.dumps(obj, separators=(",", ":")).encode()


def band(data):
    """``(raw, xz)`` of a PSID/RSID file's load band, header and load word off."""
    body = _psid_body(data)[1]
    return len(body), xz(body)


def songs(data):
    """How many subtunes the file declares, which is what its band holds."""
    return psid_songs(data)[0]


def halves(obj):
    """``{score, rest}`` of one object: the score materialised, and the sound half."""
    score = compact(obj["score"])
    rest = compact({k: v for k, v in obj.items() if k != "score"})
    return {
        "score_raw": len(score),
        "score_xz": xz(score),
        "rest_raw": len(rest),
        "rest_xz": xz(rest),
    }


def tune_row(data, objs, name=""):
    """One row of section 9.1: a tune's band against its certified objects."""
    raw, band_xz = band(data)
    joined = b"".join(compact(o) for o in objs)
    return {
        "tune": name,
        "songs": songs(data),
        "certified": len(objs),
        "band_raw": raw,
        "band_xz": band_xz,
        "object_xz": xz(joined),
        "summed_xz": sum(xz(compact(o)) for o in objs),
        "ratio": xz(joined) / band_xz,
    }


def line(row):
    """The one line a document row quotes per tune."""
    cover = (
        ""
        if row["songs"] == row["certified"]
        else "  (%d of %d)"
        % (
            row["certified"],
            row["songs"],
        )
    )
    return "%-32s %2d/%-3d %8d %9d  %5.2fx%s" % (
        row["tune"],
        row["certified"],
        row["songs"],
        row["band_xz"],
        row["object_xz"],
        row["ratio"],
        cover,
    )
