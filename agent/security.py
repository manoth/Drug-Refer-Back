from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path
from typing import Dict, Iterable, List

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken


PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


class SecurityError(RuntimeError):
    pass


class CredentialCipher:
    def __init__(self, key_file: Path):
        self.key_file = key_file
        self.key = self._load_or_create_key()
        self.fernet = Fernet(self.key)

    def _load_or_create_key(self) -> bytes:
        from_environment = os.getenv("AGENT_MASTER_KEY")
        if from_environment:
            key = from_environment.encode("ascii")
            try:
                Fernet(key)
            except (ValueError, TypeError) as exc:
                raise SecurityError("AGENT_MASTER_KEY is not a valid Fernet key") from exc
            return key

        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        if self.key_file.exists():
            key = self.key_file.read_bytes().strip()
            try:
                Fernet(key)
            except (ValueError, TypeError) as exc:
                raise SecurityError(f"Invalid master key file: {self.key_file}") from exc
            os.chmod(self.key_file, 0o600)
            return key

        key = Fernet.generate_key()
        descriptor = os.open(
            self.key_file,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(key + b"\n")
        return key

    @property
    def signing_secret(self) -> str:
        raw = base64.urlsafe_b64decode(self.key)
        return hashlib.sha256(raw + b"web-session-signing").hexdigest()

    def encrypt(self, value: str) -> str:
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError) as exc:
            raise SecurityError("Stored credential cannot be decrypted") from exc


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_new_password(password: str, username: str) -> List[str]:
    errors: List[str] = []
    if len(password) < 8:
        errors.append("รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร")
    if len(password) > 128:
        errors.append("รหัสผ่านต้องไม่เกิน 128 ตัวอักษร")
    if not re.search(r"[a-z]", password):
        errors.append("ต้องมีอักษรภาษาอังกฤษตัวพิมพ์เล็ก")
    if not re.search(r"[A-Z]", password):
        errors.append("ต้องมีอักษรภาษาอังกฤษตัวพิมพ์ใหญ่")
    if not re.search(r"\d", password):
        errors.append("ต้องมีตัวเลข")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("ต้องมีอักขระพิเศษ")
    lowered = password.casefold()
    forbidden = {
        "admin",
        "admin123",
        "password",
        "password123",
        "12345678",
        username.casefold(),
    }
    if lowered in forbidden or username.casefold() in lowered:
        errors.append("รหัสผ่านต้องไม่เหมือนหรือมีชื่อผู้ใช้")
    return errors


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def verify_csrf(expected: str, supplied: str) -> bool:
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def settings_fingerprint(values: Dict[str, str], fields: Iterable[str]) -> str:
    canonical = "\x1f".join(f"{field}={values.get(field, '')}" for field in fields)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
