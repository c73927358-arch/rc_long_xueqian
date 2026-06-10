import hmac
import os

from .settings import PROTECTED_NOTIFICATION_WRITE_PATHS


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


class ApiKeyAuthenticator:
    """Shared-key authenticator for mutating notification APIs."""

    def is_protected_path(self, path):
        return is_protected_notification_write_path(path)

    def is_authorized(self, headers):
        return has_valid_notification_api_key(headers)

    def require_authorized(self, path, headers):
        return not self.is_protected_path(path) or self.is_authorized(headers)


authenticator = ApiKeyAuthenticator()
