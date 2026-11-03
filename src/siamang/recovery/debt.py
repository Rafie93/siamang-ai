"""Technical debt: a compromise made to get unstuck is written down where the reviewer will see it."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

TEXT_LIMIT = 300


@dataclass(frozen=True)
class DebtItem:
    stage: str
    tier: int  # 0 = the stage itself reported it; 1-3 = the recovery tier that made the compromise
    note: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "note", " ".join(str(self.note).split())[:TEXT_LIMIT])

    def to_dict(self) -> dict:
        return {"stage": self.stage, "tier": self.tier, "note": self.note}


def render_debt(items: Iterable[DebtItem]) -> str:
    """Markdown section for a pull request description. Empty when nothing was compromised."""
    rows = [f"- **{i.stage}**{f' (recovery tier {i.tier})' if i.tier else ''}: {i.note}" for i in items if i.note]
    return ("## Technical debt\n\nThese compromises were made so the run could continue. Review them.\n\n"
            + "\n".join(rows) + "\n") if rows else ""
