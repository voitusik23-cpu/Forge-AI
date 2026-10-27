"""Reports: what Forge can prove about a result, separated from what it assumes.

The package holds the owner-facing *evidence* discipline. It has no I/O, no
provider and no store: :mod:`app.reports.evidence` turns the facts a caller has
already measured into the three lists the card shows, and refuses to promote an
assumption into a fact.
"""

from __future__ import annotations

from app.reports.evidence import ASSUMED_BASIS, build_evidence

__all__ = ["ASSUMED_BASIS", "build_evidence"]
