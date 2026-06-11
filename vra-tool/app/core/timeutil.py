"""UTC time helpers.

``datetime.datetime.utcnow()`` is deprecated (Python 3.12+) and scheduled for
removal. ``utcnow()`` here is a drop-in replacement that returns the *same*
naive-UTC value the old call produced, so storage/comparison semantics across
the codebase (SQLAlchemy columns, article-age math) are unchanged.
"""

from __future__ import annotations

import datetime as dt


def utcnow() -> dt.datetime:
    """Naive UTC timestamp — drop-in for the deprecated ``datetime.utcnow()``."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
