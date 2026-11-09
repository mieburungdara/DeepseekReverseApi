"""Autentikasi API key untuk gateway.

Banyak agen AI (Open WebUI, LobeChat, Cursor, Continue, dll.) **mewajibkan**
field API key diisi sebelum mau mengecek koneksi atau model. Gateway ini
menyediakan tiga mode:

``open`` (default)
    Tidak ada pemeriksaan sama sekali. Key apa pun diterima, termasuk tidak
    mengirim key. Paling ramah untuk uji coba lokal.

``any``
    Key apa pun diterima **asalkan tidak kosong**. Berguna bila Anda ingin
    agen tetap "merasa" melakukan autentikasi.

``strict``
    Key harus sama persis dengan ``DEEPSEEK_API_KEY``. Gunakan ini bila server
    diekspos ke jaringan (mis. bind ``0.0.0.0``) atau lewat tunnel publik.

Format header yang diterima::

    Authorization: Bearer <key>
    Authorization: <key>
    x-api-key: <key>
    api-key: <key>

Semua variasi ini lazim dipakai SDK/agen yang berbeda.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Optional

#: Nama mode yang valid.
MODES = ("open", "any", "strict")

#: Header yang diperiksa, berurutan.
KEY_HEADERS = ("authorization", "x-api-key", "api-key", "x-goog-api-key")


@dataclass
class AuthResult:
    ok: bool
    mode: str
    key: Optional[str] = None
    #: Pesan untuk pengguna bila gagal.
    error: Optional[str] = None


def current_mode() -> str:
    """Mode aktif dari env ``DEEPSEEK_AUTH_MODE`` (default ``open``)."""
    mode = (os.environ.get("DEEPSEEK_AUTH_MODE") or "open").strip().lower()
    return mode if mode in MODES else "open"


def expected_key() -> Optional[str]:
    """Key yang diharapkan pada mode ``strict``."""
    return os.environ.get("DEEPSEEK_API_KEY") or None


def extract_key(headers: dict[str, str]) -> Optional[str]:
    """Ambil key dari header apa pun yang lazim dipakai."""
    for name in KEY_HEADERS:
        raw = headers.get(name)
        if not raw:
            continue
        value = raw.strip()
        if not value:
            continue
        # "Bearer <key>" (case-insensitive)
        if value.lower().startswith("bearer"):
            value = value[6:].strip()
        if value:
            return value
    return None


def check(headers: dict[str, str]) -> AuthResult:
    """Validasi permintaan berdasarkan mode aktif."""
    mode = current_mode()
    key = extract_key(headers)

    if mode == "open":
        return AuthResult(ok=True, mode=mode, key=key)

    if mode == "any":
        if key:
            return AuthResult(ok=True, mode=mode, key=key)
        return AuthResult(
            ok=False,
            mode=mode,
            error=(
                "API key tidak ditemukan. Mode 'any' mewajibkan header "
                "Authorization: Bearer <key> (isi bebas)."
            ),
        )

    # strict
    expected = expected_key()
    if not expected:
        # Mode strict tanpa key yang diset: jangan mengunci diri sendiri.
        return AuthResult(
            ok=False,
            mode=mode,
            error=(
                "Server berjalan pada mode 'strict' tetapi DEEPSEEK_API_KEY "
                "belum diset. Set variabel itu, atau ubah DEEPSEEK_AUTH_MODE."
            ),
        )
    if key and hmac.compare_digest(key, expected):
        return AuthResult(ok=True, mode=mode, key=key)
    if not key:
        return AuthResult(
            ok=False,
            mode=mode,
            error=(
                "API key tidak ditemukan. Kirim header "
                "'Authorization: Bearer <key>'."
            ),
        )
    return AuthResult(
        ok=False,
        mode=mode,
        error="API key tidak valid.",
    )


def describe() -> str:
    """Ringkasan singkat mode auth untuk log startup."""
    mode = current_mode()
    if mode == "open":
        return "open (key apa pun diterima, termasuk tanpa key)"
    if mode == "any":
        return "any (key apa pun diterima asalkan tidak kosong)"
    if expected_key():
        return "strict (harus sama dengan DEEPSEEK_API_KEY)"
    return "strict (PERINGATAN: DEEPSEEK_API_KEY belum diset)"
