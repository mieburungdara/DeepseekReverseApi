"""CLI login helper: exchange email/password for a bearer token.

Usage::

    python -m deepseek_web_api.login --email you@example.com --save token.json
    python -m deepseek_web_api.login --token-file token.json --check

The password is read via ``getpass`` so it does not land in shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys

from .client import DeepSeekWebClient


async def _login(email: str, password: str, save: str | None) -> int:
    async with DeepSeekWebClient(email=email, password=password) as client:
        state = await client.login()
        print(f"OK  token diperoleh (panjang {len(state.token)})")
        if state.user_id:
            print(f"    user id : {state.user_id}")
        if state.email:
            print(f"    email   : {state.email}")

        if save:
            state.save(save)
            print(f"    disimpan: {os.path.abspath(save)}")
        else:
            print("\ntoken (simpan sendiri, jangan dibagikan):")
            print(state.token)

        try:
            user = await client.get_current_user()
            print(f"    verifikasi /users/current: {user.get('email') or user.get('id')}")
        except Exception as exc:  # noqa: BLE001
            print(f"    peringatan: verifikasi gagal ({exc})")
    return 0


async def _check(path: str) -> int:
    async with DeepSeekWebClient(token_file=path) as client:
        user = await client.get_current_user()
        print(f"OK  token valid: {user.get('email') or user.get('id')}")
        challenge = await client.create_pow_challenge("/api/v0/chat/completion")
        print(
            f"    PoW challenge: algorithm={challenge.algorithm} "
            f"difficulty={challenge.difficulty}"
        )
    return 0


def _read_password() -> str:
    """Baca password, dengan fallback saat stdin bukan terminal.

    ``getpass`` butuh TTY; kalau dijalankan dari pipe (mis. ``echo x | login.bat``
    atau CI), ia menggantung atau error. Dalam kondisi itu pakai ``input()``.
    """
    if sys.stdin is None or not sys.stdin.isatty():
        # Tidak ada TTY: baca apa adanya dari stdin.
        try:
            return input("Password DeepSeek (terlihat): ")
        except EOFError:
            return ""
    try:
        return getpass.getpass("Password DeepSeek: ")
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception:
        # Terminal yang tidak mendukung getpass (mis. beberapa terminal Windows).
        return input("Password DeepSeek (terlihat): ")


def _read_line(prompt: str) -> str:
    """Baca satu baris dari stdin, toleran terhadap EOF."""
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email")
    parser.add_argument("--password", help="hindari; lebih baik biarkan kosong")
    parser.add_argument("--save", help="path untuk menyimpan token")
    parser.add_argument("--token-file", help="periksa token yang sudah ada")
    parser.add_argument("--check", action="store_true", help="hanya verifikasi token")
    args = parser.parse_args()

    try:
        if args.token_file and args.check:
            return asyncio.run(_check(args.token_file))

        # Email dibaca di sini (bukan di .bat) supaya `set /p` tidak menelan
        # stdin lebih dulu dan membuat prompt password langsung EOF.
        email = args.email or os.environ.get("DEEPSEEK_EMAIL")
        if not email:
            email = _read_line("Email DeepSeek : ")
        if not email:
            print("GAGAL: email kosong.", file=sys.stderr)
            return 1

        password = args.password or os.environ.get("DEEPSEEK_PASSWORD")
        if not password:
            password = _read_password()
        if not password:
            print("GAGAL: password kosong.", file=sys.stderr)
            return 1
        return asyncio.run(_login(email, password, args.save))
    except KeyboardInterrupt:
        print("\ndibatalkan", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"GAGAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
