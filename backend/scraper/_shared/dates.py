"""Conservative normalization of posting dates supplied by job boards."""
import re
from datetime import date, datetime, time, timedelta, timezone


_RELATIVE = re.compile(r"(?:posted\s+)?(\d+)\+?\s*(minute|hour|day|week|month)s?\s+ago", re.I)


def parse_posted_at(value, *, now: datetime | None = None) -> datetime | None:
    """Return an aware UTC datetime, or None when the board did not provide one."""
    if value is None or value == "":
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    elif isinstance(value, (int, float)):
        seconds = float(value) / (1000 if abs(float(value)) > 10_000_000_000 else 1)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    else:
        text = re.sub(r"\s+", " ", str(value)).strip()
        lower = text.lower().removeprefix("posted ").strip()
        if lower in {"just now", "today"}:
            return now.astimezone(timezone.utc)
        if lower == "yesterday":
            return (now - timedelta(days=1)).astimezone(timezone.utc)
        match = _RELATIVE.fullmatch(lower)
        if match:
            count = int(match.group(1))
            unit = match.group(2).lower()
            delta = {
                "minute": timedelta(minutes=count), "hour": timedelta(hours=count),
                "day": timedelta(days=count), "week": timedelta(weeks=count),
                "month": timedelta(days=30 * count),
            }[unit]
            return (now - delta).astimezone(timezone.utc)
        candidate = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            dt = None
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y"):
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
