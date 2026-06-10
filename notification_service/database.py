import json
import sqlite3
import threading

from .settings import DB_PATH, QUEUE_STATUSES, delivering_lease_seconds
from .security import body_preview_for_api, redact_body_for_api, redact_headers, redact_query_secrets
from .time_utils import format_ts, now_ts


DB_LOCK = threading.Lock()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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


class NotificationDatabase:
    """Gateway for database initialization and connection creation."""

    def connect(self):
        return get_db()

    def initialize(self):
        init_db()


database = NotificationDatabase()
