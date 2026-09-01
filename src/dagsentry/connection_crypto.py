"""Authenticated encryption for write-only Managed Connection secrets."""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dagsentry.config import Settings
from dagsentry.domain.connection import ConnectionProvider, ConnectionPurpose

NONCE_BYTES = 12
KEY_BYTES = 32


class ConnectionEncryptionError(ValueError):
    """A connection Secret cannot be safely encrypted or decrypted."""


@dataclass(frozen=True)
class EncryptedConnectionSecret:
    """Database-ready AES-GCM output without its plaintext input."""

    ciphertext: bytes
    nonce: bytes
    key_version: int


class ConnectionSecretCipher:
    """Encrypt Provider secrets with record identity bound as authenticated data."""

    def __init__(self, key: bytes, key_version: int) -> None:
        if len(key) != KEY_BYTES:
            raise ConnectionEncryptionError("connection encryption key must contain 32 bytes")
        if key_version < 1:
            raise ConnectionEncryptionError("connection encryption key version must be positive")
        self._cipher = AESGCM(key)
        self.key_version = key_version

    @classmethod
    def from_settings(cls, settings: Settings) -> ConnectionSecretCipher:
        """Load one versioned Base64 key or fail without exposing its value."""
        if settings.connection_encryption_key is None:
            raise ConnectionEncryptionError("connection encryption key is not configured")
        return cls.from_base64(
            settings.connection_encryption_key.get_secret_value(),
            settings.connection_encryption_key_version,
        )

    @classmethod
    def from_base64(cls, encoded_key: str, key_version: int) -> ConnectionSecretCipher:
        """Decode one operator-supplied Base64 key without retaining its encoded form."""
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ConnectionEncryptionError("connection encryption key is invalid") from error
        return cls(key, key_version)

    def encrypt(
        self,
        secret: dict[str, str],
        *,
        connection_id: UUID,
        environment: str,
        purpose: ConnectionPurpose,
        provider: ConnectionProvider,
    ) -> EncryptedConnectionSecret:
        """Encrypt canonical JSON using a fresh 96-bit nonce."""
        plaintext = json.dumps(
            secret,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = self._cipher.encrypt(
            nonce,
            plaintext,
            _associated_data(connection_id, environment, purpose, provider),
        )
        return EncryptedConnectionSecret(ciphertext, nonce, self.key_version)

    def decrypt(
        self,
        encrypted: EncryptedConnectionSecret,
        *,
        connection_id: UUID,
        environment: str,
        purpose: ConnectionPurpose,
        provider: ConnectionProvider,
    ) -> dict[str, str]:
        """Decrypt and validate a Secret without returning partial data on failure."""
        if encrypted.key_version != self.key_version:
            raise ConnectionEncryptionError("connection encryption key version is unavailable")
        try:
            plaintext = self._cipher.decrypt(
                encrypted.nonce,
                encrypted.ciphertext,
                _associated_data(connection_id, environment, purpose, provider),
            )
            value = json.loads(plaintext)
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ConnectionEncryptionError("connection Secret could not be decrypted") from error
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise ConnectionEncryptionError("connection Secret is invalid")
        return value


def _associated_data(
    connection_id: UUID,
    environment: str,
    purpose: ConnectionPurpose,
    provider: ConnectionProvider,
) -> bytes:
    return (
        f"dagsentry-managed-connection:v1:{connection_id}:{environment}:{purpose.value}:"
        f"{provider.value}"
    ).encode()
