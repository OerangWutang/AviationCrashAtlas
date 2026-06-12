from __future__ import annotations

import pytest

from atlas.config import Settings


def test_null_pool_warning_checks_each_effective_database_url() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://app@pgbouncer/atlas",
        system_database_url="postgresql+asyncpg://system@pgbouncer/atlas",
        tenant_database_url="postgresql+asyncpg://tenant@postgres/atlas",
        public_database_url="postgresql+asyncpg://public@postgres/atlas",
        environment="production",
        api_key_hash_secret="a" * 64,
        # Migration 053 makes the chain HMAC secret mandatory in production.
        audit_chain_secret_hex="a" * 64,
        # Migration 054 makes the MFA KEK mandatory in production.
        mfa_kek_hex="a" * 64,
        db_use_null_pool=True,
    )

    with pytest.warns(UserWarning) as warnings_seen:
        settings.validate_common_runtime_settings()

    messages = [str(w.message) for w in warnings_seen]
    assert any("TENANT_DATABASE_URL" in message for message in messages)
    assert any("PUBLIC_DATABASE_URL" in message for message in messages)


def test_production_rejects_wildcard_allowed_hosts() -> None:
    """Production startup must fail when ALLOWED_HOSTS is set to wildcard."""
    settings = Settings(
        database_url="postgresql+asyncpg://user@localhost/atlas",
        tenant_database_url="postgresql+asyncpg://app@localhost/atlas",
        system_database_url="postgresql+asyncpg::system@localhost/atlas",
        environment="production",
        api_key_hash_secret="a" * 64,
        audit_chain_secret_hex="a" * 64,
        mfa_kek_hex="a" * 64,
        allowed_hosts=["*"],
    )
    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        settings.validate_api_runtime_settings()


def test_production_rejects_empty_allowed_hosts() -> None:
    """Production startup must fail when ALLOWED_HOSTS is unset (empty list)."""
    settings = Settings(
        database_url="postgresql+asyncpg://user@localhost/atlas",
        tenant_database_url="postgresql+asyncpg://app@localhost/atlas",
        system_database_url="postgresql+asyncpg::system@localhost/atlas",
        environment="production",
        api_key_hash_secret="a" * 64,
        audit_chain_secret_hex="a" * 64,
        mfa_kek_hex="a" * 64,
        allowed_hosts=[],
    )
    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        settings.validate_api_runtime_settings()


def test_default_allowed_hosts_accepts_localhost_in_dev() -> None:
    """Default ALLOWED_HOSTS (localhost, 127.0.0.1) must pass in dev mode."""
    settings = Settings(
        database_url="postgresql+asyncpg://user@localhost/atlas",
        environment="development",
        api_key_hash_secret=None,
    )
    # Should not raise — dev mode allows localhost defaults
    settings.validate_api_runtime_settings()
