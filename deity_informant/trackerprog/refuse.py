"""T2/T3 refusals: fail-closed, each naming its cell (prototype section 8)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

REASONS = (
    "sample stream",
    "external input",
    "unclassified update",
    "score not cursor-shaped",
    "command residue",
    "guard not in IR",
    "program residue",
    "fetch not in IR",
    "producer not in IR",
    "acc not executable",
)


@dataclass(frozen=True, slots=True)
class Refusal(Exception):
    """Named; raised where a lowering cannot go on, carried as data everywhere else."""

    why: str
    cell: str
    site: str = ""
    detail: str = ""

    def __post_init__(self):
        if self.why not in REASONS:
            raise ValueError(self.why)

    def to_dict(self):
        return asdict(self)
