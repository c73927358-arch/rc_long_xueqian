#!/usr/bin/env python3
import argparse
import csv
import hmac
import io
import ipaddress
import json
import math
import mimetypes
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT_DIR / "public"
DB_PATH = Path(os.environ.get("NOTIFICATION_DB_PATH", ROOT_DIR / "notifications.db"))
DB_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
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
WORKER_STATE_LOCK = threading.Lock()
WORKER_STATE = {
    "started_at": None,
    "last_tick_at": None,
    "last_claimed_job_id": None,
    "last_claimed_at": None,
    "last_lease_recovery_at": None,
    "last_lease_recovery_count": 0,
    "last_error": None,
}
WORKER_CONCURRENCY = None
WORKER_THREADS = []
SERVICE_ORIGIN = None
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


def normalize_manual_text(value, max_length):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length]


class InvalidTargetError(ValueError):
    pass


def now_ts():
    return time.time()


def format_ts(value):
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def update_worker_state(**values):
    with WORKER_STATE_LOCK:
        WORKER_STATE.update(values)


def snapshot_worker_state():
    with WORKER_STATE_LOCK:
        return dict(WORKER_STATE)


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configured_notification_api_keys():
    raw = os.environ.get("NOTIFICATION_API_KEYS", "")
    return tuple(key.strip() for key in raw.split(",") if key.strip())


def is_protected_notification_write_path(path):
    if path in PROTECTED_NOTIFICATION_WRITE_PATHS:
        return True
    parts = path.strip("/").split("/")
    return (
        len(parts) == 4
        and parts[0] == "api"
        and parts[1] == "notifications"
        and parts[3] in {"retry", "dead-letter"}
    )


def extract_bearer_api_key(headers):
    authorization = headers.get("Authorization")
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def api_key_matches(candidate, expected):
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def has_valid_notification_api_key(headers):
    configured_keys = configured_notification_api_keys()
    if not configured_keys:
        return True

    candidates = []
    header_key = headers.get("X-Notification-Api-Key")
    if header_key is not None:
        candidates.append(header_key.strip())
    bearer_key = extract_bearer_api_key(headers)
    if bearer_key is not None:
        candidates.append(bearer_key)

    return any(
        candidate and api_key_matches(candidate, configured_key)
        for candidate in candidates
        for configured_key in configured_keys
    )


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


def configured_worker_concurrency():
    return WORKER_CONCURRENCY if WORKER_CONCURRENCY is not None else notification_worker_concurrency()


def is_sensitive_key(key):
    return bool(SENSITIVE_KEY_RE.search(str(key or "")))


def redact_query_secrets(value):
    if not value:
        return value

    def replace(match):
        separator, key, raw_value = match.groups()
        if is_sensitive_key(key):
            return f"{separator}{key}={REDACTED_VALUE}"
        return f"{separator}{key}={raw_value}"

    return URL_QUERY_RE.sub(replace, str(value))


def redact_sensitive_json(value):
    if isinstance(value, dict):
        return {
            key: REDACTED_VALUE if is_sensitive_key(key) else redact_sensitive_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_json(item) for item in value]
    return value


def redact_headers(headers):
    return {
        key: REDACTED_VALUE if is_sensitive_key(key) else value
        for key, value in headers.items()
    }


def redact_body_for_api(body):
    try:
        parsed = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return body
    return json.dumps(redact_sensitive_json(parsed), ensure_ascii=False, separators=(",", ":"))


def body_preview_for_api(body):
    redacted = redact_body_for_api(body)
    return redacted[:160] + ("..." if len(redacted) > 160 else "")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_allowed_target_origins():
    raw = os.environ.get("NOTIFICATION_ALLOWED_TARGETS", "").strip()
    if not raw:
        return None
    origins = set()
    for item in raw.split(","):
        origin = item.strip()
        if not origin:
            continue
        parsed = urlparse(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("NOTIFICATION_ALLOWED_TARGETS must contain comma-separated exact http(s) origins")
        origins.add(origin_from_parsed(parsed))
    return origins


def origin_from_parsed(parsed):
    host = parsed.hostname.lower() if parsed.hostname else ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    port_part = f":{port}" if port and port != default_port else ""
    return f"{parsed.scheme.lower()}://{host}{port_part}"


def origin_from_host_header(host_header):
    if not host_header:
        return None
    parsed = urlparse(f"http://{host_header.strip()}")
    if not parsed.hostname:
        return None
    try:
        return origin_from_parsed(parsed)
    except ValueError:
        return None


def current_request_origin(handler):
    host_origin = origin_from_host_header(handler.headers.get("Host"))
    if host_origin:
        return host_origin

    server_host, server_port = handler.server.server_address[:2]
    host = server_host.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    parsed = urlparse(f"http://{host}:{server_port}")
    return origin_from_parsed(parsed)


def is_same_origin_mock_vendor(parsed, current_origin):
    if not env_flag("ALLOW_LOCAL_MOCK_VENDOR", default=True):
        return False
    try:
        return origin_from_parsed(parsed) == current_origin and parsed.path.startswith("/mock/vendor/")
    except ValueError:
        return False


def delivery_validation_origin(target_url):
    parsed = urlparse(target_url)
    try:
        target_origin = origin_from_parsed(parsed)
    except ValueError:
        return None
    if is_same_origin_mock_vendor(parsed, target_origin):
        return target_origin
    return None


def resolve_target_addresses(hostname, port):
    try:
        literal = ipaddress.ip_address(hostname.strip("[]"))
        return [literal]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(hostname, port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"targetUrl hostname could not be resolved: {exc}") from exc

    addresses = []
    for info in infos:
        sockaddr = info[4]
        try:
            addresses.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not addresses:
        raise ValueError("targetUrl hostname resolved to no usable addresses")
    return addresses


def is_blocked_target_address(address):
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    )


def assert_public_resolved_target(parsed):
    for address in resolve_target_addresses(parsed.hostname, parsed.port):
        if is_blocked_target_address(address):
            raise ValueError(f"targetUrl resolves to blocked SSRF address {address}")


def init_db():
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                request_id TEXT UNIQUE,
                event_type TEXT,
                source_system TEXT,
                target_url TEXT NOT NULL,
                method TEXT NOT NULL,
                headers_json TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                delivery_run INTEGER NOT NULL DEFAULT 1,
                max_attempts INTEGER NOT NULL,
                timeout_seconds REAL,
                next_attempt_at REAL,
                last_error TEXT,
                last_manual_action TEXT,
                last_manual_action_at REAL,
                last_manual_action_by TEXT,
                resolution_note TEXT,
                last_status_code INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                delivered_at REAL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                notification_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                attempt_sequence INTEGER,
                delivery_run INTEGER,
                status TEXT NOT NULL,
                status_code INTEGER,
                error TEXT,
                duration_ms INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        ensure_column(conn, "notifications", "failure_type", "TEXT")
        ensure_column(conn, "notifications", "delivery_run", "INTEGER DEFAULT 1")
        ensure_column(conn, "notifications", "timeout_seconds", "REAL")
        ensure_column(conn, "notifications", "last_manual_action", "TEXT")
        ensure_column(conn, "notifications", "last_manual_action_at", "REAL")
        ensure_column(conn, "notifications", "last_manual_action_by", "TEXT")
        ensure_column(conn, "notifications", "resolution_note", "TEXT")
        ensure_column(conn, "notification_attempts", "error_type", "TEXT")
        ensure_column(conn, "notification_attempts", "attempt_sequence", "INTEGER")
        ensure_column(conn, "notification_attempts", "delivery_run", "INTEGER")
        conn.execute("UPDATE notifications SET delivery_run = 1 WHERE delivery_run IS NULL")
        conn.execute("UPDATE notification_attempts SET delivery_run = 1 WHERE delivery_run IS NULL")
        backfill_attempt_sequences(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notifications_ready ON notifications(status, next_attempt_at, created_at)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC)")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notification_attempts_notification
                ON notification_attempts(notification_id, created_at ASC, id ASC)
            """
        )
        conn.execute(
            """
            UPDATE notifications
               SET status = 'queued',
                   next_attempt_at = ?,
                   updated_at = ?,
                   last_error = 'service restarted while delivery was in progress'
             WHERE status = 'delivering'
            """,
            (timestamp, timestamp),
        )


def backfill_attempt_sequences(conn):
    rows = conn.execute(
        """
        SELECT id, notification_id
          FROM notification_attempts
         ORDER BY notification_id ASC, created_at ASC, id ASC
        """
    ).fetchall()
    sequence_by_notification = {}
    for row in rows:
        notification_id = row["notification_id"]
        sequence_by_notification[notification_id] = sequence_by_notification.get(notification_id, 0) + 1
        conn.execute(
            """
            UPDATE notification_attempts
               SET attempt_sequence = COALESCE(attempt_sequence, ?)
             WHERE id = ?
            """,
            (sequence_by_notification[notification_id], row["id"]),
        )


def ensure_column(conn, table_name, column_name, column_definition):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def row_to_dict(row, include_body=True):
    if row is None:
        return None
    body = row["body"]
    item = {
        "id": row["id"],
        "requestId": row["request_id"],
        "eventType": row["event_type"],
        "sourceSystem": row["source_system"],
        "targetUrl": row["target_url"],
        "method": row["method"],
        "headers": redact_headers(json.loads(row["headers_json"] or "{}")),
        "status": row["status"],
        "attemptCount": row["attempt_count"],
        "deliveryRun": row["delivery_run"],
        "maxAttempts": row["max_attempts"],
        "timeoutSeconds": row["timeout_seconds"],
        "nextAttemptAt": format_ts(row["next_attempt_at"]),
        "lastError": redact_query_secrets(row["last_error"]),
        "failureType": row["failure_type"],
        "lastManualAction": row["last_manual_action"],
        "lastManualActionAt": format_ts(row["last_manual_action_at"]),
        "lastManualActionBy": row["last_manual_action_by"],
        "resolutionNote": redact_query_secrets(row["resolution_note"]),
        "lastStatusCode": row["last_status_code"],
        "createdAt": format_ts(row["created_at"]),
        "updatedAt": format_ts(row["updated_at"]),
        "deliveredAt": format_ts(row["delivered_at"]),
    }
    if include_body:
        item["body"] = redact_body_for_api(body)
    else:
        item["bodyPreview"] = body_preview_for_api(body)
    return item


def attempt_row_to_dict(row):
    return {
        "id": row["id"],
        "notificationId": row["notification_id"],
        "attemptNumber": row["attempt_number"],
        "attemptSequence": row["attempt_sequence"],
        "deliveryRun": row["delivery_run"],
        "status": row["status"],
        "statusCode": row["status_code"],
        "error": redact_query_secrets(row["error"]),
        "errorType": row["error_type"],
        "durationMs": row["duration_ms"],
        "createdAt": format_ts(row["created_at"]),
    }


def validate_target_url(value, current_origin=None):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("targetUrl must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("targetUrl must not include username or password")
    try:
        origin = origin_from_parsed(parsed)
    except ValueError as exc:
        raise ValueError("targetUrl has an invalid host or port") from exc

    if current_origin and is_same_origin_mock_vendor(parsed, current_origin):
        return value

    allowed_origins = parse_allowed_target_origins()
    if allowed_origins is not None and origin not in allowed_origins:
        raise ValueError("targetUrl origin is not allowed by NOTIFICATION_ALLOWED_TARGETS")

    assert_public_resolved_target(parsed)
    return value


class SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, current_origin=None):
        super().__init__()
        self.current_origin = current_origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url = urljoin(req.full_url, newurl)
        try:
            validate_target_url(redirect_url, current_origin=self.current_origin)
        except ValueError as exc:
            raise InvalidTargetError(f"redirect target blocked: {exc}") from exc
        return super().redirect_request(req, fp, code, msg, headers, redirect_url)


def build_safe_delivery_opener(current_origin=None):
    return build_opener(SafeRedirectHandler(current_origin=current_origin))


def normalize_headers(headers):
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise ValueError("headers must be an object")
    normalized = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("header names must be non-empty strings")
        normalized[key.strip()] = str(value)
    return normalized


def normalize_body(body, headers):
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    if isinstance(body, (dict, list, int, float, bool)):
        has_content_type = any(key.lower() == "content-type" for key in headers)
        if not has_content_type:
            headers["Content-Type"] = "application/json"
        return json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    raise ValueError("body must be an object, array, string, number, boolean, or null")


def clamp_max_attempts(value):
    if value is None:
        return 5
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("maxAttempts must be an integer") from exc
    return min(max(parsed, 1), 10)


def clamp_timeout_seconds(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("timeoutSeconds must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeoutSeconds must be a number") from exc
    if not math.isfinite(parsed):
        raise ValueError("timeoutSeconds must be a finite number")
    return min(max(parsed, MIN_DELIVERY_TIMEOUT_SECONDS), MAX_DELIVERY_TIMEOUT_SECONDS)


def normalize_request_id(value):
    if value is None:
        return None
    return str(value).strip() or None


def normalize_manual_action_fields(handled_by=None, note=None):
    return normalize_manual_text(handled_by, 120), normalize_manual_text(note, 1000)


def parse_manual_action_payload(payload):
    handled_by = payload.get("handledBy")
    if handled_by is None:
        handled_by = payload.get("actionBy")
    note = payload.get("note")
    if note is None:
        note = payload.get("resolutionNote")
    return normalize_manual_action_fields(handled_by, note)


def create_notification(payload, current_origin=None):
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    request_id = normalize_request_id(payload.get("requestId"))
    if request_id is not None:
        with DB_LOCK, get_db() as conn:
            row = conn.execute("SELECT * FROM notifications WHERE request_id = ?", (request_id,)).fetchone()
            if row is not None:
                return row_to_dict(row), True

    target_url = validate_target_url(str(payload.get("targetUrl", "")).strip(), current_origin=current_origin)
    method = str(payload.get("method") or "POST").upper()
    if method not in ALLOWED_METHODS:
        raise ValueError("method must be one of POST, PUT, PATCH")
    headers = normalize_headers(payload.get("headers"))
    body = normalize_body(payload.get("body"), headers)
    max_attempts = clamp_max_attempts(payload.get("maxAttempts"))
    timeout_seconds = clamp_timeout_seconds(payload.get("timeoutSeconds"))

    notification_id = str(uuid.uuid4())
    timestamp = now_ts()
    values = (
        notification_id,
        request_id,
        str(payload.get("eventType") or "").strip() or None,
        str(payload.get("sourceSystem") or "").strip() or None,
        target_url,
        method,
        json.dumps(headers, ensure_ascii=False),
        body,
        "queued",
        0,
        1,
        max_attempts,
        timeout_seconds,
        timestamp,
        None,
        None,
        timestamp,
        timestamp,
        None,
    )

    with DB_LOCK, get_db() as conn:
        try:
            conn.execute(
                """
                INSERT INTO notifications (
                    id, request_id, event_type, source_system, target_url, method,
                    headers_json, body, status, attempt_count, delivery_run, max_attempts,
                    timeout_seconds, next_attempt_at, last_error, last_status_code,
                    created_at, updated_at, delivered_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        except sqlite3.IntegrityError:
            if request_id is None:
                raise
            row = conn.execute("SELECT * FROM notifications WHERE request_id = ?", (request_id,)).fetchone()
            return row_to_dict(row), True
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
        return row_to_dict(row), False


def parse_clamped_int(value, default, min_value, max_value, name):
    if value in {None, ""}:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    return min(max(parsed, min_value), max_value)


def parse_time_filter(value, name, is_upper_bound=False):
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if not text:
        return None

    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    timestamp = parsed.timestamp()
    if is_upper_bound and parsed.microsecond == 0:
        timestamp += 0.999999
    return timestamp


def normalize_notification_sort(sort=None, order=None):
    sort_key = sort or "createdAt"
    if sort_key not in LIST_SORT_COLUMNS:
        allowed = ", ".join(LIST_SORT_COLUMNS)
        raise ValueError(f"sort must be one of: {allowed}")

    order_key = (order or "desc").lower()
    if order_key not in {"asc", "desc"}:
        raise ValueError("order must be asc or desc")

    return LIST_SORT_COLUMNS[sort_key], order_key.upper(), sort_key, order_key


def notification_filter_sql(
    status=None,
    event_type=None,
    source_system=None,
    target_url=None,
    created_from=None,
    created_to=None,
    updated_from=None,
    updated_to=None,
):
    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if event_type:
        clauses.append("event_type LIKE ?")
        params.append(f"%{event_type}%")
    if source_system:
        clauses.append("source_system LIKE ?")
        params.append(f"%{source_system}%")
    if target_url:
        clauses.append("target_url LIKE ?")
        params.append(f"%{target_url}%")
    if created_from is not None:
        clauses.append("created_at >= ?")
        params.append(created_from)
    if created_to is not None:
        clauses.append("created_at <= ?")
        params.append(created_to)
    if updated_from is not None:
        clauses.append("updated_at >= ?")
        params.append(updated_from)
    if updated_to is not None:
        clauses.append("updated_at <= ?")
        params.append(updated_to)
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where_sql, params


def query_notification_rows(
    status=None,
    limit=50,
    offset=0,
    event_type=None,
    source_system=None,
    target_url=None,
    sort=None,
    order=None,
    created_from=None,
    created_to=None,
    updated_from=None,
    updated_to=None,
    max_limit=200,
):
    limit = parse_clamped_int(limit, 50, 1, max_limit, "limit")
    offset = parse_clamped_int(offset, 0, 0, 1_000_000, "offset")
    sort_column, order_sql, sort_key, order_key = normalize_notification_sort(sort, order)
    where_sql, params = notification_filter_sql(
        status=status,
        event_type=event_type,
        source_system=source_system,
        target_url=target_url,
        created_from=parse_time_filter(created_from, "createdFrom"),
        created_to=parse_time_filter(created_to, "createdTo", is_upper_bound=True),
        updated_from=parse_time_filter(updated_from, "updatedFrom"),
        updated_to=parse_time_filter(updated_to, "updatedTo", is_upper_bound=True),
    )

    secondary_sort = ", id ASC"
    if sort_column != "created_at":
        secondary_sort = ", created_at DESC, id ASC"
    query_params = [*params, limit + 1, offset]
    with DB_LOCK, get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT *
              FROM notifications
              {where_sql}
             ORDER BY {sort_column} {order_sql}{secondary_sort}
             LIMIT ? OFFSET ?
            """,
            query_params,
        ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "rows": rows,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "count": len(rows),
            "hasMore": has_more,
            "sort": sort_key,
            "order": order_key,
        },
    }


def list_notifications(
    status=None,
    limit=50,
    offset=0,
    event_type=None,
    source_system=None,
    target_url=None,
    sort=None,
    order=None,
    created_from=None,
    created_to=None,
    updated_from=None,
    updated_to=None,
):
    result = query_notification_rows(
        status=status,
        limit=limit,
        offset=offset,
        event_type=event_type,
        source_system=source_system,
        target_url=target_url,
        sort=sort,
        order=order,
        created_from=created_from,
        created_to=created_to,
        updated_from=updated_from,
        updated_to=updated_to,
        max_limit=200,
    )
    return {
        "items": [row_to_dict(row, include_body=False) for row in result["rows"]],
        "pagination": result["pagination"],
    }


def export_notifications_csv(
    status=None,
    limit=1000,
    offset=0,
    event_type=None,
    source_system=None,
    target_url=None,
    sort=None,
    order=None,
    created_from=None,
    created_to=None,
    updated_from=None,
    updated_to=None,
):
    result = query_notification_rows(
        status=status,
        limit=limit,
        offset=offset,
        event_type=event_type,
        source_system=source_system,
        target_url=target_url,
        sort=sort,
        order=order,
        created_from=created_from,
        created_to=created_to,
        updated_from=updated_from,
        updated_to=updated_to,
        max_limit=5000,
    )
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in result["rows"]:
        item = row_to_dict(row, include_body=False)
        writer.writerow({field: item.get(field) for field in CSV_EXPORT_FIELDS})
    return output.getvalue()


def get_notification(notification_id):
    with DB_LOCK, get_db() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
    return row_to_dict(row)


def get_notification_attempts(notification_id):
    with DB_LOCK, get_db() as conn:
        notification = conn.execute("SELECT id FROM notifications WHERE id = ?", (notification_id,)).fetchone()
        if notification is None:
            return None
        rows = conn.execute(
            """
            SELECT *
              FROM notification_attempts
             WHERE notification_id = ?
             ORDER BY created_at ASC, id ASC
            """,
            (notification_id,),
        ).fetchall()
    return [attempt_row_to_dict(row) for row in rows]


def expired_delivering_cutoff(timestamp):
    return timestamp - delivering_lease_seconds()


def count_expired_delivering(conn, timestamp):
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
          FROM notifications
         WHERE status = 'delivering'
           AND updated_at <= ?
        """,
        (expired_delivering_cutoff(timestamp),),
    ).fetchone()
    return int(row["count"])


def get_queue_summary(conn, timestamp):
    counts = {status: 0 for status in QUEUE_STATUSES}
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS count
          FROM notifications
         GROUP BY status
        """
    ).fetchall()
    for row in rows:
        if row["status"] in counts:
            counts[row["status"]] = int(row["count"])
    ready_row = conn.execute(
        """
        SELECT COUNT(*) AS count
          FROM notifications
         WHERE status IN ('queued', 'waiting_retry')
           AND next_attempt_at <= ?
           AND attempt_count < max_attempts
        """,
        (timestamp,),
    ).fetchone()
    return {
        "counts": counts,
        "readyCount": int(ready_row["count"]),
        "expiredDeliveringCount": count_expired_delivering(conn, timestamp),
    }


def get_health_payload():
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        queue = get_queue_summary(conn, timestamp)

    worker_state = snapshot_worker_state()
    worker_threads = list(WORKER_THREADS)
    worker_alive_count = sum(1 for thread in worker_threads if thread.is_alive())
    worker_thread_count = len(worker_threads)
    worker_alive = worker_alive_count > 0
    return {
        "status": "ok",
        "serviceVersion": SERVICE_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "database": {
            "path": str(DB_PATH),
            "ok": True,
        },
        "worker": {
            "alive": worker_alive,
            "concurrency": configured_worker_concurrency(),
            "threadCount": worker_thread_count,
            "aliveCount": worker_alive_count,
            "pollIntervalSeconds": WORKER_POLL_INTERVAL_SECONDS,
            "deliveringLeaseSeconds": delivering_lease_seconds(),
            "startedAt": format_ts(worker_state["started_at"]),
            "lastTickAt": format_ts(worker_state["last_tick_at"]),
            "lastClaimedJobId": worker_state["last_claimed_job_id"],
            "lastClaimedAt": format_ts(worker_state["last_claimed_at"]),
            "lastLeaseRecoveryAt": format_ts(worker_state["last_lease_recovery_at"]),
            "lastLeaseRecoveryCount": worker_state["last_lease_recovery_count"],
            "lastError": worker_state["last_error"],
        },
        "queue": queue,
        "now": format_ts(timestamp),
    }


def get_stats_payload():
    timestamp = now_ts()
    recent_error_window_seconds = 3600
    recent_error_since = timestamp - recent_error_window_seconds
    with DB_LOCK, get_db() as conn:
        queue = get_queue_summary(conn, timestamp)
        notification_row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(AVG(attempt_count), 0) AS average_attempts
              FROM notifications
            """
        ).fetchone()
        attempt_row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(AVG(attempt_sequence), 0) AS average_sequence
              FROM notification_attempts
            """
        ).fetchone()
        recent_error_row = conn.execute(
            """
            SELECT COUNT(*) AS count
              FROM notification_attempts
             WHERE status = 'failed'
               AND created_at >= ?
            """,
            (recent_error_since,),
        ).fetchone()
        error_type_rows = conn.execute(
            """
            SELECT COALESCE(error_type, 'unknown') AS error_type, COUNT(*) AS count
              FROM notification_attempts
             WHERE status = 'failed'
               AND created_at >= ?
             GROUP BY COALESCE(error_type, 'unknown')
            """,
            (recent_error_since,),
        ).fetchall()

    total_notifications = int(notification_row["total"])
    total_attempts = int(attempt_row["total"])
    return {
        "status": "ok",
        "serviceVersion": SERVICE_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "queue": queue,
        "notifications": {
            "total": total_notifications,
            "averageAttempts": round(float(notification_row["average_attempts"] or 0), 2),
        },
        "attempts": {
            "total": total_attempts,
            "averagePerNotification": round(total_attempts / total_notifications, 2)
            if total_notifications
            else 0.0,
            "averageSequence": round(float(attempt_row["average_sequence"] or 0), 2),
            "recentErrorCount": int(recent_error_row["count"]),
            "recentErrorWindowSeconds": recent_error_window_seconds,
            "recentErrorsByType": {row["error_type"]: int(row["count"]) for row in error_type_rows},
        },
        "now": format_ts(timestamp),
    }


def mark_dead_letter(notification_id, handled_by=None, note=None):
    timestamp = now_ts()
    handled_by, note = normalize_manual_action_fields(handled_by, note)
    with DB_LOCK, get_db() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
        if row is None:
            return None
        if row["status"] not in DEAD_LETTER_ELIGIBLE_STATUSES:
            allowed = ", ".join(sorted(DEAD_LETTER_ELIGIBLE_STATUSES))
            raise ValueError(f"notification status must be one of {allowed} to move to dead_letter")
        conn.execute(
            """
            UPDATE notifications
               SET status = 'dead_letter',
                   next_attempt_at = NULL,
                   last_manual_action = 'dead_letter',
                   last_manual_action_at = ?,
                   last_manual_action_by = ?,
                   resolution_note = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (timestamp, handled_by, note, timestamp, notification_id),
        )
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
    return row_to_dict(row)


def retry_notification(notification_id, handled_by=None, note=None):
    timestamp = now_ts()
    handled_by, note = normalize_manual_action_fields(handled_by, note)
    with DB_LOCK, get_db() as conn:
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
        if row is None:
            return None
        if row["status"] == "delivering":
            raise ValueError("notification is delivering and cannot be retried right now")
        conn.execute(
            """
            UPDATE notifications
               SET status = 'queued',
                   attempt_count = 0,
                   delivery_run = COALESCE(delivery_run, 1) + 1,
                   next_attempt_at = ?,
                   last_error = NULL,
                   failure_type = NULL,
                   last_status_code = NULL,
                   delivered_at = NULL,
                   last_manual_action = 'retry',
                   last_manual_action_at = ?,
                   last_manual_action_by = ?,
                   resolution_note = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (timestamp, timestamp, handled_by, note, timestamp, notification_id),
        )
        row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
    return row_to_dict(row)


def retry_notifications_batch(status="failed", limit=50, handled_by=None, note=None):
    if status not in BATCH_RETRY_STATUSES:
        allowed = ", ".join(sorted(BATCH_RETRY_STATUSES))
        raise ValueError(f"status must be one of {allowed}")
    limit = min(max(int(limit), 1), 200)
    timestamp = now_ts()
    handled_by, note = normalize_manual_action_fields(handled_by, note)
    with DB_LOCK, get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
              FROM notifications
             WHERE status = ?
             ORDER BY updated_at ASC, created_at ASC
             LIMIT ?
            """,
            (status, limit),
        ).fetchall()
        ids = [row["id"] for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE notifications
                   SET status = 'queued',
                       attempt_count = 0,
                       delivery_run = COALESCE(delivery_run, 1) + 1,
                       next_attempt_at = ?,
                       last_error = NULL,
                       failure_type = NULL,
                       last_status_code = NULL,
                       delivered_at = NULL,
                       last_manual_action = 'retry',
                       last_manual_action_at = ?,
                       last_manual_action_by = ?,
                       resolution_note = ?,
                       updated_at = ?
                 WHERE id IN ({placeholders})
                """,
                [timestamp, timestamp, handled_by, note, timestamp, *ids],
            )
            rows = conn.execute(
                f"""
                SELECT *
                  FROM notifications
                 WHERE id IN ({placeholders})
                 ORDER BY created_at DESC
                """,
                ids,
            ).fetchall()
    return {"count": len(rows), "items": [row_to_dict(row, include_body=False) for row in rows]}


def backoff_seconds(attempt_count):
    return min(5 * (2 ** max(attempt_count - 1, 0)), 300)


def elapsed_ms(started_at):
    return max(int(round((time.monotonic() - started_at) * 1000)), 0)


def claim_next_job():
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM notifications
             WHERE status IN ('queued', 'waiting_retry')
               AND next_attempt_at <= ?
               AND attempt_count < max_attempts
             ORDER BY next_attempt_at ASC, created_at ASC
             LIMIT 1
            """,
            (timestamp,),
        ).fetchone()
        if row is None:
            return None
        updated = conn.execute(
            """
            UPDATE notifications
               SET status = 'delivering',
                   attempt_count = attempt_count + 1,
                   updated_at = ?
             WHERE id = ?
               AND status IN ('queued', 'waiting_retry')
               AND next_attempt_at <= ?
               AND attempt_count < max_attempts
            """,
            (timestamp, row["id"], timestamp),
        ).rowcount
        if updated == 0:
            return None
        return conn.execute("SELECT * FROM notifications WHERE id = ?", (row["id"],)).fetchone()


def insert_attempt(conn, notification_id, attempt_number, status, status_code, error, error_type, duration_ms, timestamp):
    notification = conn.execute(
        "SELECT delivery_run FROM notifications WHERE id = ?",
        (notification_id,),
    ).fetchone()
    delivery_run = int(notification["delivery_run"] or 1) if notification else 1
    sequence_row = conn.execute(
        "SELECT COALESCE(MAX(attempt_sequence), 0) + 1 AS next_sequence FROM notification_attempts WHERE notification_id = ?",
        (notification_id,),
    ).fetchone()
    attempt_sequence = int(sequence_row["next_sequence"])
    conn.execute(
        """
        INSERT INTO notification_attempts (
            notification_id, attempt_number, attempt_sequence, delivery_run,
            status, status_code, error, error_type, duration_ms, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            notification_id,
            attempt_number,
            attempt_sequence,
            delivery_run,
            status,
            status_code,
            error[:1000] if error else None,
            error_type,
            duration_ms,
            timestamp,
        ),
    )


def reclaim_expired_deliveries(limit=50):
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        rows = conn.execute(
            """
            SELECT *
              FROM notifications
             WHERE status = 'delivering'
               AND updated_at <= ?
             ORDER BY updated_at ASC, created_at ASC
             LIMIT ?
            """,
            (expired_delivering_cutoff(timestamp), limit),
        ).fetchall()
        if not rows:
            return 0

        for row in rows:
            attempt_count = max(int(row["attempt_count"] or 0), 1)
            max_attempts = int(row["max_attempts"] or 1)
            can_retry = attempt_count < max_attempts
            status = "queued" if can_retry else "failed"
            next_attempt_at = timestamp if can_retry else None
            error = (
                "delivery lease expired before completion; requeued for retry"
                if can_retry
                else "delivery lease expired before completion; max attempts reached"
            )
            conn.execute(
                """
                UPDATE notifications
                   SET status = ?,
                       next_attempt_at = ?,
                       last_error = ?,
                       failure_type = 'lease_timeout',
                       last_status_code = NULL,
                       updated_at = ?
                 WHERE id = ?
                   AND status = 'delivering'
                """,
                (status, next_attempt_at, error, timestamp, row["id"]),
            )
            insert_attempt(conn, row["id"], attempt_count, "failed", None, error, "lease_timeout", 0, timestamp)

    update_worker_state(last_lease_recovery_at=timestamp, last_lease_recovery_count=len(rows))
    return len(rows)


def mark_success(row, status_code, duration_ms):
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        conn.execute(
            """
            UPDATE notifications
               SET status = 'succeeded',
                   last_error = NULL,
                   failure_type = NULL,
                   last_status_code = ?,
                   delivered_at = ?,
                   updated_at = ?,
                   next_attempt_at = NULL
             WHERE id = ?
            """,
            (status_code, timestamp, timestamp, row["id"]),
        )
        insert_attempt(conn, row["id"], row["attempt_count"], "succeeded", status_code, None, None, duration_ms, timestamp)


def mark_failure(row, status_code, error, error_type, duration_ms):
    timestamp = now_ts()
    attempt_count = int(row["attempt_count"])
    max_attempts = int(row["max_attempts"])
    if attempt_count >= max_attempts:
        status = "failed"
        next_attempt_at = None
    else:
        status = "waiting_retry"
        next_attempt_at = timestamp + backoff_seconds(attempt_count)

    with DB_LOCK, get_db() as conn:
        conn.execute(
            """
            UPDATE notifications
               SET status = ?,
                   last_error = ?,
                   failure_type = ?,
                   last_status_code = ?,
                   next_attempt_at = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (status, error[:1000], error_type, status_code, next_attempt_at, timestamp, row["id"]),
        )
        insert_attempt(conn, row["id"], attempt_count, "failed", status_code, error, error_type, duration_ms, timestamp)


def is_timeout_error(exc):
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, URLError):
        reason = exc.reason
        return isinstance(reason, TimeoutError) or "timed out" in str(reason).lower()
    return False


def deliver_job(row):
    started_at = time.monotonic()
    headers = json.loads(row["headers_json"] or "{}")
    body_bytes = row["body"].encode("utf-8")
    validation_origin = delivery_validation_origin(row["target_url"])
    timeout_seconds = row["timeout_seconds"] if row["timeout_seconds"] is not None else delivery_timeout_seconds()
    try:
        try:
            validate_target_url(row["target_url"], current_origin=validation_origin)
        except ValueError as exc:
            raise InvalidTargetError(f"target blocked: {exc}") from exc
        request = Request(
            row["target_url"],
            data=body_bytes,
            headers=headers,
            method=row["method"],
        )
        opener = build_safe_delivery_opener(current_origin=validation_origin)
        with opener.open(request, timeout=timeout_seconds) as response:
            status_code = response.getcode()
            if 200 <= status_code < 300:
                mark_success(row, status_code, elapsed_ms(started_at))
            else:
                mark_failure(row, status_code, f"target returned HTTP {status_code}", "http_error", elapsed_ms(started_at))
    except InvalidTargetError as exc:
        mark_failure(row, None, f"invalid target: {exc}", "invalid_target", elapsed_ms(started_at))
    except HTTPError as exc:
        mark_failure(row, exc.code, f"target returned HTTP {exc.code}", "http_error", elapsed_ms(started_at))
    except URLError as exc:
        error_type = "timeout" if is_timeout_error(exc) else "network_error"
        label = "timeout" if error_type == "timeout" else "network error"
        mark_failure(row, None, f"{label}: {exc.reason}", error_type, elapsed_ms(started_at))
    except TimeoutError as exc:
        mark_failure(row, None, f"timeout: {exc}", "timeout", elapsed_ms(started_at))
    except Exception as exc:
        update_worker_state(last_error=f"delivery error for {row['id']}: {exc}")
        mark_failure(row, None, f"delivery error: {exc}", "delivery_error", elapsed_ms(started_at))


def worker_loop():
    update_worker_state(started_at=now_ts(), last_tick_at=now_ts(), last_error=None)
    while not STOP_EVENT.is_set():
        update_worker_state(last_tick_at=now_ts())
        try:
            reclaim_expired_deliveries()
            row = claim_next_job()
            if row is None:
                STOP_EVENT.wait(WORKER_POLL_INTERVAL_SECONDS)
                continue
            update_worker_state(last_claimed_job_id=row["id"], last_claimed_at=now_ts())
            deliver_job(row)
        except Exception as exc:
            update_worker_state(last_error=f"worker loop error: {exc}")
            print(f"Worker error: {exc}")
            STOP_EVENT.wait(WORKER_POLL_INTERVAL_SECONDS)


def log_startup_self_check(worker_concurrency):
    try:
        allowed_targets = parse_allowed_target_origins()
        allowed_targets_label = "not set" if allowed_targets is None else ", ".join(sorted(allowed_targets))
    except ValueError as exc:
        allowed_targets_label = f"invalid: {exc}"
    print(f"Startup self-check: database.path={DB_PATH}")
    print(f"Startup self-check: public.exists={PUBLIC_DIR.exists()} path={PUBLIC_DIR}")
    print(f"Startup self-check: allowedTargets={allowed_targets_label}")
    print(f"Startup self-check: deliveryTimeoutSeconds={delivery_timeout_seconds()}")
    print(f"Startup self-check: deliveringLeaseSeconds={delivering_lease_seconds()}")
    print(f"Startup self-check: workerConcurrency={worker_concurrency}")


class NotificationHandler(BaseHTTPRequestHandler):
    server_version = "NotificationDemo/1.0"

    def do_OPTIONS(self):
        self.send_response(204)
        self.add_common_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            try:
                self.write_json(200, get_health_payload())
            except Exception as exc:
                self.write_json(
                    500,
                    {
                        "status": "degraded",
                        "serviceVersion": SERVICE_VERSION,
                        "schemaVersion": SCHEMA_VERSION,
                        "database": {
                            "path": str(DB_PATH),
                            "ok": False,
                        },
                        "error": str(exc),
                        "now": format_ts(now_ts()),
                    },
                )
            return
        if parsed.path == "/api/stats":
            self.write_json(200, get_stats_payload())
            return
        if parsed.path == "/api/notifications":
            query = parse_qs(parsed.query)
            status = query.get("status", [None])[0]
            limit = query.get("limit", ["50"])[0]
            offset = query.get("offset", ["0"])[0]
            event_type = query.get("eventType", [None])[0]
            source_system = query.get("sourceSystem", [None])[0]
            target_url = query.get("targetUrl", [None])[0]
            sort = query.get("sort", [None])[0]
            order = query.get("order", [None])[0]
            created_from = query.get("createdFrom", [None])[0]
            created_to = query.get("createdTo", [None])[0]
            updated_from = query.get("updatedFrom", [None])[0]
            updated_to = query.get("updatedTo", [None])[0]
            try:
                result = list_notifications(
                    status=status,
                    limit=limit,
                    offset=offset,
                    event_type=event_type,
                    source_system=source_system,
                    target_url=target_url,
                    sort=sort,
                    order=order,
                    created_from=created_from,
                    created_to=created_to,
                    updated_from=updated_from,
                    updated_to=updated_to,
                )
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            self.write_json(200, result)
            return
        if parsed.path == "/api/notifications/export.csv":
            query = parse_qs(parsed.query)
            try:
                content = export_notifications_csv(
                    status=query.get("status", [None])[0],
                    limit=query.get("limit", ["1000"])[0],
                    offset=query.get("offset", ["0"])[0],
                    event_type=query.get("eventType", [None])[0],
                    source_system=query.get("sourceSystem", [None])[0],
                    target_url=query.get("targetUrl", [None])[0],
                    sort=query.get("sort", [None])[0],
                    order=query.get("order", [None])[0],
                    created_from=query.get("createdFrom", [None])[0],
                    created_to=query.get("createdTo", [None])[0],
                    updated_from=query.get("updatedFrom", [None])[0],
                    updated_to=query.get("updatedTo", [None])[0],
                )
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            self.write_text(
                200,
                content,
                "text/csv; charset=utf-8",
                extra_headers={"Content-Disposition": 'attachment; filename="notifications.csv"'},
            )
            return
        if parsed.path.startswith("/api/notifications/") and parsed.path.endswith("/attempts"):
            notification_id = parsed.path.split("/")[-2]
            items = get_notification_attempts(notification_id)
            if items is None:
                self.write_json(404, {"error": "notification not found"})
                return
            self.write_json(200, {"items": items})
            return
        if parsed.path.startswith("/api/notifications/"):
            notification_id = parsed.path.rsplit("/", 1)[-1]
            item = get_notification(notification_id)
            if item is None:
                self.write_json(404, {"error": "notification not found"})
                return
            self.write_json(200, item)
            return
        if parsed.path.startswith("/mock/vendor/"):
            self.handle_mock_vendor(parsed)
            return
        self.serve_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if is_protected_notification_write_path(parsed.path) and not has_valid_notification_api_key(self.headers):
            self.write_json(401, {"error": "unauthorized"})
            return
        if parsed.path == "/api/notifications":
            try:
                payload = self.read_json_body()
                item, duplicate = create_notification(payload, current_origin=current_request_origin(self))
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            self.write_json(
                201 if not duplicate else 200,
                {
                    "id": item["id"],
                    "requestId": item["requestId"],
                    "status": item["status"],
                    "duplicate": duplicate,
                    "duplicated": duplicate,
                    "idempotent": duplicate,
                    "idempotency": "reused_existing" if duplicate else "created",
                },
            )
            return
        if parsed.path == "/api/notifications/retry":
            try:
                payload = self.read_json_body()
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                handled_by, note = parse_manual_action_payload(payload)
                result = retry_notifications_batch(
                    status=str(payload.get("status") or "failed"),
                    limit=payload.get("limit", 50),
                    handled_by=handled_by,
                    note=note,
                )
            except ValueError as exc:
                self.write_json(400, {"error": str(exc)})
                return
            self.write_json(200, result)
            return
        if parsed.path.startswith("/api/notifications/") and parsed.path.endswith("/dead-letter"):
            notification_id = parsed.path.split("/")[-2]
            try:
                payload = self.read_json_body()
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                handled_by, note = parse_manual_action_payload(payload)
                item = mark_dead_letter(
                    notification_id,
                    handled_by=handled_by,
                    note=note,
                )
            except ValueError as exc:
                self.write_json(409, {"error": str(exc)})
                return
            if item is None:
                self.write_json(404, {"error": "notification not found"})
                return
            self.write_json(200, item)
            return
        if parsed.path.startswith("/api/notifications/") and parsed.path.endswith("/retry"):
            notification_id = parsed.path.split("/")[-2]
            try:
                payload = self.read_json_body()
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                handled_by, note = parse_manual_action_payload(payload)
                item = retry_notification(
                    notification_id,
                    handled_by=handled_by,
                    note=note,
                )
            except ValueError as exc:
                self.write_json(409, {"error": str(exc)})
                return
            if item is None:
                self.write_json(404, {"error": "notification not found"})
                return
            self.write_json(200, item)
            return
        if parsed.path.startswith("/mock/vendor/"):
            self.handle_mock_vendor(parsed)
            return
        self.write_json(404, {"error": "not found"})

    def do_PUT(self):
        self.do_POST()

    def do_PATCH(self):
        self.do_POST()

    def handle_mock_vendor(self, parsed):
        vendor_name = parsed.path.rsplit("/", 1)[-1] or "unknown"
        query = parse_qs(parsed.query)
        body = self.read_raw_body().decode("utf-8", errors="replace")
        delay_ms_raw = query.get("delayMs", ["0"])[0]
        try:
            delay_ms = max(int(delay_ms_raw), 0)
        except (TypeError, ValueError):
            delay_ms = 0
        if delay_ms:
            time.sleep(delay_ms / 1000)

        redirect_target = query.get("redirectTo", [None])[0]
        redirect_mode = query.get("redirect", [None])[0]
        if redirect_mode == "blocked":
            redirect_target = f"{current_request_origin(self)}/not-mock/redirect-blocked"
        elif redirect_mode == "mock":
            redirect_target = f"{current_request_origin(self)}/mock/vendor/{vendor_name}-redirected"
        if redirect_target:
            redirect_status_raw = query.get("redirectStatus", ["302"])[0]
            try:
                redirect_status = int(redirect_status_raw)
            except (TypeError, ValueError):
                redirect_status = 302
            if redirect_status not in {301, 302, 303, 307, 308}:
                redirect_status = 302
            payload = {
                "vendor": vendor_name,
                "received": True,
                "status": redirect_status,
                "method": self.command,
                "redirectTo": redirect_target,
                "body": body,
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(redirect_status)
            self.add_common_headers()
            self.send_header("Location", redirect_target)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        should_fail = query.get("fail", ["0"])[0] in {"1", "true", "yes"}
        should_fail = should_fail or self.headers.get("X-Mock-Fail", "").lower() in {"1", "true", "yes"}
        status_raw = query.get("status", [None])[0]
        status_code = 500 if should_fail else 200
        if status_raw and re.fullmatch(r"\d{3}", status_raw):
            status_code = int(status_raw)
        received = 200 <= status_code < 300
        if should_fail:
            self.write_json(
                status_code,
                {
                    "vendor": vendor_name,
                    "received": received,
                    "message": "mock failure",
                    "status": status_code,
                    "method": self.command,
                    "body": body,
                },
            )
            return
        self.write_json(
            status_code,
            {
                "vendor": vendor_name,
                "received": received,
                "status": status_code,
                "method": self.command,
                "body": body,
            },
        )

    def read_raw_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1024 * 1024:
            raise ValueError("request body is too large")
        return self.rfile.read(length) if length else b""

    def read_json_body(self):
        try:
            raw = self.read_raw_body()
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("request body must be valid JSON") from exc

    def serve_static(self, path):
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        file_path = (PUBLIC_DIR / relative).resolve()
        public_root = PUBLIC_DIR.resolve()
        if public_root not in file_path.parents and file_path != public_root:
            self.write_json(403, {"error": "forbidden"})
            return
        if not file_path.exists() or file_path.is_dir():
            self.write_json(404, {"error": "not found"})
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.add_common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_json(self, status_code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.add_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_text(self, status_code, text, content_type, extra_headers=None):
        data = text.encode("utf-8")
        self.send_response(status_code)
        self.add_common_headers()
        self.send_header("Content-Type", content_type)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def add_common_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Notification-Api-Key, X-Requested-With",
        )

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")


def main():
    global SERVICE_ORIGIN, WORKER_CONCURRENCY

    parser = argparse.ArgumentParser(description="Internal HTTP notification delivery demo service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()

    SERVICE_ORIGIN = origin_from_parsed(urlparse(f"http://{args.host}:{args.port}"))
    WORKER_CONCURRENCY = notification_worker_concurrency()
    init_db()
    log_startup_self_check(WORKER_CONCURRENCY)
    WORKER_THREADS.clear()
    for index in range(WORKER_CONCURRENCY):
        worker_thread = threading.Thread(
            target=worker_loop,
            name=f"notification-worker-{index + 1}",
            daemon=True,
        )
        WORKER_THREADS.append(worker_thread)
        worker_thread.start()

    server = ThreadingHTTPServer((args.host, args.port), NotificationHandler)
    print(f"Notification service running at http://{args.host}:{args.port}")
    print(f"Frontend: http://{args.host}:{args.port}/")
    print(f"Mock vendor: http://{args.host}:{args.port}/mock/vendor/crm")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        STOP_EVENT.set()
        server.server_close()


if __name__ == "__main__":
    main()
