"""Retry otomatis untuk rate limit DeepSeek.

DeepSeek membalas ``rate_limit_reached`` ("Messages too frequent") ketika
pesan dikirim terlalu rapat. Tanpa retry, gateway meneruskan 429 ke klien
padahal menunggu sebentar biasanya sudah cukup.

Dua helper di sini:

* :func:`call_with_retry` — untuk satu giliran non-streaming.
* :func:`iter_with_retry` — untuk generator async (streaming), dengan syarat
  **belum ada item yang dikirim** ke klien. Bila stream sudah mulai mengalir
  lalu putus, mengulang akan menduplikasi teks, jadi error diteruskan.

Hanya error yang sifatnya sementara yang diulang (rate limit, challenge,
40304/40301). Error auth dan request invalid tidak diulang.
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import AsyncIterator, Awaitable, Callable, Optional, TypeVar

from .http import ChallengeRequiredError, DeepSeekWebError, RateLimitError

T = TypeVar("T")

#: Jumlah percobaan tambahan setelah percobaan pertama.
#: ``DEEPSEEK_MAX_RETRIES=0`` mematikan retry (perilaku lama).
def max_retries() -> int:
    try:
        return max(0, int(os.environ.get("DEEPSEEK_MAX_RETRIES", "3")))
    except ValueError:
        return 3


#: Jeda dasar (detik). Percobaan ke-n menunggu ``base * 2**(n-1)``.
def base_delay() -> float:
    try:
        return max(0.0, float(os.environ.get("DEEPSEEK_RETRY_BASE", "1.0")))
    except ValueError:
        return 1.0


#: Batas atas jeda satu percobaan (detik).
def max_delay() -> float:
    try:
        return max(0.0, float(os.environ.get("DEEPSEEK_RETRY_MAX", "16.0")))
    except ValueError:
        return 16.0


def is_retryable(exc: BaseException) -> bool:
    """Apakah error ini layak dicoba ulang?"""
    if isinstance(exc, (RateLimitError, ChallengeRequiredError)):
        return True
    if isinstance(exc, DeepSeekWebError):
        # 40301 INVALID_POW_RESPONSE / 40304 CHALLENGE_FAILED: PoW sekali pakai
        # bisa gagal karena challenge kedaluwarsa, aman diulang.
        return exc.code in (40301, 40304)
    return False


def compute_delay(attempt: int) -> float:
    """Jeda untuk percobaan ke-``attempt`` (0-based), dengan jitter.

    Jitter mencegah beberapa klien mencoba ulang pada detik yang sama.
    """
    delay = min(base_delay() * (2**attempt), max_delay())
    if delay <= 0:
        return 0.0
    # +-20% jitter.
    return delay * random.uniform(0.8, 1.2)


async def call_with_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
) -> T:
    """Jalankan ``factory()`` dengan retry untuk error sementara.

    ``factory`` harus membuat **percobaan baru** setiap dipanggil (jangan
    mengandalkan objek yang sudah terpakai).
    """
    attempts = max_retries()
    attempt = 0
    while True:
        try:
            return await factory()
        except Exception as exc:  # noqa: BLE001
            if attempt >= attempts or not is_retryable(exc):
                raise
            delay = compute_delay(attempt)
            attempt += 1
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            await asyncio.sleep(delay)


async def iter_with_retry(
    factory: Callable[[], AsyncIterator[T]],
    *,
    on_retry: Optional[Callable[[int, float, BaseException], None]] = None,
) -> AsyncIterator[T]:
    """Ulangi pembuatan stream selama **belum ada item** yang dikirim.

    Begitu satu item terkirim ke klien, error diteruskan apa adanya: mengulang
    akan menduplikasi konten yang sudah tampil.
    """
    attempts = max_retries()
    attempt = 0
    while True:
        started = False
        try:
            async for item in factory():
                started = True
                yield item
            return
        except Exception as exc:  # noqa: BLE001
            if started or attempt >= attempts or not is_retryable(exc):
                raise
            delay = compute_delay(attempt)
            attempt += 1
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            await asyncio.sleep(delay)
