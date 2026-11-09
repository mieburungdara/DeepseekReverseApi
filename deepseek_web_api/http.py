"""Shared helpers: config, device fingerprint headers, HTTP plumbing."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

BASE_URL = "https://chat.deepseek.com"

#: The web app reports these on every API call (module 6594 ``q_``).
APP_VERSION = "2.7.1"
PLATFORM = "web"
#: ``x-client-bundle-id`` for the main chat brand; see ``BUNDLE_IDS``.
DEFAULT_BRAND = "deepseek"

#: ``x-client-bundle-id`` per brand, from module 40093 (``HP``).
BUNDLE_IDS = {
    "deepseek": "deepseek-chat",
}

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Error codes surfaced by the API envelope (module 21824).
ERROR_CODES = {
    0: "OK",
    40002: "MISSING_TOKEN",
    40003: "INVALID_TOKEN",
    40006: "REQUEST_DECRYPT_FAILED",
    40007: "REQUEST_DECRYPT_NOT_ALLOWED",
    40012: "USER_IS_BANNED",
    40029: "IP_ACCESS_RESTRICTED",
    40300: "POW_HEADER_ERROR",
    40301: "INVALID_POW_RESPONSE",
    40303: "CHALLENGE_REQUIRED",
    40304: "CHALLENGE_FAILED",
    50006: "MUTED",
}


class DeepSeekWebError(RuntimeError):
    """Base error for the reverse-engineered client."""

    def __init__(self, message: str, *, code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.code = code
        self.payload = payload


class AuthError(DeepSeekWebError):
    """Login failed or the stored token is no longer valid."""


class ApiError(DeepSeekWebError):
    """The API returned a non-zero ``code``/``biz_code``."""


class NetworkError(DeepSeekWebError):
    """The request never produced a usable HTTP response."""


class ChallengeRequiredError(DeepSeekWebError):
    """DeepSeek meminta verifikasi captcha (40303 CHALLENGE_REQUIRED).

    Ini bukan kesalahan kode: akun/IP perlu menyelesaikan captcha di browser.
    Dipisahkan dari ``NetworkError`` agar bisa ditangani berbeda (mis. berhenti
    mengirim request dan memberi tahu pengguna).
    """


class RateLimitError(DeepSeekWebError):
    """Terlalu banyak pesan (``rate_limit_reached``).

    Server DeepSeek mengirim event ``hint`` berisi
    "Messages too frequent. Try again later." lalu menutup stream tanpa
    jawaban. Dipisahkan agar gateway bisa membalas 429 + ``Retry-After``.
    """


def timezone_offset_seconds() -> int:
    """UTC offset in seconds, matching the browser's ``utcOffset() * 60``."""
    # ``time.timezone`` is seconds west of UTC, i.e. the sign is inverted.
    if time.localtime().tm_isdst and time.daylight:
        return -time.altzone
    return -time.timezone


def client_headers(
    *,
    user_agent: str = DEFAULT_UA,
    brand: str = DEFAULT_BRAND,
    locale: str = "en_US",
    cookies: Optional[str] = None,
) -> dict[str, str]:
    """Headers the web app attaches to *every* API request.

    ``cookies`` opsional: string cookie mentah dari browser (mis. berisi
    ``aws-waf-token=...``). Ini sangat membantu bila IP kena tantangan WAF.
    """
    headers = {
        "user-agent": user_agent,
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": BASE_URL,
        "referer": f"{BASE_URL}/",
        "x-client-bundle-id": BUNDLE_IDS.get(brand, BUNDLE_IDS[DEFAULT_BRAND]),
        "x-client-platform": PLATFORM,
        "x-client-version": APP_VERSION,
        "x-client-locale": locale,
        "x-client-timezone-offset": str(timezone_offset_seconds()),
    }
    if cookies:
        headers["cookie"] = cookies.strip()
    return headers


def unwrap_envelope(payload: Any) -> Any:
    """Validate the ``{code, msg, data:{biz_code, biz_msg, biz_data}}`` envelope.

    The web app treats ``code != 0`` and ``biz_code != 0`` as failures and reads
    the real result from ``data.biz_data``.
    """
    if not isinstance(payload, dict):
        raise ApiError(f"Unexpected response shape: {payload!r}"[:400], payload=payload)

    code = payload.get("code", 0)
    if code != 0:
        name = ERROR_CODES.get(code, "UNKNOWN")
        raise ApiError(
            f"API error {code} ({name}): {payload.get('msg') or 'no message'}",
            code=code,
            payload=payload,
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        return data

    biz_code = data.get("biz_code", 0)
    if biz_code != 0:
        raise ApiError(
            f"API business error {biz_code}: {data.get('biz_msg') or 'no message'}",
            code=biz_code,
            payload=payload,
        )
    return data.get("biz_data")


@dataclass
class TokenState:
    """Persisted auth material."""

    token: str
    user_id: Optional[str] = None
    email: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    #: Opaque blob the server may set for extra challenge flows.
    settings_jwt: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "user_id": self.user_id,
            "email": self.email,
            "created_at": self.created_at,
            "settings_jwt": self.settings_jwt,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenState":
        if not isinstance(data, dict):
            raise ValueError(
                "Isi file token harus objek JSON, contoh: {\"token\": \"...\"}"
            )

        token = data.get("token")
        if not token:
            # Toleransi: nilai localStorage `userToken` di browser berbentuk
            # {"value":"<token>","__version":"0"} - terima itu juga.
            token = data.get("value")
        if not token or not isinstance(token, str):
            raise ValueError(
                "File token tidak berisi token. Format yang benar:\n"
                '  {"token": "TOKEN_ANDA"}\n'
                "atau cukup tempel token mentah (satu baris, tanpa kurung kurawal)."
            )

        return cls(
            token=token.strip().strip('"'),
            user_id=data.get("user_id"),
            email=data.get("email"),
            created_at=float(data.get("created_at", time.time())),
            settings_jwt=data.get("settings_jwt"),
        )

    def save(self, path: str) -> None:
        import os

        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)
        try:  # best effort: keep the token file private on POSIX
            os.chmod(path, 0o600)
        except OSError:
            pass

    @classmethod
    def load(cls, path: str) -> "TokenState":
        """Muat token dari file.

        Menerima tiga bentuk, supaya copy-paste dari mana pun tetap jalan:

        1. JSON lengkap  : ``{"token": "...", "email": "..."}``
        2. JSON DevTools : ``{"value": "...", "__version": "0"}``
        3. Token mentah  : satu baris berisi token saja
        """
        with open(path, encoding="utf-8") as fh:
            raw = fh.read().strip()

        if not raw:
            raise ValueError(f"File token kosong: {path}")

        if raw.startswith("{"):
            try:
                return cls.from_dict(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"File token bukan JSON yang valid ({exc}). "
                    'Format yang benar: {"token": "TOKEN_ANDA"}'
                ) from exc

        # Anggap isinya token mentah.
        return cls(token=raw.strip().strip('"'))


def make_client(**kwargs: Any) -> httpx.AsyncClient:
    """Async HTTP client with browser-ish defaults."""
    kwargs.setdefault("timeout", httpx.Timeout(120.0, connect=20.0))
    kwargs.setdefault("follow_redirects", True)
    return httpx.AsyncClient(**kwargs)
