"""The exemplar set: one tune per player-family cluster, and the sources they cite.

docs/idiom-catalog.md's family table is this table rendered and
``tests/test_idiom_catalog.py`` gates the two equal. A family is one cluster of
``tools/family_cluster.py``, not one player name: families fragment by build.
"""

from collections import namedtuple

Family = namedtuple("Family", "key label tunes cluster sidid")
Anchor = namedtuple("Anchor", "source min_run trust")

M = "MUSICIANS/"

# (key, label, exemplars, cluster size from out/family_cluster.json, SIDId names)
FAMILIES = tuple(
    Family(k, lab, tuple(M + t for t in tunes), n, sidid)
    for k, lab, tunes, n, sidid in (
        (
            "goattracker2",
            "GoatTracker 2 export",
            ("J/Jammer/Grid_Runner",),
            75,
            ("GoatTracker_V2.x",),
        ),
        ("dmc", "DMC / DMC V4", ("D/Daf/Alioth",), 59, ("DMC", "DMC_V4.x")),
        (
            "music-assembler",
            "Music Assembler",
            ("A/Alfatech/Galway-tune",),
            54,
            ("Music_Assembler", "VoiceTracker"),
        ),
        (
            "futurecomposer",
            "FutureComposer",
            ("B/Beast/Discmonsters_Intro",),
            42,
            ("MoN/FutureComposer",),
        ),
        (
            "soundmonitor",
            "Soundmonitor",
            ("T/Tel_Kees/Before_I_Forget",),
            30,
            ("Soundmonitor", "MusicMaster_1"),
        ),
        ("jch", "JCH NewPlayer", ("D/Deek/4_Tunes",), 20, ("JCH_NewPlayer",)),
        (
            "sidwizard",
            "SID-Wizard export",
            ("C/Chabee/Angry_Birds",),
            19,
            ("Hermit/SidWizard_V1.x",),
        ),
        ("dmc5", "DMC V5", ("C/Cleve/ABC_Music",), 15, ("DMC_V5.x",)),
        (
            "master-composer",
            "Master Composer",
            ("B/Buckley_Kevin/Down_Under",),
            15,
            ("Master_Composer",),
        ),
        (
            "follin",
            "Follin (script interpreter)",
            ("F/Follin_Tim/Ghouls_n_Ghosts", "F/Follin_Tim/Agent_X_II_The_Mad_Profs_Back"),
            10,
            ("Stephen_Ruddy",),
        ),
        ("dmc5b", "DMC V5 (second build)", ("E/Extern/From_Beyond_main",), 10, ("DMC", "DMC_V5.x")),
        (
            "goattracker1b",
            "GoatTracker 1 (second build)",
            ("C/Cerror/BubbleBobble",),
            10,
            ("GoatTracker_V1.x",),
        ),
        ("hubbard", "Hubbard (hand-coded)", ("H/Hubbard_Rob/Commando",), 9, ("Rob_Hubbard",)),
        ("romuzak", "RoMuzak V6", ("A/Albert_Christoph/Dynasty_8_tune_2",), 9, ("RoMuzak_V6.x",)),
        (
            "deflemask",
            "DefleMask v12 export",
            ("B/Big_Lumby/An_Attempt_Was_Made",),
            8,
            ("DefleMask_v12",),
        ),
        ("electrosound", "Electrosound", ("G/Gray_Matt/Atmosphere_II",), 6, ("Electrosound",)),
        (
            "cheesecutter",
            "CheeseCutter 2",
            ("C/Codex/Frantic_3_tune_5",),
            6,
            ("CheeseCutter_2.x", "Laxity_NewPlayer_V21"),
        ),
        (
            "laxity",
            "Laxity NewPlayer V21",
            ("L/Laxity/21_G4_demo_tune_2",),
            6,
            ("Laxity_NewPlayer_V21",),
        ),
        ("defmon", "defMON", ("G/Goto80/Automatas",), 6, ("DefMon",)),
        (
            "goattracker1",
            "GoatTracker 1 export",
            ("C/Cadaver/Aces_High",),
            4,
            ("GoatTracker_V1.x",),
        ),
        (
            "galway-wizball",
            "Galway 2nd gen (Wizball)",
            ("G/Galway_Martin/Wizball",),
            1,
            ("Martin_Galway",),
        ),
        (
            "galway-rambo",
            "Galway 1st gen (Rambo)",
            ("G/Galway_Martin/Rambo_First_Blood_Part_II",),
            1,
            ("Martin_Galway",),
        ),
        (
            "galway1",
            "Galway 1st gen (Comic Bakery)",
            ("G/Galway_Martin/Comic_Bakery",),
            1,
            ("Martin_Galway",),
        ),
        ("galway2", "Galway 2nd gen (Athena)", ("G/Galway_Martin/Athena",), 1, ("Martin_Galway",)),
    )
)

# Canonical sources, strongest binding first; a family absent here cites its exemplar alone.
ANCHORS = {
    k: Anchor(*rest)
    for k, *rest in (
        ("follin", "docs/follin-dispatch-study.md", 0, "in-repo, derived from the exemplar"),
        ("defmon", "defmon/defmon.asm", 16, "cite freely"),
        ("galway-rambo", "galway/rambload.asm", 32, "cite freely"),
        ("galway-wizball", "galway/wizball.asm", 32, "cite freely"),
        ("sidwizard", "sidwizard/player.asm", 32, "cite at run >= 32"),
        ("hubbard", "hubbard/rob_hubbards_music.txt", 64, "cite on runs >= 64"),
        ("goattracker2", "goattracker/player.s", 32, "provisional (build differs)"),
    )
}

ALIGNED = {k: a for k, a in ANCHORS.items() if not a.source.startswith("docs/")}

BY_KEY = {f.key: f for f in FAMILIES}
EXEMPLARS = tuple(t for f in FAMILIES for t in f.tunes)
FAMILY_OF = {t: f.key for f in FAMILIES for t in f.tunes}
COVERAGE = sum(f.cluster for f in FAMILIES)
