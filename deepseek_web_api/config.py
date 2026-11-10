"""Konfigurasi global untuk client DeepSeek web.

Semua jeda antar-request ke DeepSeek dikendalikan dari **satu** variabel di
modul ini, supaya tidak ada dua angka berbeda yang harus dijaga sinkron.
Override lewat env ``DEEPSEEK_MIN_INTERVAL`` (detik, ``0`` = matikan).
"""

from __future__ import annotations

import os

def _env_float(name: str, default: float) -> float:
    """Baca angka dari env; nilai rusak jatuh ke default, jangan melempar."""
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


#: Jeda minimum antar-request ke DeepSeek (detik).
#: Mencegah rate limit ``Messages too frequent``.
MIN_INTERVAL = _env_float("DEEPSEEK_MIN_INTERVAL", 5.0)

#: Batas waktu solver PoW (detik). ``0`` = tanpa batas.
#: Solver berjalan di thread terpisah, jadi timeout tidak membekukan event loop.
POW_TIMEOUT = _env_float("DEEPSEEK_POW_TIMEOUT", 180.0)


def min_interval() -> float:
    """Jeda minimum antar-request yang berlaku (detik, ``0`` = tanpa jeda).

    Fungsi ini yang wajib dipakai klien maupun gateway. Nilai dibaca ulang
    dari env setiap kali, jadi perubahan env saat runtime ikut terpakai.
    """
    return _env_float("DEEPSEEK_MIN_INTERVAL", MIN_INTERVAL)


def pow_timeout() -> float:
    """Batas waktu menyelesaikan satu challenge PoW (detik, ``0`` = tanpa batas).

    Fallback Python murni bisa memakan ~2 menit per challenge, sehingga
    defaultnya sengaja longgar. Nilai invalid jatuh ke default, bukan error,
    agar konfigurasi yang salah tidak membuat seluruh request gagal.
    """
    return _env_float("DEEPSEEK_POW_TIMEOUT", POW_TIMEOUT)
