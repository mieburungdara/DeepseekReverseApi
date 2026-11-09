"""Cek .bat: kurung buka/tutup yang tidak di-escape di dalam blok if/for.

Kurung tutup `)` di dalam `echo` pada blok `if (...)` akan menutup blok lebih
awal, memotong teks dan merusak alur. Skrip ini menemukannya sebelum dijalankan.
"""

from __future__ import annotations

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def analyse(path: str) -> list[str]:
    problems: list[str] = []
    depth = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\r\n")
            stripped = line.strip()

            # Abaikan baris komentar
            if stripped.upper().startswith(("REM ", "REM", "::")):
                continue

            # Hitung kedalaman blok: buka di akhir baris yang memulai blok
            opens = len(re.findall(r"(?<!\^)\(", stripped))
            closes = len(re.findall(r"(?<!\^)\)", stripped))

            # Baris `echo` di dalam blok: kurung harus di-escape
            if depth > 0 and stripped.lower().startswith("echo"):
                unescaped_close = re.search(r"(?<!\^)\)", stripped)
                unescaped_open = re.search(r"(?<!\^)\(", stripped)
                if unescaped_close or unescaped_open:
                    problems.append(
                        f"{os.path.basename(path)}:{lineno}: kurung tidak di-escape "
                        f"di dalam blok -> {stripped[:70]}"
                    )

            depth += opens - closes
            if depth < 0:
                depth = 0
    return problems


def main() -> int:
    files = sorted(glob.glob(os.path.join(ROOT, "*.bat")))
    if not files:
        print("tidak ada file .bat")
        return 0

    all_problems: list[str] = []
    for path in files:
        problems = analyse(path)
        status = "OK" if not problems else f"{len(problems)} masalah"
        print(f"  {os.path.basename(path):12s} {status}")
        all_problems.extend(problems)

    if all_problems:
        print("\nMasalah ditemukan:")
        for p in all_problems:
            print("  " + p)
        return 1
    print("\nSemua .bat bersih dari masalah escaping kurung.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
