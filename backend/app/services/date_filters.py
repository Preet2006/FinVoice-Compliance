"""
Date filters for the call list.

THE CONTRACT: every call time is stored as a naive IST wall-clock time, so a filter value is also
an IST wall-clock time. Nothing is ever converted to UTC and then read as IST.

Accepted values:
  * a date, "2026-10-12": the whole IST day. As a lower bound it means 00:00:00; as an upper bound
    it means the end of that day (23:59:59.999999), so choosing the same day twice returns that day.
  * a naive datetime, "2026-10-12T01:30:00": read as IST, used exactly as given (inclusive).
  * a datetime with an offset ("...Z", "...+05:30"): converted to IST first, then used as above.
Lower bounds are inclusive (>=) and upper bounds inclusive (<=).
"""

from datetime import date, datetime, time
from typing import Optional

from app.rules import IST

END_OF_DAY = time(23, 59, 59, 999999)


def parse_filter_bound(value: Optional[str], *, upper: bool) -> Optional[datetime]:
    """The naive IST datetime a filter value stands for, or None for an empty value.
    Raises ValueError for anything that is not a date or an ISO datetime."""
    text = (value or "").strip()
    if not text:
        return None
    if len(text) == 10:  # a plain date
        day = date.fromisoformat(text)
        return datetime.combine(day, END_OF_DAY if upper else time.min)
    parsed = datetime.fromisoformat(text)  # accepts a trailing Z on Python 3.11+
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(IST).replace(tzinfo=None)
    return parsed
