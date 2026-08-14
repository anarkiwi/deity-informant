"""One identity per cached tune, and the corpus enumeration the sweeps share.

A stem does not identify a tune: eight stems in the cache name two tunes each, so a
row keyed by stem merges two and ``--tunes Commando`` runs two while looking like one.
The identity is the cache-relative path without its suffix, unique by construction.
"""

import gzip
import hashlib
import json
import os
import signal
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HVSC = ROOT / ".oracle-cache" / "hvsc"
CACHE = HVSC.resolve()
CAP_S = 1800  # wall seconds per tune, so one pathological build cannot hold the sweep open

PKG = ROOT / "deity_informant"
ARTIFACTS = ROOT / ".sweep-cache"  # frameprog artifacts, content-keyed; never committed
CACHE_ENV = "DI_SWEEP_CACHE"  # "0"/"off": bypass; "refresh": recompute and rewrite
_FINGERPRINT = None


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


# ---- the decompile artifact cache (3a) -----------------------------------------
def fingerprint():
    """SHA of every package source file: no key can name a build it did not come from.

    Content, not a maintained constant -- a stale artifact after an edit to any
    module the decompile reaches is the failure mode this exists to make impossible."""
    global _FINGERPRINT  # pylint: disable=global-statement
    if _FINGERPRINT is None:
        h = hashlib.sha256()
        for p in sorted(PKG.rglob("*.py")) + sorted(PKG.rglob("*.lark")):
            h.update(p.name.encode())
            h.update(p.read_bytes())
        _FINGERPRINT = h.hexdigest()
    return _FINGERPRINT


def _key(mem, init, play, frames, subtune, kw):
    """The cache key: the image as the caller mutated it, the build, and the code."""
    h = hashlib.sha256(bytes(mem))
    h.update(repr((init, play, frames, subtune, sorted(kw.items()))).encode())
    h.update(fingerprint().encode())
    return h.hexdigest()


def _digest(wlog):
    """SHA of a SID write log: what a cached run compares a replay against."""
    h = hashlib.sha256()
    for c, reg, val in wlog:
        h.update(b"%d %d %d\n" % (c, reg, val))
    return h.hexdigest()


class Replayed:
    """The evidence a cached artifact carries: the log is a digest, not the log."""

    __slots__ = ("wlog_sha", "wlog_len", "cached")

    def __init__(self, sha, n):
        self.wlog_sha = sha
        self.wlog_len = n
        self.cached = True


def wlog_matches(ev, log):
    """Whether ``log`` is the write log this evidence recorded (cached or not)."""
    return len(log) == ev.wlog_len and _digest(log) == ev.wlog_sha


def _serve(mem, init, play, frames, subtune, kw):
    """``(model, evidence, where to store)``: a hit rebuilds, a miss traces; None: cached.

    The artifact is the frameprog text, which is total, so a hit skips the trace. An
    unknown key recomputes -- never a partial match -- and ``DI_SWEEP_CACHE=0``
    bypasses both directions, ``=refresh`` recomputes and rewrites."""
    from deity_informant import codec
    from deity_informant import frameprog
    from deity_informant import structured as S

    mode = os.environ.get(CACHE_ENV, "").lower()
    path = None
    if mode not in ("0", "off", "no"):
        path = ARTIFACTS / ("%s.fp.gz" % _key(mem, init, play, frames, subtune, kw))
        got = _read(path) if mode != "refresh" else None
        if got is not None:
            rec, text = got
            model = frameprog.block_model(frameprog.loads(text), kw.get("sound", False))
            codec.verify(model)
            return model, Replayed(rec["wlog_sha"], rec["wlog_len"]), None
    model, ev = S.decompile(mem, init, play, frames, subtune, **kw)
    ev.wlog_sha, ev.wlog_len, ev.cached = _digest(ev.wlog), len(ev.wlog), False
    return model, ev, path


def decompile(mem, init, play, frames, subtune=0, **kw):
    """``(model, evidence)`` as ``structured.decompile``, off the artifact cache."""
    from deity_informant import frameprog

    model, ev, path = _serve(mem, init, play, frames, subtune, kw)
    if path is not None:
        _store(path, ev, frameprog.dumps(frameprog.program(model)))
    return model, ev


def build(mem, init, play, frames, subtune=0, extents=None, **kw):
    """``(model, frame program, evidence)``: the cached decompile plus rung (g).

    The program handed back is the artifact read back, not ``frameprog.program``'s
    pre-render object, so no gate can judge what the emission does not ship. The
    stored artifact stays the extent-free text; only ``--extents`` emits twice."""
    from deity_informant import frameprog

    model, ev, path = _serve(mem, init, play, frames, subtune, kw)
    text = frameprog.dumps(frameprog.program(model, extents))
    if path is not None:
        _store(path, ev, text if extents is None else frameprog.dumps(frameprog.program(model)))
    return model, frameprog.loads(text), ev


def artifact_text(entry):
    """The frameprog text cached for one ``entries()`` row, or None where none is.

    Every sweep builds at the full Songlengths length from the same image, so that
    image and this key are what name a tune's artifact: a text census reads it here
    rather than re-deriving the key beside the one the writer used."""
    from deity_informant.c64 import load_psid

    sid, subtune, secs = entry
    mem, _load, init, play = load_psid(Path(sid).read_bytes())
    mem[0xD418] = 0x0F  # the filter volume the corpus is swept at
    got = _read(ARTIFACTS / ("%s.fp.gz" % _key(mem, init, play, int(secs * 50), subtune, {})))
    return None if got is None else got[1]


def _read(path):
    """``(header, text)`` of a stored artifact, or None: a damaged file is a miss.

    Storage faults recompute; a well-formed artifact the reader rejects does not,
    because that is a grammar regression and must be loud."""
    try:
        head, text = gzip.decompress(path.read_bytes()).decode("utf-8").split("\n", 1)
        return json.loads(head), text
    except (OSError, EOFError, ValueError):
        return None


def _store(path, ev, text):
    """Write one artifact atomically: 32 workers share this directory."""
    head = json.dumps({"wlog_sha": ev.wlog_sha, "wlog_len": ev.wlog_len})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".%d.tmp" % os.getpid())
    tmp.write_bytes(gzip.compress(("%s\n%s" % (head, text)).encode("utf-8")))
    os.replace(tmp, path)


def _alarm(_sig, _frame):
    raise TimeoutError("build cap")


def arm():
    """Pool initialiser: arm the per-tune build cap in each worker."""
    signal.signal(signal.SIGALRM, _alarm)
