"""One identity per cached tune, and the corpus enumeration the sweeps share.

A stem does not identify a tune: eight stems in the cache name two tunes each, so a
row keyed by stem merges two and ``--tunes Commando`` runs two while looking like one.
The identity is the cache-relative path without its suffix, unique by construction.
"""

import signal
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HVSC = ROOT / ".oracle-cache" / "hvsc"
CACHE = HVSC.resolve()
CAP_S = 1800  # wall seconds per tune, so one pathological build cannot hold the sweep open


def tune_id(path):
    """The identity of a cached tune: its cache-relative path, suffix dropped."""
    return Path(path).resolve().relative_to(CACHE).with_suffix("").as_posix()


def tune_name(ident):
    """The display name of an identity: its last component, which need not be unique."""
    return ident.rsplit("/", 1)[-1]


def row_head(entry):
    """The identity fields every sweep row opens with: unique key, then display name."""
    ident = tune_id(entry[0])
    return {"tune": ident, "name": tune_name(ident)}


def resolve(ids, raw):
    """The one identity ``raw`` names, or exit naming the identities it does not choose.

    A bare stem is how these tools are driven, so it stays accepted; what cannot
    happen is a stem naming two tunes and quietly running both."""
    token = raw.strip().strip("/")
    token = token[:-4] if token.endswith(".sid") else token
    hits = sorted(i for i in ids if i == token or i.endswith("/" + token))
    if not hits:
        sys.exit("no cached tune matches %r" % raw)
    if len(hits) > 1:
        sys.exit(
            "%r names %d cached tunes; ask for one of:\n  %s" % (raw, len(hits), "\n  ".join(hits))
        )
    return hits[0]


def entries(names=None):
    """``[(path, subtune, secs)]`` per cached tune, at full Songlengths length.

    Asserts the corpus is uniquely identified before sweeping it: the identity is
    unique by construction, so a violation means the construction changed."""
    from deity_informant.c64 import load_psid, psid_songs, song_lengths, song_seconds

    lengths = song_lengths((HVSC / "Songlengths.md5").read_text(encoding="latin-1"))
    paths = sorted(HVSC.rglob("*.sid"))
    ids = {tune_id(p) for p in paths}
    if len(ids) != len(paths):
        sys.exit("cache is not uniquely identified: %d files, %d ids" % (len(paths), len(ids)))
    want = None if names is None else {resolve(ids, n) for n in names if n.strip()}
    out = []
    for path in paths:
        if want is not None and tune_id(path) not in want:
            continue
        data = path.read_bytes()
        _mem, _load, _init, play = load_psid(data)
        sub = psid_songs(data)[1] - 1
        secs = song_seconds(data, lengths, sub)
        if play and secs:
            out.append((str(path), sub, secs))
    return out


def check_rows(rows):
    """Refuse to write a result set two rows could collide in."""
    dupes = [k for k, n in Counter(r["tune"] for r in rows).items() if n > 1]
    if dupes:
        sys.exit("rows are not uniquely keyed: %s" % ", ".join(sorted(dupes)))
    return rows


def _alarm(_sig, _frame):
    raise TimeoutError("build cap")


def arm():
    """Pool initialiser: arm the per-tune build cap in each worker."""
    signal.signal(signal.SIGALRM, _alarm)
