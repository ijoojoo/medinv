import json
from hashlib import sha256
from typing import Any

from cryptography.fernet import Fernet, MultiFernet


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class TokenCipher:
    def __init__(self, keys: str):
        # First key encrypts; subsequent keys permit a managed key rotation window.
        self.cipher = MultiFernet([Fernet(key.strip().encode()) for key in keys.split(",")])

    def seal(self, value: dict[str, Any]) -> str:
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def open(self, value: str) -> dict[str, Any]:
        return dict(json.loads(self.cipher.decrypt(value.encode())))
