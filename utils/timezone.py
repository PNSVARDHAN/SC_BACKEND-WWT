from datetime import datetime, timedelta, timezone
try:
    # Python 3.9+ zoneinfo preferred
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:
    # Fallback to fixed-offset timezone
    IST = timezone(timedelta(hours=5, minutes=30))

def now_ist() -> datetime:
    """Return current datetime with IST tzinfo."""
    return datetime.now(IST)

def ensure_ist(dt: datetime | None) -> datetime | None:
    """Return a datetime guaranteed to be tz-aware in IST.

    If dt is None, returns None. If dt is naive, attach IST tzinfo. If dt
    already has tzinfo, convert it to IST.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)
