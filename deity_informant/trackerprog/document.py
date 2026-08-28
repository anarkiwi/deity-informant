"""T3 -- the trackerprog document: S4-style tagged JSON, and the digest that names one."""

from __future__ import annotations

import hashlib
import json

from ..tuneprog.ir import dec, enc

KEYS = (
    "meta",
    "pitch",
    "streams",
    "accs",
    "instruments",
    "producers",
    "loops",
    "registers",
    "memory",
    "score",
    "globals",
    "inputs",
)


def to_json(tp):
    """``["$trackerprog", *KEYS]`` with every IR node and dict encoded."""
    return ["$trackerprog"] + [enc(tp[k]) for k in KEYS]


def from_json(doc):
    assert doc[0] == "$trackerprog"
    out = {k: dec(v) for k, v in zip(KEYS, doc[1:])}
    out["inputs"] = {int(k): v for k, v in out["inputs"].items()}
    if out["instruments"]:
        out["instruments"]["rows"] = {int(k): v for k, v in out["instruments"]["rows"].items()}
    return out


def text(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def digest(tp):
    """The sha256 of a trackerprog's document text: what a render is bound to."""
    return hashlib.sha256(text(to_json(tp)).encode()).hexdigest()
