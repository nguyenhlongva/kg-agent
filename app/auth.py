"""Băm mật khẩu, phiên đăng nhập ký HMAC, giới hạn số lần thử."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from . import db
from .config import settings

USER_COOKIE = "kb_user"
ADMIN_COOKIE = "kb_admin"


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 240_000)
    return "pbkdf2$240000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except Exception:
        return False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(kind: str, sub: int, ttl_seconds: int, ver: str = "") -> str:
    payload = _b64(json.dumps({"k": kind, "s": sub, "e": int(time.time()) + ttl_seconds, "v": ver}).encode())
    sig = _b64(hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).digest())
    return payload + "." + sig


def read_token(token: str | None, kind: str) -> dict | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    good = _b64(hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, good):
        return None
    try:
        data = json.loads(_unb64(payload))
    except ValueError:
        return None
    if data.get("k") != kind or data.get("e", 0) < time.time():
        return None
    return data


def pw_version(password_hash: str) -> str:
    """Đổi mật khẩu thì các phiên cũ hết hiệu lực."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:12]


def current_user(request: Request) -> dict:
    data = read_token(request.cookies.get(USER_COOKIE), "user")
    if not data:
        raise HTTPException(401, "Chưa đăng nhập")
    u = db.q1("SELECT * FROM users WHERE id=?", (data["s"],))
    if not u:
        raise HTTPException(401, "Tài khoản không còn tồn tại")
    if u["blocked"]:
        raise HTTPException(403, "Tài khoản đã bị khóa. Vui lòng liên hệ quản trị viên.")
    return u


def current_admin(request: Request) -> dict:
    data = read_token(request.cookies.get(ADMIN_COOKIE), "admin")
    if not data:
        raise HTTPException(401, "Chưa đăng nhập quản trị")
    a = db.q1("SELECT * FROM admins WHERE id=?", (data["s"],))
    if not a or not a["active"] or pw_version(a["password_hash"]) != data.get("v"):
        raise HTTPException(401, "Phiên quản trị đã hết hạn")
    return a


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("+84"):
        digits = "0" + digits[3:]
    elif digits.startswith("84") and len(digits) == 11:
        digits = "0" + digits[2:]
    return digits if re.fullmatch(r"0\d{9}", digits) else None


class RateLimiter:
    def __init__(self, limit: int, window_s: int) -> None:
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        t = time.time()
        dq = self.hits[key]
        while dq and dq[0] < t - self.window:
            dq.popleft()
        if len(dq) >= self.limit:
            raise HTTPException(429, "Thử quá nhiều lần. Vui lòng đợi vài phút.")
        dq.append(t)


login_limiter = RateLimiter(10, 300)
chat_limiter = RateLimiter(20, 60)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")
