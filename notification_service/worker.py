import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .database import DB_LOCK, expired_delivering_cutoff, get_db
from .security import (
    InvalidTargetError,
    build_safe_delivery_opener,
    delivery_validation_origin,
    validate_target_url,
)
from .settings import WORKER_POLL_INTERVAL_SECONDS, delivery_timeout_seconds, notification_worker_concurrency
from .time_utils import elapsed_ms, now_ts


STOP_EVENT = threading.Event()
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


def update_worker_state(**values):
    with WORKER_STATE_LOCK:
        WORKER_STATE.update(values)

def snapshot_worker_state():
    with WORKER_STATE_LOCK:
        return dict(WORKER_STATE)

def configured_worker_concurrency():
    return WORKER_CONCURRENCY if WORKER_CONCURRENCY is not None else notification_worker_concurrency()

def backoff_seconds(attempt_count):
    return min(5 * (2 ** max(attempt_count - 1, 0)), 300)

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


def worker_runtime_snapshot():
    worker_threads = list(WORKER_THREADS)
    return {
        "state": snapshot_worker_state(),
        "threads": worker_threads,
        "threadCount": len(worker_threads),
        "aliveCount": sum(1 for thread in worker_threads if thread.is_alive()),
        "concurrency": configured_worker_concurrency(),
    }


class DeliveryWorkerPool:
    """Worker-pool facade that starts and stops delivery worker threads."""

    def start(self, concurrency=None):
        global WORKER_CONCURRENCY
        STOP_EVENT.clear()
        WORKER_CONCURRENCY = concurrency if concurrency is not None else notification_worker_concurrency()
        WORKER_THREADS.clear()
        for index in range(WORKER_CONCURRENCY):
            worker_thread = threading.Thread(
                target=worker_loop,
                name=f"notification-worker-{index + 1}",
                daemon=True,
            )
            WORKER_THREADS.append(worker_thread)
            worker_thread.start()
        return WORKER_CONCURRENCY

    def stop(self):
        STOP_EVENT.set()


delivery_worker_pool = DeliveryWorkerPool()
