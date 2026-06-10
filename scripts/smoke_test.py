#!/usr/bin/env python3
import argparse
import csv
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT_DIR / "server.py"
MIN_DELIVERY_TIMEOUT_SECONDS = 0.1
MAX_DELIVERY_TIMEOUT_SECONDS = 60.0
SMOKE_WORKER_CONCURRENCY = 2


class SmokeError(Exception):
    pass


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request_json(base_url, method, path, payload=None, timeout=5, headers=None):
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(f"{base_url}{path}", data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.getcode(), json.loads(raw) if raw else None
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw
        return exc.code, body
    except URLError as exc:
        raise SmokeError(f"request failed: {exc}") from exc


def request_text(base_url, method, path, payload=None, timeout=5, headers=None):
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(f"{base_url}{path}", data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.getcode(), dict(response.headers), response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise SmokeError(f"request failed: {exc}") from exc


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def require_number_close(value, expected, message, tolerance=0.001):
    require(isinstance(value, (int, float)), message)
    require(abs(float(value) - expected) <= tolerance, message)


def wait_for_service(base_url, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, body = request_json(base_url, "GET", "/health", timeout=1)
            if status == 200 and body and body.get("status") == "ok":
                return
        except SmokeError:
            pass
        time.sleep(0.2)
    raise SmokeError(f"service did not become healthy at {base_url}")


def require_health_schema(base_url, require_lease_fields=True, expected_worker_concurrency=None):
    status, body = request_json(base_url, "GET", "/health")
    require(status == 200, f"health returned {status}: {body}")
    require(isinstance(body, dict), f"health response should be an object: {body}")
    require(body.get("status") == "ok", f"health status should be ok: {body}")
    require(
        isinstance(body.get("serviceVersion"), str) and body.get("serviceVersion"),
        f"health serviceVersion invalid: {body}",
    )
    require(
        isinstance(body.get("schemaVersion"), str) and body.get("schemaVersion"),
        f"health schemaVersion invalid: {body}",
    )

    database = body.get("database")
    require(isinstance(database, dict), f"health database should be an object: {body}")
    require(database.get("ok") is True, f"health database.ok should be true: {body}")
    require(isinstance(database.get("path"), str) and database.get("path"), f"health database.path invalid: {body}")

    worker = body.get("worker")
    require(isinstance(worker, dict), f"health worker should be an object: {body}")
    require(worker.get("alive") is True, f"health worker.alive should be true: {body}")
    require(isinstance(worker.get("concurrency"), int), f"health worker.concurrency invalid: {body}")
    require(isinstance(worker.get("threadCount"), int), f"health worker.threadCount invalid: {body}")
    require(isinstance(worker.get("aliveCount"), int), f"health worker.aliveCount invalid: {body}")
    require(worker.get("aliveCount") >= 1, f"health worker.aliveCount should be positive: {body}")
    require(worker.get("threadCount") >= worker.get("aliveCount"), f"health worker thread counts invalid: {body}")
    if expected_worker_concurrency is not None:
        require(
            worker.get("concurrency") == expected_worker_concurrency,
            f"health worker.concurrency should be {expected_worker_concurrency}: {body}",
        )
        require(
            worker.get("threadCount") == expected_worker_concurrency,
            f"health worker.threadCount should be {expected_worker_concurrency}: {body}",
        )
        require(
            worker.get("aliveCount") == expected_worker_concurrency,
            f"health worker.aliveCount should be {expected_worker_concurrency}: {body}",
        )
    require(
        isinstance(worker.get("pollIntervalSeconds"), (int, float)),
        f"health worker.pollIntervalSeconds invalid: {body}",
    )
    if require_lease_fields:
        require(
            isinstance(worker.get("deliveringLeaseSeconds"), (int, float)),
            f"health worker.deliveringLeaseSeconds invalid: {body}",
        )
        require(
            isinstance(worker.get("lastLeaseRecoveryCount"), int),
            f"health worker.lastLeaseRecoveryCount invalid: {body}",
        )

    queue = body.get("queue")
    require(isinstance(queue, dict), f"health queue should be an object: {body}")
    counts = queue.get("counts")
    require(isinstance(counts, dict), f"health queue.counts should be an object: {body}")
    for queue_status in ("queued", "delivering", "waiting_retry", "succeeded", "failed", "dead_letter"):
        require(isinstance(counts.get(queue_status), int), f"health count for {queue_status} invalid: {body}")
    require(isinstance(queue.get("readyCount"), int), f"health queue.readyCount invalid: {body}")
    if require_lease_fields:
        require(
            isinstance(queue.get("expiredDeliveringCount"), int),
            f"health queue.expiredDeliveringCount invalid: {body}",
        )
    require(isinstance(body.get("now"), str) and body.get("now"), f"health now invalid: {body}")
    return body


def require_stats_schema(base_url, require_lease_fields=True):
    status, body = request_json(base_url, "GET", "/api/stats")
    require(status == 200, f"stats returned {status}: {body}")
    require(isinstance(body, dict), f"stats response should be an object: {body}")
    require(body.get("status") == "ok", f"stats status should be ok: {body}")
    require(
        isinstance(body.get("serviceVersion"), str) and body.get("serviceVersion"),
        f"stats serviceVersion invalid: {body}",
    )
    require(
        isinstance(body.get("schemaVersion"), str) and body.get("schemaVersion"),
        f"stats schemaVersion invalid: {body}",
    )

    queue = body.get("queue")
    require(isinstance(queue, dict), f"stats queue should be an object: {body}")
    counts = queue.get("counts")
    require(isinstance(counts, dict), f"stats queue.counts should be an object: {body}")
    for queue_status in ("queued", "delivering", "waiting_retry", "succeeded", "failed", "dead_letter"):
        require(isinstance(counts.get(queue_status), int), f"stats count for {queue_status} invalid: {body}")
    require(isinstance(queue.get("readyCount"), int), f"stats queue.readyCount invalid: {body}")
    if require_lease_fields:
        require(
            isinstance(queue.get("expiredDeliveringCount"), int),
            f"stats queue.expiredDeliveringCount invalid: {body}",
        )

    notifications = body.get("notifications")
    require(isinstance(notifications, dict), f"stats notifications should be an object: {body}")
    require(isinstance(notifications.get("total"), int), f"stats notifications.total invalid: {body}")
    require(
        isinstance(notifications.get("averageAttempts"), (int, float)),
        f"stats notifications.averageAttempts invalid: {body}",
    )

    attempts = body.get("attempts")
    require(isinstance(attempts, dict), f"stats attempts should be an object: {body}")
    require(isinstance(attempts.get("total"), int), f"stats attempts.total invalid: {body}")
    require(
        isinstance(attempts.get("averagePerNotification"), (int, float)),
        f"stats attempts.averagePerNotification invalid: {body}",
    )
    require(
        isinstance(attempts.get("recentErrorCount"), int),
        f"stats attempts.recentErrorCount invalid: {body}",
    )
    require(
        isinstance(attempts.get("recentErrorWindowSeconds"), int),
        f"stats attempts.recentErrorWindowSeconds invalid: {body}",
    )
    require(
        isinstance(attempts.get("recentErrorsByType"), dict),
        f"stats attempts.recentErrorsByType invalid: {body}",
    )
    require(isinstance(body.get("now"), str) and body.get("now"), f"stats now invalid: {body}")
    return body


def post_notification(base_url, payload, headers=None):
    status, body = request_json(base_url, "POST", "/api/notifications", payload=payload, headers=headers)
    return status, body


def create_notification(base_url, payload, headers=None):
    status, body = post_notification(base_url, payload, headers=headers)
    require(status in {200, 201}, f"create notification returned {status}: {body}")
    require(body and body.get("id"), f"create notification did not return an id: {body}")
    return body["id"]


def list_notifications_response(base_url, **params):
    query = {"limit": 200}
    query.update({key: value for key, value in params.items() if value is not None})
    status, body = request_json(base_url, "GET", f"/api/notifications?{urlencode(query)}")
    require(status == 200, f"list notifications returned {status}: {body}")
    require(isinstance(body, dict) and isinstance(body.get("items"), list), f"list response is invalid: {body}")
    pagination = body.get("pagination")
    require(isinstance(pagination, dict), f"list pagination is missing or invalid: {body}")
    require(isinstance(pagination.get("limit"), int), f"pagination.limit invalid: {body}")
    require(isinstance(pagination.get("offset"), int), f"pagination.offset invalid: {body}")
    require(isinstance(pagination.get("count"), int), f"pagination.count invalid: {body}")
    require(isinstance(pagination.get("hasMore"), bool), f"pagination.hasMore invalid: {body}")
    require(pagination.get("count") == len(body["items"]), f"pagination count should match items: {body}")
    return body


def list_notifications(base_url, **params):
    body = list_notifications_response(base_url, **params)
    return body["items"]


def export_notifications_csv(base_url, **params):
    query = {"limit": 1000}
    query.update({key: value for key, value in params.items() if value is not None})
    status, headers, text = request_text(base_url, "GET", f"/api/notifications/export.csv?{urlencode(query)}")
    require(status == 200, f"CSV export returned {status}: {text}")
    content_type = headers.get("Content-Type", "")
    require("text/csv" in content_type, f"CSV export content type invalid: {content_type}")
    return text


def get_notification(base_url, notification_id):
    status, body = request_json(base_url, "GET", f"/api/notifications/{notification_id}")
    require(status == 200, f"get notification {notification_id} returned {status}: {body}")
    return body


def wait_for_status(base_url, notification_id, expected_status, timeout=20):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = get_notification(base_url, notification_id)
        if last.get("status") == expected_status:
            return last
        time.sleep(0.3)
    raise SmokeError(
        f"notification {notification_id} did not reach {expected_status}; last status was {last.get('status') if last else None}"
    )


def get_attempts(base_url, notification_id):
    status, body = request_json(base_url, "GET", f"/api/notifications/{notification_id}/attempts")
    require(status == 200, f"get attempts for {notification_id} returned {status}: {body}")
    require(isinstance(body, dict) and isinstance(body.get("items"), list), f"attempts response is invalid: {body}")
    return body["items"]


def retry_notification(base_url, notification_id, payload=None, headers=None):
    status, body = request_json(
        base_url,
        "POST",
        f"/api/notifications/{notification_id}/retry",
        payload=payload or {},
        headers=headers,
    )
    require(status == 200, f"retry notification {notification_id} returned {status}: {body}")
    return body


def retry_notifications_batch(base_url, payload=None, headers=None):
    status, body = request_json(base_url, "POST", "/api/notifications/retry", payload=payload or {}, headers=headers)
    require(status == 200, f"batch retry returned {status}: {body}")
    require(isinstance(body, dict) and isinstance(body.get("items"), list), f"batch retry response is invalid: {body}")
    return body


def dead_letter_notification(base_url, notification_id, payload=None, expected_status=200, headers=None):
    status, body = request_json(
        base_url,
        "POST",
        f"/api/notifications/{notification_id}/dead-letter",
        payload=payload or {},
        headers=headers,
    )
    require(status == expected_status, f"dead-letter notification {notification_id} returned {status}: {body}")
    return body


def require_last_attempt_error_type(attempts, expected_error_type):
    require(attempts, "notification should have at least one attempt")
    last_attempt = attempts[-1]
    require(
        last_attempt.get("errorType") == expected_error_type,
        f"last attempt errorType should be {expected_error_type}: {attempts}",
    )
    return last_attempt


def run_smoke(
    base_url,
    include_timeout=True,
    require_lease_health=True,
    require_idempotency_fields=True,
    isolated_queue=True,
    expected_worker_concurrency=None,
):
    suffix = time.time_ns()
    short_wait = 10 if isolated_queue else 30
    retry_wait = 20 if isolated_queue else 60

    require_health_schema(
        base_url,
        require_lease_fields=require_lease_health,
        expected_worker_concurrency=expected_worker_concurrency,
    )
    print("ok health schema includes database, worker, and queue state")
    initial_stats = require_stats_schema(base_url, require_lease_fields=require_lease_health)
    require(initial_stats["attempts"]["recentErrorCount"] >= 0, f"stats recent error count invalid: {initial_stats}")
    print("ok stats API schema includes queue and attempt summaries")

    idempotent_request_id = f"smoke-idempotent-{suffix}"
    idempotent_payload = {
        "requestId": idempotent_request_id,
        "targetUrl": f"{base_url}/mock/vendor/smoke-idempotent",
        "method": "POST",
        "body": {"case": "idempotent-first"},
        "maxAttempts": 1,
    }
    first_status, first_body = post_notification(base_url, idempotent_payload)
    require(first_status == 201, f"first idempotent create should return 201: {first_status} {first_body}")
    require(first_body and first_body.get("id"), f"first idempotent create did not return an id: {first_body}")
    require(first_body.get("duplicate") is False, f"first idempotent create should not be duplicate: {first_body}")
    duplicate_status, duplicate_body = post_notification(
        base_url,
        {
            **idempotent_payload,
            "targetUrl": f"{base_url}/mock/vendor/smoke-idempotent-changed",
            "body": {"case": "idempotent-duplicate"},
        },
    )
    require(duplicate_status == 200, f"duplicate requestId should return 200: {duplicate_status} {duplicate_body}")
    require(duplicate_body.get("id") == first_body.get("id"), f"duplicate requestId should return existing id: {duplicate_body}")
    require(duplicate_body.get("duplicate") is True, f"duplicate flag should be true: {duplicate_body}")
    if require_idempotency_fields:
        require(duplicate_body.get("duplicated") is True, f"duplicated flag should be true: {duplicate_body}")
        require(duplicate_body.get("idempotent") is True, f"idempotent flag should be true: {duplicate_body}")
        require(duplicate_body.get("idempotency") == "reused_existing", f"idempotency marker invalid: {duplicate_body}")
    idempotent_done = wait_for_status(base_url, first_body["id"], "succeeded", timeout=short_wait)
    require(
        idempotent_done.get("targetUrl", "").endswith("/mock/vendor/smoke-idempotent"),
        f"duplicate request should not overwrite original targetUrl: {idempotent_done}",
    )
    idempotent_items = [item for item in list_notifications(base_url) if item.get("requestId") == idempotent_request_id]
    require(len(idempotent_items) == 1, f"duplicate requestId should create exactly one notification: {idempotent_items}")
    print(f"ok requestId idempotency returns existing notification: {first_body['id']}")

    success_id = create_notification(
        base_url,
        {
            "requestId": f"smoke-success-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-success",
            "method": "POST",
            "body": {"case": "success"},
            "maxAttempts": 1,
        },
    )
    success = wait_for_status(base_url, success_id, "succeeded", timeout=short_wait)
    success_attempts = get_attempts(base_url, success_id)
    require(success.get("attemptCount") == 1, f"success attemptCount should be 1: {success}")
    require(len(success_attempts) >= 1, "success notification should have at least one attempt")
    require(success_attempts[-1].get("status") == "succeeded", f"success attempt status invalid: {success_attempts}")
    require(success_attempts[-1].get("statusCode") == 200, f"success status code invalid: {success_attempts}")
    require(isinstance(success_attempts[-1].get("durationMs"), int), f"success duration invalid: {success_attempts}")
    print(f"ok success delivery: {success_id} ({len(success_attempts)} attempt)")

    localhost_base_url = base_url.replace("127.0.0.1", "localhost", 1)
    localhost_mock_id = create_notification(
        localhost_base_url,
        {
            "requestId": f"smoke-localhost-mock-{suffix}",
            "targetUrl": f"{localhost_base_url}/mock/vendor/smoke-localhost-mock",
            "method": "POST",
            "body": {"case": "localhost-mock"},
            "maxAttempts": 1,
        },
    )
    wait_for_status(base_url, localhost_mock_id, "succeeded", timeout=short_wait)
    print(f"ok localhost mock delivery remains allowed: {localhost_mock_id}")

    blocked_request_id = f"smoke-blocked-localhost-{suffix}"
    blocked_status, blocked_body = post_notification(
        base_url,
        {
            "requestId": blocked_request_id,
            "targetUrl": f"{base_url}/not-mock/vendor",
            "method": "POST",
            "body": {"case": "blocked"},
            "maxAttempts": 1,
        },
    )
    require(blocked_status == 400, f"127.0.0.1 non-mock target should be rejected: {blocked_status} {blocked_body}")
    require(
        "blocked SSRF address" in (blocked_body or {}).get("error", ""),
        f"blocked localhost error should explain SSRF rejection: {blocked_body}",
    )
    items = list_notifications(base_url)
    require(
        all(item.get("requestId") != blocked_request_id for item in items),
        f"blocked notification should not be inserted: {items}",
    )
    print("ok localhost non-mock target rejected before insert")

    sensitive_id = create_notification(
        base_url,
        {
            "requestId": f"smoke-sensitive-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-sensitive",
            "method": "POST",
            "headers": {
                "Authorization": "Bearer real-token",
                "X-Api-Key": "real-api-key",
                "X-Trace-Id": "trace-visible",
            },
            "body": {
                "token": "body-token",
                "customer": "visible",
                "nested": {"password": "body-password", "note": "visible-note"},
            },
            "maxAttempts": 1,
        },
    )
    sensitive = wait_for_status(base_url, sensitive_id, "succeeded", timeout=short_wait)
    require(sensitive.get("headers", {}).get("Authorization") == "[REDACTED]", f"authorization not redacted: {sensitive}")
    require(sensitive.get("headers", {}).get("X-Api-Key") == "[REDACTED]", f"api key not redacted: {sensitive}")
    require(sensitive.get("headers", {}).get("X-Trace-Id") == "trace-visible", f"non-sensitive header changed: {sensitive}")
    try:
        redacted_body = json.loads(sensitive.get("body") or "{}")
    except json.JSONDecodeError as exc:
        raise SmokeError(f"sensitive body should remain JSON text: {sensitive}") from exc
    require(redacted_body.get("token") == "[REDACTED]", f"body token not redacted: {redacted_body}")
    require(redacted_body.get("customer") == "visible", f"non-sensitive body field changed: {redacted_body}")
    require(
        redacted_body.get("nested", {}).get("password") == "[REDACTED]",
        f"nested body password not redacted: {redacted_body}",
    )
    require(
        redacted_body.get("nested", {}).get("note") == "visible-note",
        f"nested non-sensitive body field changed: {redacted_body}",
    )
    print(f"ok sensitive fields redacted in detail API: {sensitive_id}")

    failure_id = create_notification(
        base_url,
        {
            "requestId": f"smoke-failure-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-failure?fail=1",
            "method": "POST",
            "body": {"case": "failure"},
            "maxAttempts": 2,
        },
    )
    failure = wait_for_status(base_url, failure_id, "failed", timeout=retry_wait)
    failure_attempts = get_attempts(base_url, failure_id)
    require(failure.get("attemptCount") == 2, f"failure attemptCount should be 2: {failure}")
    require(len(failure_attempts) >= 2, f"failure notification should have retry attempts: {failure_attempts}")
    require(
        all(item.get("status") == "failed" for item in failure_attempts[-2:]),
        f"failure attempt statuses invalid: {failure_attempts}",
    )
    require(
        all(item.get("statusCode") == 500 for item in failure_attempts[-2:]),
        f"failure attempt status codes invalid: {failure_attempts}",
    )
    require(failure.get("failureType") == "http_error", f"failureType should be http_error: {failure}")
    require_last_attempt_error_type(failure_attempts, "http_error")
    print(f"ok failure retry: {failure_id} ({len(failure_attempts)} attempts)")

    require(failure.get("deliveryRun") == 1, f"initial failed notification deliveryRun should be 1: {failure}")
    old_attempt_count = len(failure_attempts)
    old_sequences = [item.get("attemptSequence") for item in failure_attempts]
    require(old_sequences == list(range(1, old_attempt_count + 1)), f"attemptSequence should start at 1: {failure_attempts}")
    require(
        all(item.get("deliveryRun") == 1 for item in failure_attempts),
        f"initial attempts should be in deliveryRun 1: {failure_attempts}",
    )
    retried = retry_notification(base_url, failure_id)
    require(retried.get("deliveryRun") == 2, f"manual retry should increment deliveryRun: {retried}")
    require(retried.get("attemptCount") == 0, f"manual retry should reset attemptCount: {retried}")
    retried_failure = wait_for_status(base_url, failure_id, "failed", timeout=retry_wait)
    retried_attempts = get_attempts(base_url, failure_id)
    require(retried_failure.get("deliveryRun") == 2, f"retried failure should remain deliveryRun 2: {retried_failure}")
    require(len(retried_attempts) > old_attempt_count, f"manual retry should preserve and append attempts: {retried_attempts}")
    require(
        [item.get("attemptSequence") for item in retried_attempts] == list(range(1, len(retried_attempts) + 1)),
        f"attemptSequence should be full-history increasing: {retried_attempts}",
    )
    require(
        [item.get("attemptNumber") for item in retried_attempts[old_attempt_count:]] == list(
            range(1, len(retried_attempts) - old_attempt_count + 1)
        ),
        f"attemptNumber should restart within deliveryRun: {retried_attempts}",
    )
    require(
        all(item.get("deliveryRun") == 2 for item in retried_attempts[old_attempt_count:]),
        f"new attempts should be in deliveryRun 2: {retried_attempts}",
    )
    print(f"ok manual retry keeps attempt history and increments deliveryRun: {failure_id}")

    http_error_id = create_notification(
        base_url,
        {
            "requestId": f"smoke-http-429-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-http-429?status=429",
            "method": "POST",
            "body": {"case": "http_error"},
            "maxAttempts": 1,
        },
    )
    http_error = wait_for_status(base_url, http_error_id, "failed", timeout=short_wait)
    http_error_attempts = get_attempts(base_url, http_error_id)
    require(http_error.get("failureType") == "http_error", f"429 failureType should be http_error: {http_error}")
    require(http_error.get("lastStatusCode") == 429, f"429 lastStatusCode invalid: {http_error}")
    http_error_attempt = require_last_attempt_error_type(http_error_attempts, "http_error")
    require(http_error_attempt.get("statusCode") == 429, f"429 attempt statusCode invalid: {http_error_attempts}")
    print(f"ok 429 classified as http_error: {http_error_id}")

    redirect_blocked_id = create_notification(
        base_url,
        {
            "requestId": f"smoke-redirect-blocked-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-redirect-blocked?redirect=blocked",
            "method": "POST",
            "body": {"case": "redirect-blocked"},
            "maxAttempts": 1,
        },
    )
    redirect_blocked = wait_for_status(base_url, redirect_blocked_id, "failed", timeout=short_wait)
    redirect_attempts = get_attempts(base_url, redirect_blocked_id)
    require(
        redirect_blocked.get("failureType") == "invalid_target",
        f"blocked redirect failureType should be invalid_target: {redirect_blocked}",
    )
    require(redirect_blocked.get("lastStatusCode") is None, f"blocked redirect should not have status code: {redirect_blocked}")
    redirect_attempt = require_last_attempt_error_type(redirect_attempts, "invalid_target")
    require(
        "redirect target blocked" in (redirect_attempt.get("error") or ""),
        f"blocked redirect attempt should explain redirect validation: {redirect_attempts}",
    )
    print(f"ok blocked redirect target classified as invalid_target: {redirect_blocked_id}")

    if isolated_queue:
        batch_id = create_notification(
            base_url,
            {
                "requestId": f"smoke-batch-retry-{suffix}",
                "targetUrl": f"{base_url}/mock/vendor/smoke-batch-retry?fail=1",
                "method": "POST",
                "body": {"case": "batch-retry"},
                "maxAttempts": 1,
            },
        )
        batch_failed = wait_for_status(base_url, batch_id, "failed", timeout=short_wait)
        batch_result = retry_notifications_batch(base_url, {"status": "failed", "limit": 50})
        batch_items_by_id = {item.get("id"): item for item in batch_result["items"]}
        require(batch_result.get("count", 0) >= 1, f"batch retry should retry at least one failed task: {batch_result}")
        require(batch_id in batch_items_by_id, f"batch retry should include the target failed task: {batch_result}")
        batch_item = batch_items_by_id[batch_id]
        require(batch_item.get("status") == "queued", f"batch retry should requeue task: {batch_item}")
        require(batch_item.get("attemptCount") == 0, f"batch retry should reset attemptCount: {batch_item}")
        require(
            batch_item.get("deliveryRun") == batch_failed.get("deliveryRun") + 1,
            f"batch retry should increment deliveryRun: before={batch_failed} after={batch_item}",
        )
        print(f"ok batch retry requeued failed notification: {batch_id}")
    else:
        print("skip batch retry mutation: --base-url may point at a shared local database")

    dead_letter_id = create_notification(
        base_url,
        {
            "requestId": f"smoke-dead-letter-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-dead-letter?fail=1",
            "method": "POST",
            "body": {"case": "dead-letter"},
            "maxAttempts": 1,
        },
    )
    dead_letter_failed = wait_for_status(base_url, dead_letter_id, "failed", timeout=short_wait)
    dead_letter_attempts_before = get_attempts(base_url, dead_letter_id)
    dead_lettered = dead_letter_notification(
        base_url,
        dead_letter_id,
        {"actionBy": "smoke-operator", "resolutionNote": "vendor outage needs manual follow-up"},
    )
    require(dead_lettered.get("status") == "dead_letter", f"dead-letter status invalid: {dead_lettered}")
    require(
        dead_lettered.get("deliveryRun") == dead_letter_failed.get("deliveryRun"),
        f"dead-letter should not increment deliveryRun: before={dead_letter_failed} after={dead_lettered}",
    )
    require(dead_lettered.get("lastManualAction") == "dead_letter", f"manual action invalid: {dead_lettered}")
    require(dead_lettered.get("lastManualActionBy") == "smoke-operator", f"manual actor invalid: {dead_lettered}")
    require(
        dead_lettered.get("resolutionNote") == "vendor outage needs manual follow-up",
        f"resolution note invalid: {dead_lettered}",
    )
    require(isinstance(dead_lettered.get("lastManualActionAt"), str), f"manual action time invalid: {dead_lettered}")
    require(dead_lettered.get("failureType") == "http_error", f"dead-letter should preserve failureType: {dead_lettered}")
    dead_letter_items = list_notifications(base_url, status="dead_letter")
    require(any(item.get("id") == dead_letter_id for item in dead_letter_items), f"list should include dead-letter: {dead_letter_items}")
    dead_letter_health = require_health_schema(base_url, require_lease_fields=require_lease_health)
    require(
        dead_letter_health["queue"]["counts"].get("dead_letter", 0) >= 1,
        f"health should count dead-letter tasks: {dead_letter_health}",
    )
    dead_letter_stats = require_stats_schema(base_url, require_lease_fields=require_lease_health)
    require(
        dead_letter_stats["queue"]["counts"].get("dead_letter", 0) >= 1,
        f"stats should count dead-letter tasks: {dead_letter_stats}",
    )
    if isolated_queue:
        default_retry_result = retry_notifications_batch(base_url, {"limit": 1})
        default_retry_ids = {item.get("id") for item in default_retry_result["items"]}
        require(dead_letter_id not in default_retry_ids, f"default batch retry should not include dead-letter: {default_retry_result}")
        still_dead_letter = get_notification(base_url, dead_letter_id)
        require(still_dead_letter.get("status") == "dead_letter", f"default batch retry should not move dead-letter: {still_dead_letter}")
    else:
        print("skip default batch retry exclusion: --base-url may point at a shared local database")
    retried_dead_letter = retry_notification(
        base_url,
        dead_letter_id,
        {"handledBy": "smoke-retry", "note": "retry after manual vendor check"},
    )
    require(retried_dead_letter.get("status") == "queued", f"single retry should requeue dead-letter: {retried_dead_letter}")
    require(retried_dead_letter.get("attemptCount") == 0, f"single retry should reset attemptCount: {retried_dead_letter}")
    require(
        retried_dead_letter.get("deliveryRun") == dead_letter_failed.get("deliveryRun") + 1,
        f"single retry should increment deliveryRun: before={dead_letter_failed} after={retried_dead_letter}",
    )
    require(retried_dead_letter.get("failureType") is None, f"single retry should clear failureType: {retried_dead_letter}")
    require(retried_dead_letter.get("lastError") is None, f"single retry should clear lastError: {retried_dead_letter}")
    require(retried_dead_letter.get("lastManualAction") == "retry", f"single retry manual action invalid: {retried_dead_letter}")
    require(retried_dead_letter.get("lastManualActionBy") == "smoke-retry", f"single retry actor invalid: {retried_dead_letter}")
    require(
        retried_dead_letter.get("resolutionNote") == "retry after manual vendor check",
        f"single retry note invalid: {retried_dead_letter}",
    )
    retried_dead_letter_failed = wait_for_status(base_url, dead_letter_id, "failed", timeout=short_wait)
    dead_letter_attempts_after = get_attempts(base_url, dead_letter_id)
    require(
        len(dead_letter_attempts_after) > len(dead_letter_attempts_before),
        f"single retry should preserve and append attempts: before={dead_letter_attempts_before} after={dead_letter_attempts_after}",
    )
    require(
        retried_dead_letter_failed.get("lastManualAction") == "retry",
        f"retry manual audit should remain after delivery failure: {retried_dead_letter_failed}",
    )
    print(f"ok dead-letter mark, default exclusion, and single retry audit: {dead_letter_id}")

    if isolated_queue:
        batch_dead_letter_id = create_notification(
            base_url,
            {
                "requestId": f"smoke-batch-dead-letter-{suffix}",
                "targetUrl": f"{base_url}/mock/vendor/smoke-batch-dead-letter?fail=1",
                "method": "POST",
                "body": {"case": "batch-dead-letter"},
                "maxAttempts": 1,
            },
        )
        batch_dead_letter_failed = wait_for_status(base_url, batch_dead_letter_id, "failed", timeout=short_wait)
        dead_letter_notification(
            base_url,
            batch_dead_letter_id,
            {"handledBy": "smoke-operator", "note": "batch dead-letter fixture"},
        )
        explicit_dead_letter_batch = retry_notifications_batch(
            base_url,
            {
                "status": "dead_letter",
                "limit": 50,
                "actionBy": "smoke-batch",
                "resolutionNote": "batch retry dead-letter",
            },
        )
        explicit_dead_letter_items = {item.get("id"): item for item in explicit_dead_letter_batch["items"]}
        require(
            batch_dead_letter_id in explicit_dead_letter_items,
            f"explicit dead-letter batch retry should include target: {explicit_dead_letter_batch}",
        )
        explicit_dead_letter_item = explicit_dead_letter_items[batch_dead_letter_id]
        require(explicit_dead_letter_item.get("status") == "queued", f"explicit dead-letter batch should requeue: {explicit_dead_letter_item}")
        require(
            explicit_dead_letter_item.get("deliveryRun") == batch_dead_letter_failed.get("deliveryRun") + 1,
            f"explicit dead-letter batch should increment deliveryRun: before={batch_dead_letter_failed} after={explicit_dead_letter_item}",
        )
        require(explicit_dead_letter_item.get("lastManualAction") == "retry", f"explicit batch manual action invalid: {explicit_dead_letter_item}")
        require(explicit_dead_letter_item.get("lastManualActionBy") == "smoke-batch", f"explicit batch actor invalid: {explicit_dead_letter_item}")
        print(f"ok explicit batch retry can restore dead-letter notification: {batch_dead_letter_id}")
    else:
        print("skip explicit dead-letter batch retry: --base-url may point at a shared local database")

    filter_id = create_notification(
        base_url,
        {
            "requestId": f"smoke-filter-{suffix}",
            "eventType": f"crm.contact.updated.{suffix}",
            "sourceSystem": f"smoke-crm-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-filter-target-{suffix}",
            "method": "POST",
            "body": {"case": "filter"},
            "maxAttempts": 1,
        },
    )
    wait_for_status(base_url, filter_id, "succeeded", timeout=short_wait)
    event_items = list_notifications(base_url, eventType=f"contact.updated.{suffix}")
    source_items = list_notifications(base_url, sourceSystem=f"crm-{suffix}")
    target_items = list_notifications(base_url, targetUrl=f"smoke-filter-target-{suffix}")
    require(any(item.get("id") == filter_id for item in event_items), f"eventType filter missed target: {event_items}")
    require(any(item.get("id") == filter_id for item in source_items), f"sourceSystem filter missed target: {source_items}")
    require(any(item.get("id") == filter_id for item in target_items), f"targetUrl filter missed target: {target_items}")
    print(f"ok list filters matched notification: {filter_id}")

    time_event_type = f"smoke.time_range.{suffix}"
    older_time_id = create_notification(
        base_url,
        {
            "requestId": f"smoke-time-range-old-{suffix}",
            "eventType": time_event_type,
            "sourceSystem": f"smoke-time-range-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-time-range-old-{suffix}",
            "method": "POST",
            "body": {"case": "time-range", "position": "old"},
            "maxAttempts": 1,
        },
    )
    older_time_item = wait_for_status(base_url, older_time_id, "succeeded", timeout=short_wait)
    time.sleep(1.1)
    newer_time_id = create_notification(
        base_url,
        {
            "requestId": f"smoke-time-range-new-{suffix}",
            "eventType": time_event_type,
            "sourceSystem": f"smoke-time-range-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-time-range-new-{suffix}",
            "method": "POST",
            "body": {"case": "time-range", "position": "new"},
            "maxAttempts": 1,
        },
    )
    newer_time_item = wait_for_status(base_url, newer_time_id, "succeeded", timeout=short_wait)

    created_from_items = list_notifications(
        base_url,
        eventType=time_event_type,
        createdFrom=newer_time_item.get("createdAt"),
        sort="createdAt",
        order="asc",
    )
    require(
        [item.get("id") for item in created_from_items] == [newer_time_id],
        f"createdFrom should include newer notification only: {created_from_items}",
    )
    created_to_items = list_notifications(
        base_url,
        eventType=time_event_type,
        createdTo=older_time_item.get("createdAt"),
        sort="createdAt",
        order="asc",
    )
    require(
        [item.get("id") for item in created_to_items] == [older_time_id],
        f"createdTo should include older notification only: {created_to_items}",
    )
    updated_from_items = list_notifications(
        base_url,
        eventType=time_event_type,
        updatedFrom=newer_time_item.get("updatedAt"),
        sort="updatedAt",
        order="asc",
    )
    require(
        [item.get("id") for item in updated_from_items] == [newer_time_id],
        f"updatedFrom should include newer notification only: {updated_from_items}",
    )
    updated_to_items = list_notifications(
        base_url,
        eventType=time_event_type,
        updatedTo=older_time_item.get("updatedAt"),
        sort="updatedAt",
        order="asc",
    )
    require(
        [item.get("id") for item in updated_to_items] == [older_time_id],
        f"updatedTo should include older notification only: {updated_to_items}",
    )
    ranged_csv_text = export_notifications_csv(
        base_url,
        eventType=time_event_type,
        createdFrom=newer_time_item.get("createdAt"),
        sort="createdAt",
        order="asc",
    )
    ranged_csv_rows = list(csv.DictReader(io.StringIO(ranged_csv_text)))
    require(
        [row["id"] for row in ranged_csv_rows] == [newer_time_id],
        f"CSV createdFrom should include newer notification only: {ranged_csv_rows}",
    )
    bad_time_status, bad_time_body = request_json(base_url, "GET", "/api/notifications?createdFrom=not-a-time")
    require(bad_time_status == 400, f"invalid list time should return 400: {bad_time_status} {bad_time_body}")
    bad_csv_status, _, bad_csv_text = request_text(base_url, "GET", "/api/notifications/export.csv?updatedTo=not-a-time")
    require(bad_csv_status == 400, f"invalid CSV time should return 400: {bad_csv_status} {bad_csv_text}")
    print("ok list and CSV time range filters matched expected notifications")

    page_ids = []
    for index in range(3):
        page_id = create_notification(
            base_url,
            {
                "requestId": f"smoke-page-{suffix}-{index}",
                "eventType": f"smoke.pagination.{suffix}",
                "sourceSystem": f"smoke-page-{suffix}",
                "targetUrl": f"{base_url}/mock/vendor/smoke-page-{suffix}-{index}",
                "method": "POST",
                "body": {"case": "pagination", "index": index},
                "maxAttempts": 1,
            },
        )
        page_ids.append(page_id)
        time.sleep(0.02)
    for page_id in page_ids:
        wait_for_status(base_url, page_id, "succeeded", timeout=short_wait)

    first_page = list_notifications_response(
        base_url,
        eventType=f"smoke.pagination.{suffix}",
        limit=2,
        offset=0,
        sort="createdAt",
        order="asc",
    )
    first_page_ids = [item.get("id") for item in first_page["items"]]
    require(first_page_ids == page_ids[:2], f"createdAt asc first page invalid: {first_page}")
    require(
        first_page["pagination"] == {
            "limit": 2,
            "offset": 0,
            "count": 2,
            "hasMore": True,
            "sort": "createdAt",
            "order": "asc",
        },
        f"first page pagination invalid: {first_page}",
    )
    second_page = list_notifications_response(
        base_url,
        eventType=f"smoke.pagination.{suffix}",
        limit=2,
        offset=2,
        sort="createdAt",
        order="asc",
    )
    require([item.get("id") for item in second_page["items"]] == page_ids[2:], f"second page invalid: {second_page}")
    require(second_page["pagination"].get("hasMore") is False, f"second page should not have more: {second_page}")
    desc_page = list_notifications_response(
        base_url,
        eventType=f"smoke.pagination.{suffix}",
        limit=3,
        sort="createdAt",
        order="desc",
    )
    require([item.get("id") for item in desc_page["items"]] == list(reversed(page_ids)), f"createdAt desc invalid: {desc_page}")

    bad_sort_status, bad_sort_body = request_json(base_url, "GET", "/api/notifications?sort=created_at")
    require(bad_sort_status == 400, f"invalid sort should return 400: {bad_sort_status} {bad_sort_body}")
    bad_order_status, bad_order_body = request_json(base_url, "GET", "/api/notifications?order=sideways")
    require(bad_order_status == 400, f"invalid order should return 400: {bad_order_status} {bad_order_body}")
    print("ok list pagination and safe sort/order")

    csv_text = export_notifications_csv(
        base_url,
        eventType=f"smoke.pagination.{suffix}",
        limit=10,
        sort="createdAt",
        order="asc",
    )
    csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
    csv_fields = set(csv_rows[0].keys()) if csv_rows else set()
    expected_csv_fields = {
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
    }
    require(csv_fields == expected_csv_fields, f"CSV fields invalid: {csv_fields}")
    require([row["id"] for row in csv_rows] == page_ids, f"CSV export order/filter invalid: {csv_rows}")
    require("headers" not in csv_fields and "body" not in csv_fields, f"CSV should not expose sensitive payload fields: {csv_fields}")
    print("ok CSV export returns filtered non-sensitive notification rows")

    if include_timeout:
        legal_timeout_seconds = 0.5
        legal_timeout_id = create_notification(
            base_url,
            {
                "requestId": f"smoke-task-timeout-detail-{suffix}",
                "targetUrl": f"{base_url}/mock/vendor/smoke-task-timeout-detail?delayMs=300",
                "method": "POST",
                "body": {"case": "task-timeout-detail"},
                "maxAttempts": 1,
                "timeoutSeconds": legal_timeout_seconds,
            },
        )
        legal_timeout_detail = get_notification(base_url, legal_timeout_id)
        require_number_close(
            legal_timeout_detail.get("timeoutSeconds"),
            legal_timeout_seconds,
            f"detail should expose timeoutSeconds: {legal_timeout_detail}",
        )
        legal_timeout_success = wait_for_status(base_url, legal_timeout_id, "succeeded", timeout=short_wait)
        require_number_close(
            legal_timeout_success.get("timeoutSeconds"),
            legal_timeout_seconds,
            f"succeeded detail should preserve timeoutSeconds: {legal_timeout_success}",
        )
        print(f"ok task timeoutSeconds appears in detail and overrides global timeout: {legal_timeout_id}")

        short_task_timeout_seconds = MIN_DELIVERY_TIMEOUT_SECONDS
        task_timeout_id = create_notification(
            base_url,
            {
                "requestId": f"smoke-task-timeout-{suffix}",
                "targetUrl": f"{base_url}/mock/vendor/smoke-task-timeout?delayMs=300",
                "method": "POST",
                "body": {"case": "task-timeout"},
                "maxAttempts": 1,
                "timeoutSeconds": short_task_timeout_seconds,
            },
        )
        task_timeout_failure = wait_for_status(base_url, task_timeout_id, "failed", timeout=short_wait)
        task_timeout_attempts = get_attempts(base_url, task_timeout_id)
        require_number_close(
            task_timeout_failure.get("timeoutSeconds"),
            short_task_timeout_seconds,
            f"task timeout failure should preserve timeoutSeconds: {task_timeout_failure}",
        )
        require(task_timeout_failure.get("failureType") == "timeout", f"task timeout failureType should be timeout: {task_timeout_failure}")
        require(task_timeout_failure.get("lastStatusCode") is None, f"task timeout should not have status code: {task_timeout_failure}")
        task_timeout_attempt = require_last_attempt_error_type(task_timeout_attempts, "timeout")
        require(task_timeout_attempt.get("statusCode") is None, f"task timeout attempt should not have status code: {task_timeout_attempts}")
        print(f"ok delayed vendor obeyed short task timeoutSeconds: {task_timeout_id}")

        timeout_id = create_notification(
            base_url,
            {
                "requestId": f"smoke-timeout-{suffix}",
                "targetUrl": f"{base_url}/mock/vendor/smoke-timeout?delayMs=800",
                "method": "POST",
                "body": {"case": "timeout"},
                "maxAttempts": 1,
            },
        )
        timeout_failure = wait_for_status(base_url, timeout_id, "failed", timeout=short_wait)
        timeout_attempts = get_attempts(base_url, timeout_id)
        require(timeout_failure.get("timeoutSeconds") is None, f"global timeout task should not store timeoutSeconds: {timeout_failure}")
        require(timeout_failure.get("failureType") == "timeout", f"timeout failureType should be timeout: {timeout_failure}")
        require(timeout_failure.get("lastStatusCode") is None, f"timeout should not have status code: {timeout_failure}")
        timeout_attempt = require_last_attempt_error_type(timeout_attempts, "timeout")
        require(timeout_attempt.get("statusCode") is None, f"timeout attempt should not have status code: {timeout_attempts}")
        print(f"ok unset timeoutSeconds falls back to global delivery timeout: {timeout_id}")

        low_clamp_id = create_notification(
            base_url,
            {
                "requestId": f"smoke-timeout-clamp-low-{suffix}",
                "targetUrl": f"{base_url}/mock/vendor/smoke-timeout-clamp-low",
                "method": "POST",
                "body": {"case": "timeout-clamp-low"},
                "maxAttempts": 1,
                "timeoutSeconds": 0.01,
            },
        )
        high_clamp_id = create_notification(
            base_url,
            {
                "requestId": f"smoke-timeout-clamp-high-{suffix}",
                "targetUrl": f"{base_url}/mock/vendor/smoke-timeout-clamp-high",
                "method": "POST",
                "body": {"case": "timeout-clamp-high"},
                "maxAttempts": 1,
                "timeoutSeconds": 600,
            },
        )
        low_clamp = wait_for_status(base_url, low_clamp_id, "succeeded", timeout=short_wait)
        high_clamp = wait_for_status(base_url, high_clamp_id, "succeeded", timeout=short_wait)
        require_number_close(
            low_clamp.get("timeoutSeconds"),
            MIN_DELIVERY_TIMEOUT_SECONDS,
            f"too-small timeoutSeconds should clamp to min: {low_clamp}",
        )
        require_number_close(
            high_clamp.get("timeoutSeconds"),
            MAX_DELIVERY_TIMEOUT_SECONDS,
            f"too-large timeoutSeconds should clamp to max: {high_clamp}",
        )
        print("ok timeoutSeconds bounds clamp like maxAttempts")

        invalid_timeout_status, invalid_timeout_body = post_notification(
            base_url,
            {
                "requestId": f"smoke-timeout-invalid-{suffix}",
                "targetUrl": f"{base_url}/mock/vendor/smoke-timeout-invalid",
                "method": "POST",
                "body": {"case": "timeout-invalid"},
                "maxAttempts": 1,
                "timeoutSeconds": "soon",
            },
        )
        require(
            invalid_timeout_status == 400,
            f"invalid timeoutSeconds should return 400: {invalid_timeout_status} {invalid_timeout_body}",
        )
        require(
            "timeoutSeconds" in (invalid_timeout_body or {}).get("error", ""),
            f"invalid timeoutSeconds error should name the field: {invalid_timeout_body}",
        )
        print("ok invalid timeoutSeconds rejected with 400")
    else:
        print("skip timeout classification: existing service timeout is not controlled by smoke test")

    final_stats = require_stats_schema(base_url, require_lease_fields=require_lease_health)
    require(final_stats["notifications"]["total"] >= 1, f"stats should count notifications: {final_stats}")
    require(final_stats["attempts"]["total"] >= 1, f"stats should count attempts: {final_stats}")
    require(final_stats["attempts"]["recentErrorCount"] >= 1, f"stats should count recent errors: {final_stats}")
    require(
        final_stats["attempts"]["recentErrorsByType"].get("invalid_target", 0) >= 1,
        f"stats should include invalid_target recent errors: {final_stats}",
    )
    print("ok stats API counts recent delivery errors")


def insert_delivering_fixture(db_path):
    notification_id = "smoke-restart-recovery-delivering"
    timestamp = time.time() - 60
    with sqlite3.connect(db_path) as conn:
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
                next_attempt_at REAL,
                last_error TEXT,
                failure_type TEXT,
                last_status_code INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                delivered_at REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO notifications (
                id, request_id, event_type, source_system, target_url, method,
                headers_json, body, status, attempt_count, delivery_run, max_attempts,
                next_attempt_at, last_error, failure_type, last_status_code, created_at, updated_at, delivered_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification_id,
                "smoke-restart-recovery-delivering",
                "smoke.restart.recovery",
                "smoke",
                "http://127.0.0.1/mock/vendor/recovered",
                "POST",
                "{}",
                "{}",
                "delivering",
                1,
                1,
                1,
                timestamp,
                "delivery was interrupted",
                None,
                None,
                timestamp,
                timestamp,
                None,
            ),
        )
    return notification_id


def insert_expired_delivering_fixture(db_path, base_url):
    suffix = time.time_ns()
    notification_id = f"smoke-lease-recovery-{suffix}"
    request_id = f"smoke-lease-recovery-{suffix}"
    timestamp = time.time() - 5
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO notifications (
                id, request_id, event_type, source_system, target_url, method,
                headers_json, body, status, attempt_count, delivery_run, max_attempts,
                next_attempt_at, last_error, failure_type, last_status_code, created_at, updated_at, delivered_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification_id,
                request_id,
                "smoke.lease.recovery",
                "smoke",
                f"{base_url}/mock/vendor/smoke-lease-recovery",
                "POST",
                "{}",
                json.dumps({"case": "lease-recovery"}, separators=(",", ":")),
                "delivering",
                1,
                1,
                2,
                timestamp,
                "delivery appears stuck",
                None,
                None,
                timestamp,
                timestamp,
                None,
            ),
        )
    return notification_id


def insert_active_delivering_fixture(db_path, base_url):
    suffix = time.time_ns()
    notification_id = f"smoke-dead-letter-delivering-{suffix}"
    request_id = f"smoke-dead-letter-delivering-{suffix}"
    timestamp = time.time()
    lease_safe_updated_at = timestamp + 600
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO notifications (
                id, request_id, event_type, source_system, target_url, method,
                headers_json, body, status, attempt_count, delivery_run, max_attempts,
                next_attempt_at, last_error, failure_type, last_status_code, created_at, updated_at, delivered_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification_id,
                request_id,
                "smoke.dead_letter.delivering",
                "smoke",
                f"{base_url}/mock/vendor/smoke-dead-letter-delivering",
                "POST",
                "{}",
                json.dumps({"case": "dead-letter-delivering"}, separators=(",", ":")),
                "delivering",
                1,
                1,
                3,
                None,
                "delivery is still in progress",
                None,
                None,
                timestamp,
                lease_safe_updated_at,
                None,
            ),
        )
    return notification_id


def require_restart_recovery(base_url, notification_id):
    item = get_notification(base_url, notification_id)
    require(item.get("status") == "queued", f"delivering notification should recover to queued: {item}")
    require(
        "restarted" in (item.get("lastError") or "").lower(),
        f"recovered notification should mention restart in lastError: {item}",
    )
    health = require_health_schema(base_url)
    require(
        health["queue"]["counts"].get("queued", 0) >= 1,
        f"health queued count should include recovered task: {health}",
    )
    print(f"ok restart recovery requeued delivering notification: {notification_id}")


def require_lease_recovery(base_url, notification_id):
    recovered = wait_for_status(base_url, notification_id, "succeeded", timeout=10)
    require(recovered.get("attemptCount") == 2, f"lease-recovered notification should use second attempt: {recovered}")
    attempts = get_attempts(base_url, notification_id)
    require(len(attempts) >= 2, f"lease recovery should record lease and success attempts: {attempts}")
    lease_attempt = attempts[-2]
    success_attempt = attempts[-1]
    require(lease_attempt.get("status") == "failed", f"lease attempt should be failed: {attempts}")
    require(lease_attempt.get("errorType") == "lease_timeout", f"lease attempt should be lease_timeout: {attempts}")
    require(success_attempt.get("status") == "succeeded", f"recovered attempt should succeed: {attempts}")
    require(success_attempt.get("attemptNumber") == 2, f"recovered delivery should be attempt 2: {attempts}")
    health = require_health_schema(base_url)
    require(
        health["worker"].get("lastLeaseRecoveryCount", 0) >= 1,
        f"health worker should expose lease recovery count: {health}",
    )
    require(
        isinstance(health["queue"].get("expiredDeliveringCount"), int),
        f"health queue should expose expiredDeliveringCount: {health}",
    )
    print(f"ok delivering lease recovery requeued and delivered notification: {notification_id}")


def require_dead_letter_rejects_delivering(base_url, notification_id):
    body = dead_letter_notification(
        base_url,
        notification_id,
        {"handledBy": "smoke-operator", "note": "should not be allowed while delivering"},
        expected_status=409,
    )
    require("dead_letter" in str(body), f"delivering dead-letter rejection should mention dead_letter: {body}")
    item = get_notification(base_url, notification_id)
    require(item.get("status") == "delivering", f"delivering task should remain delivering after rejection: {item}")
    print(f"ok delivering notification cannot be marked dead-letter: {notification_id}")


def start_server(extra_env=None, include_recovery_fixture=True):
    port = find_free_port()
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "notifications-smoke.db"
    recovery_notification_id = insert_delivering_fixture(db_path) if include_recovery_fixture else None
    env = os.environ.copy()
    env["NOTIFICATION_DB_PATH"] = str(db_path)
    env["NOTIFICATION_DELIVERY_TIMEOUT_SECONDS"] = "0.2"
    env["NOTIFICATION_DELIVERING_LEASE_SECONDS"] = "0.5"
    env["NOTIFICATION_WORKER_CONCURRENCY"] = str(SMOKE_WORKER_CONCURRENCY)
    env["NOTIFICATION_API_KEYS"] = ""
    if extra_env:
        env.update(extra_env)
    process = subprocess.Popen(
        [sys.executable, str(SERVER_PATH), "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, temp_dir, db_path, f"http://127.0.0.1:{port}", recovery_notification_id


def run_api_key_auth_smoke():
    process = None
    temp_dir = None
    try:
        process, temp_dir, _, base_url, _ = start_server(
            extra_env={"NOTIFICATION_API_KEYS": "smoke-key"},
            include_recovery_fixture=False,
        )
        wait_for_service(base_url)

        options_status, options_headers, _ = request_text(base_url, "OPTIONS", "/api/notifications")
        require(options_status == 204, f"auth service OPTIONS should remain open: {options_status}")
        allow_headers = options_headers.get("Access-Control-Allow-Headers", "")
        require(
            "X-Notification-Api-Key" in allow_headers,
            f"CORS should allow notification API key header: {allow_headers}",
        )

        suffix = time.time_ns()
        payload = {
            "requestId": f"smoke-auth-{suffix}",
            "targetUrl": f"{base_url}/mock/vendor/smoke-auth-{suffix}",
            "method": "POST",
            "body": {"case": "auth"},
            "maxAttempts": 1,
        }

        no_key_status, no_key_body = post_notification(base_url, payload)
        require(no_key_status == 401, f"create without API key should return 401: {no_key_status} {no_key_body}")
        require(no_key_body.get("error") == "unauthorized", f"401 response should be stable: {no_key_body}")

        wrong_key_status, wrong_key_body = post_notification(
            base_url,
            payload,
            headers={"X-Notification-Api-Key": "wrong-key"},
        )
        require(
            wrong_key_status == 401,
            f"create with wrong API key should return 401: {wrong_key_status} {wrong_key_body}",
        )
        wrong_key_text = json.dumps(wrong_key_body, ensure_ascii=False)
        require("wrong-key" not in wrong_key_text, f"401 response should not echo supplied API key: {wrong_key_body}")
        require("smoke-key" not in wrong_key_text, f"401 response should not expose configured API key: {wrong_key_body}")

        unauthorized_items = list_notifications(base_url)
        require(
            all(item.get("requestId") != payload["requestId"] for item in unauthorized_items),
            f"unauthorized creates should not insert notifications: {unauthorized_items}",
        )

        notification_id = create_notification(
            base_url,
            payload,
            headers={"X-Notification-Api-Key": "smoke-key"},
        )
        created = wait_for_status(base_url, notification_id, "succeeded", timeout=10)
        retried = retry_notification(
            base_url,
            notification_id,
            {"handledBy": "smoke-auth", "note": "authorized retry"},
            headers={"Authorization": "Bearer smoke-key"},
        )
        require(retried.get("status") == "queued", f"authorized retry should requeue notification: {retried}")
        require(
            retried.get("deliveryRun") == created.get("deliveryRun") + 1,
            f"authorized retry should increment deliveryRun: before={created} after={retried}",
        )
        wait_for_status(base_url, notification_id, "succeeded", timeout=10)
        print(f"ok API key auth protects writes and accepts both key headers: {notification_id}")
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if temp_dir is not None:
            temp_dir.cleanup()


def main():
    parser = argparse.ArgumentParser(description="Smoke test the local notification delivery service")
    parser.add_argument("--base-url", help="Use an already running service, for example http://127.0.0.1:8000")
    parser.add_argument(
        "--include-timeout",
        action="store_true",
        help="Also run timeout classification against --base-url; the service should use a short delivery timeout",
    )
    args = parser.parse_args()

    process = None
    temp_dir = None
    base_url = args.base_url.rstrip("/") if args.base_url else None
    try:
        if base_url is None:
            process, temp_dir, db_path, base_url, recovery_notification_id = start_server()
        wait_for_service(base_url)
        if process is not None:
            require_restart_recovery(base_url, recovery_notification_id)
            lease_notification_id = insert_expired_delivering_fixture(db_path, base_url)
            require_lease_recovery(base_url, lease_notification_id)
            delivering_dead_letter_id = insert_active_delivering_fixture(db_path, base_url)
            require_dead_letter_rejects_delivering(base_url, delivering_dead_letter_id)
        run_smoke(
            base_url,
            include_timeout=(process is not None or args.include_timeout),
            require_lease_health=(process is not None),
            require_idempotency_fields=(process is not None),
            isolated_queue=(process is not None),
            expected_worker_concurrency=SMOKE_WORKER_CONCURRENCY if process is not None else None,
        )
        print("ok attempts API returned delivery logs")
        if process is not None:
            run_api_key_auth_smoke()
    except SmokeError as exc:
        print(f"smoke test failed: {exc}", file=sys.stderr)
        if process and process.poll() is not None and process.stdout:
            output = process.stdout.read()
            if output:
                print(output, file=sys.stderr)
        return 1
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if temp_dir is not None:
            temp_dir.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
