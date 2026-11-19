"""Timezone utilities for Jarvis.

Owner's home timezone is Europe/Belgrade.
Handles alias resolution, time display across zones, and natural-language
time parsing (e.g. "tomorrow 3pm MST").
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OWNER_TZ = "Europe/Belgrade"

# Common alias -> IANA map
_ALIASES: dict[str, str] = {
    # UTC / generic
    "utc": "UTC",
    "gmt": "UTC",
    # US
    "est": "America/New_York",
    "edt": "America/New_York",
    "new york": "America/New_York",
    "ny": "America/New_York",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "chicago": "America/Chicago",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "denver": "America/Denver",
    "mountain": "America/Denver",
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    # Middle East
    "dubai": "Asia/Dubai",
    "uae": "Asia/Dubai",
    "abu dhabi": "Asia/Dubai",
    "doha": "Asia/Qatar",
    "riyadh": "Asia/Riyadh",
    "cairo": "Africa/Cairo",
    "israel": "Asia/Jerusalem",
    "tel aviv": "Asia/Jerusalem",
    # Southeast Asia
    "bali": "Asia/Makassar",
    "lombok": "Asia/Makassar",
    "wita": "Asia/Makassar",
    "jakarta": "Asia/Jakarta",
    "wib": "Asia/Jakarta",
    "singapore": "Asia/Singapore",
    "sgt": "Asia/Singapore",
    "malaysia": "Asia/Kuala_Lumpur",
    "kl": "Asia/Kuala_Lumpur",
    "bangkok": "Asia/Bangkok",
    "bkk": "Asia/Bangkok",
    "thailand": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh",
    "manila": "Asia/Manila",
    # East Asia
    "tokyo": "Asia/Tokyo",
    "jst": "Asia/Tokyo",
    "beijing": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "hk": "Asia/Hong_Kong",
    "seoul": "Asia/Seoul",
    # South Asia
    "india": "Asia/Kolkata",
    "ist": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    # Europe
    "belgrade": "Europe/Belgrade",
    "serbia": "Europe/Belgrade",
    "cet": "Europe/Belgrade",
    "cest": "Europe/Belgrade",
    "london": "Europe/London",
    "uk": "Europe/London",
    "bst": "Europe/London",
    "berlin": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "paris": "Europe/Paris",
    "france": "Europe/Paris",
    "amsterdam": "Europe/Amsterdam",
    "madrid": "Europe/Madrid",
    "spain": "Europe/Madrid",
    "rome": "Europe/Rome",
    "italy": "Europe/Rome",
    "moscow": "Europe/Moscow",
    "msk": "Europe/Moscow",
    "istanbul": "Europe/Istanbul",
    "turkey": "Europe/Istanbul",
    "warsaw": "Europe/Warsaw",
    "poland": "Europe/Warsaw",
    "kyiv": "Europe/Kiev",
    "ukraine": "Europe/Kiev",
    # Australia / Pacific
    "sydney": "Australia/Sydney",
    "aest": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane",
    "perth": "Australia/Perth",
    "auckland": "Pacific/Auckland",
    "nzst": "Pacific/Auckland",
}

# Client nickname -> timezone (can be extended via config)
_CLIENT_TZ: dict[str, str] = {}


def register_client(name: str, timezone: str) -> None:
    _CLIENT_TZ[name.lower()] = resolve(timezone)


def resolve(tz: str) -> str:
    """Resolve alias or IANA name to a canonical IANA timezone string."""
    if not tz:
        return OWNER_TZ
    low = tz.strip().lower()
    # Check alias map
    if low in _ALIASES:
        return _ALIASES[low]
    # Check client map
    if low in _CLIENT_TZ:
        return _CLIENT_TZ[low]
    # Try direct IANA lookup
    try:
        ZoneInfo(tz)
        return tz
    except (ZoneInfoNotFoundError, KeyError):
        pass
    # Partial match in aliases
    for key, val in _ALIASES.items():
        if low in key or key in low:
            return val
    return OWNER_TZ  # fallback


def now_in(tz: str = OWNER_TZ) -> datetime:
    return datetime.now(ZoneInfo(resolve(tz)))


def convert(dt: datetime, to_tz: str) -> datetime:
    """Convert a datetime to a different timezone."""
    return dt.astimezone(ZoneInfo(resolve(to_tz)))


def owner_now() -> datetime:
    return now_in(OWNER_TZ)


# ── Natural-language time parsing ──────────────────────────────────────────

_TIME_RE = re.compile(
    r"(?:at\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
    re.IGNORECASE,
)
_DATE_WORDS = {
    "today": 0, "tonight": 0,
    "tomorrow": 1,
    "monday": "mon", "tuesday": "tue", "wednesday": "wed",
    "thursday": "thu", "friday": "fri", "saturday": "sat", "sunday": "sun",
    "mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
    "fri": "fri", "sat": "sat", "sun": "sun",
}
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _next_weekday(ref: date, day_abbr: str) -> date:
    target = _WEEKDAYS.index(day_abbr)
    days_ahead = (target - ref.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return ref + timedelta(days=days_ahead)


def parse_time_text(text: str, source_tz: str | None = None) -> datetime | None:
    """Parse a natural-language time expression into a timezone-aware datetime.

    Examples:
      "tomorrow 3pm MST"
      "Monday 10am Dubai"
      "next Friday at 15:30"
      "today 2pm"
    """
    src_tz = resolve(source_tz) if source_tz else OWNER_TZ
    low = text.lower()
    now = datetime.now(ZoneInfo(src_tz))
    ref_date = now.date()

    # Determine offset date
    target_date = ref_date
    for word, val in _DATE_WORDS.items():
        if word in low:
            if isinstance(val, int):
                target_date = ref_date + timedelta(days=val)
            else:
                target_date = _next_weekday(ref_date, val)
            break

    # Detect "next" modifier
    if "next " in low:
        for abbr in _WEEKDAYS:
            day_full = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][_WEEKDAYS.index(abbr)]
            if day_full in low or abbr in low.split():
                base = _next_weekday(ref_date, abbr)
                # "next X" means the one after the coming one if today is X
                if base == ref_date + timedelta(days=7) or base == ref_date:
                    target_date = base + timedelta(days=7)
                else:
                    target_date = base
                break

    # Extract time
    m = _TIME_RE.search(text)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2)) if m.group(2) else 0
    meridiem = (m.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    # Detect timezone in text (look for known aliases after time)
    text_after_time = text[m.end():].strip()
    detected_tz = src_tz
    for alias in sorted(_ALIASES.keys(), key=len, reverse=True):
        if alias in text_after_time.lower():
            detected_tz = _ALIASES[alias]
            break

    result = datetime(
        target_date.year, target_date.month, target_date.day,
        hour, minute, 0,
        tzinfo=ZoneInfo(detected_tz),
    )
    return result


# ── Multi-timezone display ─────────────────────────────────────────────────

_TZ_LABELS: dict[str, str] = {
    OWNER_TZ: "Belgrade",
    "America/New_York": "New York",
    "America/Chicago": "Chicago",
    "America/Denver": "Denver/MST",
    "America/Los_Angeles": "Los Angeles",
    "Asia/Dubai": "Dubai",
    "Asia/Makassar": "Bali",
    "Asia/Jakarta": "Jakarta",
    "Asia/Singapore": "Singapore",
    "Asia/Tokyo": "Tokyo",
    "Europe/London": "London",
    "Europe/Berlin": "Berlin",
    "UTC": "UTC",
}

DEFAULT_DISPLAY_ZONES = [
    OWNER_TZ,
    "America/Denver",   # MST
    "America/Chicago",  # CST
    "Asia/Dubai",
    "Asia/Makassar",    # Bali
]


def label(tz: str) -> str:
    return _TZ_LABELS.get(tz, tz.split("/")[-1].replace("_", " "))


def multi_tz_display(dt: datetime, zones: list[str] | None = None) -> str:
    """Format a datetime in multiple timezones for chat display."""
    zones = zones or DEFAULT_DISPLAY_ZONES
    lines: list[str] = []
    for tz in zones:
        converted = convert(dt, tz)
        is_home = tz == OWNER_TZ
        marker = " (you)" if is_home else ""
        lines.append(f"  {label(tz):<18} {converted.strftime('%a %b %d, %H:%M')}{marker}")
    return "\n".join(lines)


def tz_summary(dt: datetime) -> str:
    """Short one-line summary of a time in key zones."""
    parts = []
    for tz, lbl in [
        (OWNER_TZ, "BEG"),
        ("America/Denver", "MST"),
        ("America/Chicago", "CST"),
        ("Asia/Dubai", "DXB"),
        ("Asia/Makassar", "BALI"),
    ]:
        c = convert(dt, tz)
        parts.append(f"{lbl} {c.strftime('%H:%M')}")
    return " | ".join(parts)
