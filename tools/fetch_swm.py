"""Fetch the SID-Wizard example modules into .oracle-cache/swm.

The modules ship inside the SID-Wizard 1.94 source tarball on CSDB, not as HVSC
relpaths. The tarball is SHA-256 verified and every ``*.swm`` member is extracted
flat, so the cache the oracle tools read is reproducible from one public artifact.
"""

import hashlib
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".oracle-cache" / "swm"
URL = "https://csdb.dk/getinternalfile.php/276275/SID-Wizard-1.94-with-sources.tar.gz"
SHA256 = "544e36aff3fe14b7e4cf81a04c680a6883191a222754b2f0489e15349a89b559"


def fetch(cache=CACHE, url=URL, sha256=SHA256):
    """Download the verified tarball and extract every ``.swm`` member into ``cache``."""
    cache.mkdir(parents=True, exist_ok=True)
    tarball = cache / Path(url).name
    if not tarball.exists():
        with urllib.request.urlopen(url) as resp:
            tarball.write_bytes(resp.read())
    got = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if got != sha256:
        tarball.unlink()
        raise ValueError("tarball sha256 %s != expected %s" % (got, sha256))
    names = []
    with tarfile.open(tarball) as tf:
        for member in tf.getmembers():
            if not member.isfile() or not member.name.endswith(".swm"):
                continue
            data = tf.extractfile(member).read()
            dst = cache / Path(member.name).name
            if not dst.exists() or dst.read_bytes() != data:
                dst.write_bytes(data)
            names.append(dst.name)
    return sorted(names)


def main(_argv):
    """Fetch and report what the cache holds."""
    names = fetch()
    print("cached %d modules in %s" % (len(names), CACHE))
    for name in names:
        print(" ", name)


if __name__ == "__main__":
    main(sys.argv)
