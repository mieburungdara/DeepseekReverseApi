"""Live probe: verify the reverse-engineered flow against chat.deepseek.com.

Runs without credentials as far as possible, then reports what a real token
would unlock. Usage:

    python -m tests.live_probe
    DEEPSEEK_TOKEN=... python -m tests.live_probe
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepseek_web_api import DeepSeekWebClient, PowChallenge, solve_to_answer  # noqa: E402
from deepseek_web_api.client import POW_CHALLENGE_PATH  # noqa: E402
from deepseek_web_api.pow import ALGORITHM, native_available  # noqa: E402
from deepseek_web_api.keccak23 import deepseek_hash_v1  # noqa: E402


async def probe_pow_solver_offline() -> None:
    print("=" * 70)
    print("1. PoW solver, offline self-test")
    print("=" * 70)
    salt, expire_at, difficulty, secret = "probe-salt", 1794209943102, 5000, 321
    prefix = f"{salt}_{expire_at}_"
    target = deepseek_hash_v1(prefix + str(secret))
    challenge = PowChallenge(
        algorithm=ALGORITHM,
        challenge=target,
        salt=salt,
        difficulty=difficulty,
        signature="sig",
        expire_at=expire_at,
    )
    answer = solve_to_answer(challenge, "/api/v0/chat/completion")
    assert answer.answer == secret, answer.answer
    print(f"   backend={answer.backend} (native tersedia: {native_available()})")
    print(f"   prefix={prefix!r} (salt_expire_at_)")
    print(f"   solved answer={answer.answer} (expected {secret})")
    print(f"   header={answer.to_header_value()[:72]}...")
    print("   OK\n")


async def probe_unauthenticated() -> None:
    print("=" * 70)
    print("2. Unauthenticated endpoint behaviour")
    print("=" * 70)
    async with DeepSeekWebClient(token="invalid-token-for-probe") as client:
        for path in (POW_CHALLENGE_PATH, "/api/v0/users/current"):
            try:
                if path == POW_CHALLENGE_PATH:
                    await client.create_pow_challenge("/api/v0/chat/completion")
                else:
                    await client.get_current_user()
                print(f"   {path}: unexpectedly succeeded")
            except Exception as exc:  # noqa: BLE001 - probe
                print(f"   {path}: {type(exc).__name__}: {exc}")
    print()


async def probe_with_token(token: str | None = None, token_file: str | None = None) -> None:
    print("=" * 70)
    print("3. Authenticated flow")
    print("=" * 70)
    async with DeepSeekWebClient(token=token, token_file=token_file) as client:
        user = await client.get_current_user()
        print(f"   current user: {user.get('email') or user.get('id') or user}")

        challenge = await client.create_pow_challenge("/api/v0/chat/completion")
        print(
            f"   challenge: algorithm={challenge.algorithm} "
            f"difficulty={challenge.difficulty} salt={challenge.salt[:12]}..."
        )
        answer = solve_to_answer(challenge, "/api/v0/chat/completion")
        print(f"   solved answer={answer.answer}")

        print("   streaming completion:")
        collected = []
        async for event in client.stream_completion("Say hello in five words."):
            if len(collected) < 5:
                print(f"     event={event['event']!r} data={str(event['data'])[:160]}")
            collected.append(event)
        print(f"   received {len(collected)} SSE events")


async def main() -> int:
    """Return 0 hanya bila alur terautentikasi benar-benar berhasil."""
    await probe_pow_solver_offline()

    # Cek cookie browser: tanpa ini endpoint PoW diblokir AWS WAF.
    from deepseek_web_api.client import _default_cookies

    cookies = _default_cookies()
    if cookies:
        print(f"   cookie browser: ada ({len(cookies)} char)")
    else:
        print("   cookie browser: TIDAK ADA - endpoint PoW akan diblokir WAF")
        print("   -> jalankan set-cookie.bat")

    await probe_unauthenticated()

    token = os.environ.get("DEEPSEEK_TOKEN")
    token_file = os.environ.get("DEEPSEEK_TOKEN_FILE")
    if not token and not token_file and os.path.exists("token.json"):
        token_file = "token.json"

    if not (token or token_file):
        print("=" * 70)
        print("3. Authenticated flow: SKIPPED")
        print("   Simpan token dulu: set-token.bat  (atau set DEEPSEEK_TOKEN)")
        print("=" * 70)
        print("\nHASIL: belum lengkap - token belum ada, alur chat belum diuji.")
        return 2

    try:
        await probe_with_token(token, token_file)
    except Exception as exc:  # noqa: BLE001
        print("=" * 70)
        print(f"3. GAGAL: {type(exc).__name__}: {exc}")
        print("=" * 70)
        print("\nHASIL: GAGAL - token ditolak atau alur chat bermasalah.")
        return 1

    print("\nHASIL: OK - token valid, PoW terpecahkan, streaming berjalan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
