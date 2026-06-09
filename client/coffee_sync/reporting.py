"""Run accounting: per-row outcomes, a summary, and a dead-letter CSV."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .models import Payment


@dataclass
class RowOutcome:
    payment: Payment
    status: str  # "created" | "replayed" | "failed"
    detail: str = ""
    payment_id: str | None = None
    attempts: int = 1


@dataclass
class RunReport:
    outcomes: list[RowOutcome] = field(default_factory=list)

    def record(self, outcome: RowOutcome) -> None:
        self.outcomes.append(outcome)

    @property
    def created(self) -> list[RowOutcome]:
        return [o for o in self.outcomes if o.status == "created"]

    @property
    def replayed(self) -> list[RowOutcome]:
        return [o for o in self.outcomes if o.status == "replayed"]

    @property
    def failed(self) -> list[RowOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    @property
    def succeeded(self) -> int:
        return len(self.created) + len(self.replayed)

    def summary_lines(self) -> list[str]:
        return [
            "── Sync summary ─────────────────────────────",
            f"  total rows : {len(self.outcomes)}",
            f"  created    : {len(self.created)}",
            f"  replayed   : {len(self.replayed)}  (idempotent — already on the server)",
            f"  failed     : {len(self.failed)}",
            "─────────────────────────────────────────────",
        ]

    def write_dead_letter(self, path: str | Path) -> int:
        """Write failed rows to a CSV for inspection / reprocessing. Returns count written."""
        failed = self.failed
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["row_number", "store_id", "coffee_type", "price", "currency", "loyalty_card_id", "error"]
            )
            for o in failed:
                p = o.payment
                writer.writerow(
                    [p.row_number, p.store_id, p.coffee_type, p.price, p.currency, p.loyalty_card_id, o.detail]
                )
        return len(failed)
