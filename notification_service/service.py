import csv
import io
import json
import math
import sqlite3
import uuid
from datetime import datetime, timezone

from .database import DB_LOCK, attempt_row_to_dict, get_db, row_to_dict
from .security import validate_target_url
from .settings import (
    ALLOWED_METHODS,
    BATCH_RETRY_STATUSES,
    CSV_EXPORT_FIELDS,
    DEAD_LETTER_ELIGIBLE_STATUSES,
    LIST_SORT_COLUMNS,
    MAX_DELIVERY_TIMEOUT_SECONDS,
    MIN_DELIVERY_TIMEOUT_SECONDS,
)
from .time_utils import now_ts


def normalize_manual_text(value, max_length):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_length]

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


class NotificationService:
    """Use-case facade for notification commands and queries."""

    def create(self, payload, current_origin=None):
        return create_notification(payload, current_origin=current_origin)

    def list(self, **filters):
        return list_notifications(**filters)

    def export_csv(self, **filters):
        return export_notifications_csv(**filters)

    def get(self, notification_id):
        return get_notification(notification_id)

    def attempts(self, notification_id):
        return get_notification_attempts(notification_id)

    def retry_one(self, notification_id, handled_by=None, note=None):
        return retry_notification(notification_id, handled_by=handled_by, note=note)

    def retry_batch(self, status="failed", limit=50, handled_by=None, note=None):
        return retry_notifications_batch(status=status, limit=limit, handled_by=handled_by, note=note)

    def dead_letter(self, notification_id, handled_by=None, note=None):
        return mark_dead_letter(notification_id, handled_by=handled_by, note=note)


notification_service = NotificationService()
