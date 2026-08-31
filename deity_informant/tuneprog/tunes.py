"""Every HVSC tune this repository names, once: certificates, exemplars, oracle.

A certificate's ``tune`` field is a basename, which is the key here; nothing else
holds an HVSC path, so adding a tune is one line. Tunes are copyright works and
are never committed: they resolve from an HVSC tree, else a fetch cache.
"""

from __future__ import annotations

import os
from pathlib import Path

HVSC = {
    "A_Mind_Is_Born.sid": "MUSICIANS/L/Lft/A_Mind_Is_Born.sid",
    "Alien_3.sid": "MUSICIANS/R/Rodger_Andrew/Alien_3.sid",
    "Automatas.sid": "MUSICIANS/G/Goto80/Automatas.sid",
    "Commando.sid": "MUSICIANS/H/Hubbard_Rob/Commando.sid",
    "Deflektor.sid": "MUSICIANS/D/Daglish_Ben/Deflektor.sid",
    "Do_It_Again.sid": "MUSICIANS/L/Linus/Do_It_Again.sid",
    "Easy_Does_It.sid": "MUSICIANS/J/JCH/Easy_Does_It.sid",
    "Emomyst.sid": "MUSICIANS/H/Hermit/Emomyst.sid",
    "End_of_the_World.sid": "MUSICIANS/H/Hermit/End_of_the_World.sid",
    "Experiment_Zeta.sid": "MUSICIANS/N/NecroPolo/Experiment_Zeta.sid",
    "Ghouls_n_Ghosts.sid": "MUSICIANS/F/Follin_Tim/Ghouls_n_Ghosts.sid",
    "Guldkornekspressen_Intro.sid": "MUSICIANS/J/JCH/Guldkornekspressen_Intro.sid",
    "I_Could_Eat_a_Knob_at_Night.sid": "MUSICIANS/P/Puterman/I_Could_Eat_a_Knob_at_Night.sid",
    "Jazzpjazz.sid": "MUSICIANS/G/Goto80/Jazzpjazz.sid",
    "Je_suis_Linus_le_salaud.sid": "MUSICIANS/L/Linus/Je_suis_Linus_le_salaud.sid",
    "Jodler.sid": "MUSICIANS/B/Becher_Patrick/Jodler.sid",
    "Monty_on_the_Run.sid": "MUSICIANS/H/Hubbard_Rob/Monty_on_the_Run.sid",
    "Playful_Professor-Math_Tutor.sid": (
        "MUSICIANS/B/Baumrucker_Steven/Playful_Professor-Math_Tutor.sid"
    ),
    "Quintessence.sid": "MUSICIANS/L/Lft/Quintessence.sid",
}


def path(name):
    """The HVSC-relative path of a tune named by its basename."""
    return HVSC.get(name, name)


def cache(root=None):
    """Where fetched tunes are kept: ``$DEITY_ORACLE_CACHE/hvsc``."""
    return Path(root or os.environ.get("DEITY_ORACLE_CACHE", ".oracle-cache")) / "hvsc"


def resolve(name, hvsc=None, cache_dir=None):
    """The tune's file under an HVSC tree (``hvsc``, else ``$HVSC``), else the cache.

    ``None`` when it is neither present nor fetchable.
    """
    # pylint: disable=import-outside-toplevel,import-error
    from pysidtracker.testing import resolve_tune

    rel = path(name)
    root = hvsc or os.environ.get("HVSC")
    if root and (Path(root) / rel).is_file():
        return Path(root) / rel
    hit = resolve_tune(rel, cache_dir=cache(cache_dir))
    return None if hit is None else Path(hit)
