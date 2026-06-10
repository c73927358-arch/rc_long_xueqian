import time
from datetime import datetime, timezone


def now_ts():
    return time.time()

def format_ts(value):
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def elapsed_ms(started_at):
    return max(int(round((time.monotonic() - started_at) * 1000)), 0)
