"""Application services for GameDay Event Mode.

Services coordinate repositories and cross-cutting concerns (auditing, scoring,
validation). PR01 introduces the audit service that later mutation endpoints
use to satisfy the "all mutations must be auditable" requirement.
"""

from .audit import record_audit
from . import federation

__all__ = ["record_audit", "federation"]
