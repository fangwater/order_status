from __future__ import annotations

import base64
import hashlib
from cryptography.fernet import Fernet

def derive_fernet(master_key: str, salt: bytes) -> Fernet:
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        master_key.encode("utf-8"),
        salt,
        200_000,
        dklen=32,
    )
    return Fernet(base64.urlsafe_b64encode(derived))
