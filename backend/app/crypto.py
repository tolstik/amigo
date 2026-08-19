from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    def __init__(self, key: str | bytes | None):
        if not key:
            raise RuntimeError("AMIGO token encryption key is not configured")
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("AMIGO token encryption key is invalid") from exc

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("stored provider credential cannot be decrypted") from exc
