"""密碼雜湊（只用標準函式庫，不必修改 pyproject.toml）。

使用 PBKDF2-HMAC-SHA256：所有 Python 版本的 hashlib 都有。
macOS 內建的 /usr/bin/python3 連結 LibreSSL，沒有 hashlib.scrypt，所以不採用 scrypt。
儲存格式：pbkdf2_sha256$<iterations>$<salt base64>$<hash base64>
"""
import base64
import hashlib
import hmac
import os

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return "%s$%d$%s$%s" % (_ALGO, _ITERATIONS,
                            base64.b64encode(salt).decode("ascii"), base64.b64encode(dk).decode("ascii"))


def verify_password(password: str, stored) -> bool:
    if isinstance(stored, (bytes, bytearray)):
        stored = stored.decode("utf-8")
    try:
        algo, iterations, salt_b64, dk_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64),
                                 int(iterations), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)
