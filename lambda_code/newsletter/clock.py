"""Time helpers. Timestamps are stored as UTC ISO 8601 for people and as epoch seconds for TTLs."""

from datetime import datetime, timezone


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
