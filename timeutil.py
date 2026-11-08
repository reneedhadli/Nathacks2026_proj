"""Edmonton time, plus the helpers the RA page uses for 'how long has this been open'."""
from datetime import datetime, timedelta, timezone

import pandas as pd

TZ_NAME = "America/Edmonton"

# Used only if the time zone database is missing (common on Windows until you run
# "python -m pip install tzdata"). UTC-6 is Edmonton's offset right now. Change it if that ever changes.
FALLBACK_HOURS = -6

try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo(TZ_NAME)
except Exception:  # no time zone database installed
    LOCAL_TZ = timezone(timedelta(hours=FALLBACK_HOURS))

# How long an OPEN report may wait before it is flagged overdue (minutes)
OVERDUE_AFTER_MIN = {"High": 15, "Medium": 120, "Low": 1440}


def now_local():
    """Current time in Edmonton, no matter where the server is."""
    return datetime.now(LOCAL_TZ)


def now_naive():
    return now_local().replace(tzinfo=None)


def _parse_one(value):
    stamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(stamp):
        return pd.NaT
    if stamp.tzinfo is not None:  # saved with a time zone: convert to Edmonton time
        stamp = stamp.tz_convert(LOCAL_TZ).tz_localize(None)
    return stamp


def parse_times(series):
    """Turn saved timestamps into plain Edmonton datetimes, whatever format each was saved in."""
    parsed = [_parse_one(v) for v in series]
    return pd.Series(pd.to_datetime(parsed, errors="coerce"), index=series.index)


def human_age(minutes):
    """90 -> '1 h 30 min'."""
    if pd.isna(minutes) or minutes < 0:
        return "just now"
    minutes = int(minutes)
    if minutes < 1:
        return "under a minute"
    if minutes < 60:
        return f"{minutes} min"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    days, hrs = divmod(hours, 24)
    return f"{days} d {hrs} h" if hrs else f"{days} d"