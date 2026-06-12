"""Unit tests for ``atlas.security.mfa``.

Covers the three primitives the application layer relies on:

1. Seed generation produces Base32-decodable 160-bit values, distinct
   across calls.
2. ``encrypt_seed`` + ``decrypt_seed`` round-trip under a configured KEK
   and refuse to work without one.
3. ``verify_totp`` accepts a fresh code from the same secret and rejects
   non-numeric / wrong codes.

These tests are fast, in-memory only, and isolated from the full
``Settings`` machinery via direct ``atlas.config.get_settings.cache_clear``
calls — they do not require a database.
"""

from __future__ import annotations

import base64

import pyotp
import pytest
from cryptography.exceptions import InvalidTag

from atlas.config import get_settings
from atlas.security.mfa import (
    MfaConfigurationError,
    build_otpauth_uri,
    decrypt_seed,
    encrypt_seed,
    generate_seed,
    verify_totp,
)

_VALID_KEK_HEX = "11" * 32  # 32 bytes


@pytest.fixture()
def kek_env(monkeypatch):
    """Configure a valid MFA_KEK and reset the settings cache."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/atlas")
    monkeypatch.setenv("DATABASE_SYNC_URL", "postgresql://u:p@localhost/atlas")
    monkeypatch.setenv("MFA_KEK_HEX", _VALID_KEK_HEX)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def no_kek_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/atlas")
    monkeypatch.setenv("DATABASE_SYNC_URL", "postgresql://u:p@localhost/atlas")
    monkeypatch.delenv("MFA_KEK_HEX", raising=False)
    monkeypatch.delenv("MFA_KEK", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_generate_seed_produces_decodable_base32(kek_env):
    seed = generate_seed()
    # Re-pad to make standard base32 decode happy and check length.
    padded = seed + "=" * ((8 - len(seed) % 8) % 8)
    raw = base64.b32decode(padded)
    assert len(raw) == 20  # 160 bits


def test_generate_seed_produces_distinct_values(kek_env):
    assert generate_seed() != generate_seed()


def test_encrypt_decrypt_round_trip(kek_env):
    seed = generate_seed()
    blob = encrypt_seed(seed)
    assert isinstance(blob, bytes)
    # Nonce (12) + at least 16 bytes ciphertext+tag for a non-empty plaintext.
    assert len(blob) >= 12 + 16
    assert decrypt_seed(blob) == seed


def test_encrypt_uses_fresh_nonce_per_call(kek_env):
    seed = generate_seed()
    a = encrypt_seed(seed)
    b = encrypt_seed(seed)
    # Same plaintext + same key but different nonces must yield different
    # ciphertexts — otherwise we have catastrophically misconfigured AES-GCM.
    assert a != b


def test_decrypt_rejects_tampered_blob(kek_env):
    seed = generate_seed()
    blob = bytearray(encrypt_seed(seed))
    # Flip a byte in the ciphertext region (past the 12-byte nonce).
    blob[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        decrypt_seed(bytes(blob))


def test_decrypt_rejects_blob_too_short(kek_env):
    with pytest.raises(ValueError):
        decrypt_seed(b"abc")


def test_encrypt_without_kek_raises(no_kek_env):
    with pytest.raises(MfaConfigurationError):
        encrypt_seed("ANYSEED")


def test_decrypt_without_kek_raises(no_kek_env):
    with pytest.raises(MfaConfigurationError):
        decrypt_seed(b"\x00" * 30)


def test_verify_totp_accepts_current_code(kek_env):
    seed = generate_seed()
    code = pyotp.TOTP(seed).now()
    assert verify_totp(seed, code) is True


def test_verify_totp_rejects_wrong_code(kek_env):
    seed = generate_seed()
    # A code that almost certainly doesn't match — pyotp uses 6 digits by default.
    assert verify_totp(seed, "000000") is False


@pytest.mark.parametrize("bad", ["", "abc", "12345", "12345A"])
def test_verify_totp_rejects_non_numeric_or_short(kek_env, bad):
    seed = generate_seed()
    assert verify_totp(seed, bad) is False


def test_build_otpauth_uri_includes_issuer(kek_env):
    seed = generate_seed()
    uri = build_otpauth_uri("acct@example.com", seed)
    assert uri.startswith("otpauth://totp/")
    assert "issuer=Atlas" in uri
    # The seed should appear (Base32, unpadded matches pyotp's encoding).
    assert seed in uri
