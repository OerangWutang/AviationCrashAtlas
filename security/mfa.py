"""TOTP MFA primitives for the API-key MFA gate (TICKET-009).

This module wraps three things that the application layer needs:

1. **Seed generation** — fresh 160-bit secrets matching RFC-6238 / RFC-4226
   recommendations.  Returned as the Base32 encoding that authenticator
   apps (Google Authenticator, 1Password, etc.) accept directly.

2. **Seed encryption at rest** — ``encrypt_seed`` packs an AES-256-GCM
   nonce + ciphertext + tag into a single bytes value so that the
   ``api_keys.mfa_secret_encrypted`` column is fully self-describing.
   The key-encryption-key (KEK) is taken from settings (``MFA_KEK[_HEX]``);
   ``decrypt_seed`` is the inverse.  An attacker with read access to the
   database alone cannot derive valid TOTP codes — they would also need
   the KEK from the application environment.

3. **Verification** — ``verify_totp`` accepts a ±1 step window by default
   (so a user whose phone clock is one step out still authenticates), and
   uses the constant-time comparison provided by ``pyotp`` so the gate
   does not leak timing about how close a code was to correct.

The threat model is *not* offline-key recovery — the KEK lives in the
same process as the application that performs verification, so an
attacker with full application compromise has both.  The encryption
exists to ensure that a database-only compromise (stolen backup, dropped
read replica, SQL-injection read primitive) does not surface working
TOTP seeds.
"""

from __future__ import annotations

import os
import secrets
from base64 import b32encode
from urllib.parse import quote

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from atlas.config import get_settings

# 12 bytes is the AES-GCM standard nonce length; both libraries (RFC 5116
# and `cryptography`) treat it as the canonical "use this" size.
_NONCE_BYTES = 12
# 160 bits is the RFC-4226 recommendation for HOTP/TOTP seeds.
_SEED_BYTES = 20


class MfaConfigurationError(RuntimeError):
    """Raised when MFA primitives are called without a configured KEK.

    Distinct from ``ValueError`` so callers (CLI, API auth) can produce a
    specific operator-facing message instead of a generic 500.
    """


def _kek_bytes() -> bytes:
    settings = get_settings()
    kek_hex = settings.resolve_mfa_kek_hex()
    if not kek_hex:
        raise MfaConfigurationError(
            "MFA_KEK is not configured.  Set MFA_KEK or MFA_KEK_HEX before "
            "enrolling keys into TOTP MFA."
        )
    try:
        key = bytes.fromhex(kek_hex)
    except ValueError as exc:
        raise MfaConfigurationError("MFA_KEK is not valid hex.") from exc
    if len(key) != 32:
        raise MfaConfigurationError("MFA_KEK must be exactly 32 bytes for AES-256-GCM.")
    return key


def generate_seed() -> str:
    """Return a fresh Base32-encoded 160-bit TOTP seed.

    Base32 because that is the encoding authenticator apps expect in
    ``otpauth://`` URIs and when entered manually.  Strip trailing padding
    so the value is interchangeable with the value pyotp accepts.
    """
    raw = secrets.token_bytes(_SEED_BYTES)
    return b32encode(raw).decode("ascii").rstrip("=")


def encrypt_seed(seed: str) -> bytes:
    """Wrap a TOTP seed under the configured KEK and return ``nonce || ct``.

    The 12-byte nonce is prepended to the ciphertext (which itself carries
    the GCM tag at the end), so the stored column value alone is enough
    to decrypt — no separate nonce column or KDF metadata required.  The
    nonce is fresh per encryption and never reused under the same key.
    """
    aesgcm = AESGCM(_kek_bytes())
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = aesgcm.encrypt(nonce, seed.encode("utf-8"), associated_data=None)
    return nonce + ciphertext


def decrypt_seed(blob: bytes) -> str:
    """Inverse of :func:`encrypt_seed`.

    Raises ``MfaConfigurationError`` if the KEK is missing,
    ``cryptography.exceptions.InvalidTag`` if the blob has been tampered
    with or was encrypted under a different KEK.  The caller is expected
    to map InvalidTag to a generic "MFA verification failed" response so
    the failure mode is indistinguishable from a wrong code.
    """
    if len(blob) <= _NONCE_BYTES:
        raise ValueError("Encrypted MFA seed blob is too short to contain a nonce.")
    nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    aesgcm = AESGCM(_kek_bytes())
    plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data=None)
    return plaintext.decode("utf-8")


def build_otpauth_uri(account: str, secret: str, *, issuer: str | None = None) -> str:
    """Render an ``otpauth://totp/...`` URI for QR-code enrollment.

    The issuer is taken from settings unless explicitly overridden, so the
    authenticator app shows ``Atlas: api-key-name`` rather than just the
    account label.  pyotp's ``provisioning_uri`` already URL-encodes the
    components, so this is a thin wrapper that pins the issuer for us.
    """
    issuer_label = issuer or get_settings().mfa_issuer_name
    return pyotp.TOTP(secret).provisioning_uri(
        name=quote(account, safe=""),
        issuer_name=issuer_label,
    )


def verify_totp(secret: str, code: str, *, window: int = 1) -> bool:
    """Return True iff ``code`` is valid for ``secret`` within ``±window`` steps.

    A non-zero window absorbs small clock skew between the server and the
    user's device without materially widening the attack surface (one
    extra step ≈ 30 s of validity, and the lockout path bounds brute-force
    attempts independently).  ``pyotp.TOTP.verify`` uses ``hmac.compare_digest``
    internally so this is constant-time with respect to the candidate code.
    """
    if not code or not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=window)
