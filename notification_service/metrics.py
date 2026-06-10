from .database import DB_LOCK, get_db, get_queue_summary
from .settings import DB_PATH, SCHEMA_VERSION, SERVICE_VERSION, WORKER_POLL_INTERVAL_SECONDS, delivering_lease_seconds
from .time_utils import format_ts, now_ts
from .worker import worker_runtime_snapshot


def get_health_payload():
    timestamp = now_ts()
    with DB_LOCK, get_db() as conn:
        queue = get_queue_summary(conn, timestamp)

    worker_runtime = worker_runtime_snapshot()
    worker_state = worker_runtime["state"]
    worker_alive_count = worker_runtime["aliveCount"]
    worker_thread_count = worker_runtime["threadCount"]
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
            "concurrency": worker_runtime["concurrency"],
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


class HealthReporter:
    """Read model for health and aggregate statistics."""

    def health(self):
        return get_health_payload()

    def stats(self):
        return get_stats_payload()


health_reporter = HealthReporter()
