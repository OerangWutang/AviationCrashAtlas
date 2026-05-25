"""Tests for API key hashing in security.py."""

import hashlib
import hmac

from atlas.config import get_settings


def _set_db_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/atlas")
    monkeypatch.setenv("DATABASE_SYNC_URL", "postgresql://u:p@localhost/atlas")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "atlas")
    # Required by _validate_production_db_roles so tests that set
    # ENVIRONMENT=production reach the check they are actually testing.
    monkeypatch.setenv("TENANT_DATABASE_URL", "postgresql+asyncpg://atlas_app:p@localhost/atlas")
    monkeypatch.setenv("SYSTEM_DATABASE_URL", "postgresql+asyncpg://atlas_system:p@localhost/atlas")
    # Suppress HSTS / CORS UserWarnings so production-env tests only emit the
    # warning they are actually testing, not unrelated config noise.
    monkeypatch.setenv("HSTS_ENABLED", "true")
    monkeypatch.setenv("CORS_ORIGINS", "https://example.com")


def test_hash_api_key_without_secret_uses_sha256(monkeypatch):
    _set_db_env(monkeypatch)
    monkeypatch.delenv("API_KEY_HASH_SECRET", raising=False)
    get_settings.cache_clear()

    from atlas.security import hash_api_key

    key = "my-plain-key"
    assert hash_api_key(key) == hashlib.sha256(key.encode()).hexdigest()


def test_hash_api_key_with_secret_uses_hmac(monkeypatch):
    _set_db_env(monkeypatch)
    monkeypatch.setenv(
        "API_KEY_HASH_SECRET", "0000000000000000000000000000000000000000000000000000000000000000"
    )
    get_settings.cache_clear()

    from atlas.security import hash_api_key

    key = "my-plain-key"
    expected = hmac.digest(
        b"0000000000000000000000000000000000000000000000000000000000000000",
        key.encode(),
        hashlib.sha256,
    ).hex()
    assert hash_api_key(key) == expected


def test_hash_api_key_with_and_without_secret_differ(monkeypatch):
    _set_db_env(monkeypatch)
    key = "my-plain-key"

    monkeypatch.delenv("API_KEY_HASH_SECRET", raising=False)
    get_settings.cache_clear()
    from atlas.security import hash_api_key

    plain_hash = hash_api_key(key)

    monkeypatch.setenv("API_KEY_HASH_SECRET", "another-secret")
    get_settings.cache_clear()
    hmac_hash = hash_api_key(key)

    assert plain_hash != hmac_hash


def test_production_requires_explicit_allowed_hosts(monkeypatch):
    _set_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "API_KEY_HASH_SECRET", "0000000000000000000000000000000000000000000000000000000000000000"
    )
    monkeypatch.setenv("API_DOCS_ENABLED", "false")
    monkeypatch.setenv("SECURITY_HEADERS_ENABLED", "true")
    monkeypatch.setenv("PROMETHEUS_ALLOWED_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("ALLOWED_HOSTS", "*")
    get_settings.cache_clear()

    settings = get_settings()
    try:
        settings.warn_if_insecure()
    except RuntimeError as exc:
        assert "ALLOWED_HOSTS" in str(exc)
    else:  # pragma: no cover - explicit assertion style for old pytest versions
        raise AssertionError("production wildcard ALLOWED_HOSTS should fail startup")


def test_production_disables_docs_by_default(monkeypatch):
    _set_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "API_KEY_HASH_SECRET", "0000000000000000000000000000000000000000000000000000000000000000"
    )
    monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setenv("SECURITY_HEADERS_ENABLED", "true")
    monkeypatch.setenv("PROMETHEUS_ALLOWED_CIDRS", "127.0.0.1/32")
    monkeypatch.delenv("API_DOCS_ENABLED", raising=False)
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.effective_api_docs_enabled is False


def test_production_rejects_http_cors_origins(monkeypatch):
    _set_db_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "API_KEY_HASH_SECRET", "0000000000000000000000000000000000000000000000000000000000000000"
    )
    monkeypatch.setenv("ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setenv("API_DOCS_ENABLED", "false")
    monkeypatch.setenv("SECURITY_HEADERS_ENABLED", "true")
    monkeypatch.setenv("PROMETHEUS_ALLOWED_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("CORS_ORIGINS", "http://evil.example.com")
    get_settings.cache_clear()

    settings = get_settings()
    try:
        settings.warn_if_insecure()
    except RuntimeError as exc:
        assert "CORS" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("production HTTP CORS origin should fail startup")
