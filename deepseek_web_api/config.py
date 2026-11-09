"""Konfigurasi global untuk client DeepSeek web.

Semua jeda antar-request ke DeepSeek dikendalikan dari **satu** variabel di
modul ini, supaya tidak ada dua angka berbeda yang harus dijaga sinkron.
Override lewat env ``DEEPSEEK_MIN_INTERVAL`` (detik, ``0`` = matikan).
"""

from __future__ import annotations

import os

#: Jeda minimum antar-request ke DeepSeek (detik).
#: Mencegah rate limit ``Messages too frequent``.
MIN_INTERVAL = float(os.environ.get("DEEPSEEK_MIN_INTERVAL", "2"))


def min_interval() -> float:
    """Jeda minimum antar-request yang berlaku (detik, ``0`` = tanpa jeda).

    Fungsi ini yang wajib dipakai klien maupun gateway. Nilai dibaca ulang
    dari env setiap kali, jadi perubahan env saat runtime ikut terpakai.
    """
    return float(os.environ.get("DEEPSEEK_MIN_INTERVAL", MIN_INTERVAL))
