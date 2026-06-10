import os
import re
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PUBLIC_DIR = ROOT_DIR / "public"
DB_PATH = Path(os.environ.get("NOTIFICATION_DB_PATH", ROOT_DIR / "notifications.db"))

ALLOWED_METHODS = {"POST", "PUT", "PATCH"}
READY_STATUSES = {"queued", "waiting_retry"}
SENSITIVE_KEY_RE = re.compile(r"(authorization|token|secret|password|key|credential|signature)", re.IGNORECASE)
URL_QUERY_RE = re.compile(r"([?&])([^=\s&#]+)=([^&\s#]*)")
REDACTED_VALUE = "[REDACTED]"

DEFAULT_DELIVERY_TIMEOUT_SECONDS = 8.0
MIN_DELIVERY_TIMEOUT_SECONDS = 0.1
MAX_DELIVERY_TIMEOUT_SECONDS = 60.0
DEFAULT_DELIVERING_LEASE_SECONDS = 60.0
MIN_DELIVERING_LEASE_SECONDS = 0.1
MAX_DELIVERING_LEASE_SECONDS = 3600.0
DEFAULT_WORKER_CONCURRENCY = 1
MIN_WORKER_CONCURRENCY = 1
MAX_WORKER_CONCURRENCY = 8
WORKER_POLL_INTERVAL_SECONDS = 0.8

QUEUE_STATUSES = ("queued", "delivering", "waiting_retry", "succeeded", "failed", "dead_letter")
DEAD_LETTER_ELIGIBLE_STATUSES = {"failed", "waiting_retry", "dead_letter"}
BATCH_RETRY_STATUSES = {"failed", "waiting_retry", "dead_letter"}
PROTECTED_NOTIFICATION_WRITE_PATHS = {"/api/notifications", "/api/notifications/retry"}

SERVICE_VERSION = "2026-06-10.round-m"
SCHEMA_VERSION = "2026-06-10"

LIST_SORT_COLUMNS = {
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "nextAttemptAt": "next_attempt_at",
    "status": "status",
}
CSV_EXPORT_FIELDS = (
    "id",
    "requestId",
    "eventType",
    "sourceSystem",
    "targetUrl",
    "status",
    "failureType",
    "attemptCount",
    "deliveryRun",
    "lastStatusCode",
    "createdAt",
    "updatedAt",
    "deliveredAt",
    "lastError",
)


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def delivery_timeout_seconds():
    raw = os.environ.get("NOTIFICATION_DELIVERY_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_DELIVERY_TIMEOUT_SECONDS
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_DELIVERY_TIMEOUT_SECONDS
    return min(max(parsed, MIN_DELIVERY_TIMEOUT_SECONDS), MAX_DELIVERY_TIMEOUT_SECONDS)


def delivering_lease_seconds():
    raw = os.environ.get("NOTIFICATION_DELIVERING_LEASE_SECONDS")
    if raw is None:
        return DEFAULT_DELIVERING_LEASE_SECONDS
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_DELIVERING_LEASE_SECONDS
    return min(max(parsed, MIN_DELIVERING_LEASE_SECONDS), MAX_DELIVERING_LEASE_SECONDS)


def notification_worker_concurrency():
    raw = os.environ.get("NOTIFICATION_WORKER_CONCURRENCY")
    if raw is None:
        return DEFAULT_WORKER_CONCURRENCY
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WORKER_CONCURRENCY
    return min(max(parsed, MIN_WORKER_CONCURRENCY), MAX_WORKER_CONCURRENCY)
