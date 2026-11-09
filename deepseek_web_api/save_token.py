"""Simpan token DeepSeek ke file, menerima berbagai bentuk input.

Dipakai oleh ``set-token.bat``, tapi bisa juga dari command line::

    python -m deepseek_web_api.save_token "<token>"
    echo <token> | python -m deepseek_web_api.save_token --stdin

Menerima:
  * token mentah              -> abc123
  * JSON {"token": "..."}     -> {"token": "abc123"}
  * JSON DevTools             -> {"value": "abc123", "__version": "0"}
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys


def extract_token(raw: str) -> str:
    """Ambil token dari teks apa pun bentuknya."""
    text = (raw or "").strip()
    if not text:
        return ""

    # Kutip pembungkus dari shell
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Bukan JSON valid: coba tangkap pola "key": "value" secara longgar
            match = re.search(r'"(?:token|value)"\s*:\s*"([^"]+)"', text)
            return match.group(1) if match else ""
        if isinstance(data, dict):
            for key in ("token", "value", "userToken"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value.strip()
        return ""

    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="Simpan token DeepSeek")
    parser.add_argument("token", nargs="?", help="token (opsional)")
    parser.add_argument("--file", default="token.json", help="file tujuan")
    parser.add_argument("--stdin", action="store_true", help="baca dari stdin")
    args = parser.parse_args()

    if args.token:
        raw = args.token
    elif args.stdin:
        raw = sys.stdin.read()
    else:
        try:
            raw = input("Token: ")
        except EOFError:
            raw = ""

    token = extract_token(raw)
    if not token:
        print(
            "[GAGAL] Tidak ada token yang bisa dibaca dari input.\n"
            "         Pastikan Anda menyalin nilai 'value' dari key 'userToken'.",
            file=sys.stderr,
        )
        return 1

    payload = {"token": token}
    with open(args.file, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    try:
        os.chmod(args.file, 0o600)
    except OSError:
        pass

    print(f"[OK] Token tersimpan di {os.path.abspath(args.file)}")
    print(f"     Panjang token: {len(token)} karakter")
    print(f"     Awal token   : {token[:8]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
