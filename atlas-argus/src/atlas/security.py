from __future__ import annotations

import hashlib
import hmac

from atlas.config import get_settings


def hash_api_key(api_key: str) -> str:
    """Return the storage hash for an API key.

    If API_KEY_HASH_SECRET is configured, Atlas uses HMAC-SHA256 so a stolen
    database cannot be used to cheaply enumerate API keys offline. When the
    secret is not configured, this falls back to the legacy SHA-256 format for
    local development and existing databases.
    """

    secret = get_settings().api_key_hash_secret
    if secret:
        return hmac.digest(secret.encode(), api_key.encode(), hashlib.sha256).hex()
    return hashlib.sha256(api_key.encode()).hexdigest()
