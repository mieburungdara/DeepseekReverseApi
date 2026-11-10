"""Uji kebijakan retry (tanpa jaringan).

Menguji:
  * hanya error sementara yang diulang (rate limit / challenge / 40301 / 40304)
  * backoff tumbuh eksponensial dan dihitung dari env
  * ``iter_with_retry`` berhenti mengulang begitu ada item terkirim
  * ``DEEPSEEK_MAX_RETRIES=0`` mengembalikan perilaku lama (tanpa retry)
"""

import asyncio
import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
from deepseek_web_api import retry
from deepseek_web_api.http import (
    AuthError,
    ChallengeRequiredError,
    NetworkError,
    RateLimitError,
)

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:52s} {detail}")
    if not cond:
        failures.append(label)


def with_env(**values):
    """Konteks: set env sementara, kembalikan nilai lama."""
    old = {k: os.environ.get(k) for k in values}

    def apply():
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)

    def restore():
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    apply()
    return restore


print("=== mana yang boleh diulang ===")
check("RateLimitError -> retry", retry.is_retryable(RateLimitError("too frequent")))
check(
    "ChallengeRequiredError -> retry",
    retry.is_retryable(ChallengeRequiredError("captcha")),
)
check(
    "PoW 40301 -> retry",
    retry.is_retryable(NetworkError("pow", code=40301)),
)
check(
    "PoW 40304 -> retry",
    retry.is_retryable(NetworkError("pow", code=40304)),
)
check("AuthError -> TIDAK retry", not retry.is_retryable(AuthError("bad token")))
check(
    "NetworkError biasa -> TIDAK retry",
    not retry.is_retryable(NetworkError("putus")),
)
check("Exception umum -> TIDAK retry", not retry.is_retryable(ValueError("x")))

print("\n=== backoff ===")
restore = with_env(DEEPSEEK_RETRY_BASE="1", DEEPSEEK_RETRY_MAX="16")
try:
    raw = [retry.compute_delay(i) for i in range(4)]
    # Jitter +-20%, jadi periksa kisaran saja.
    bands = [(0.8, 1.2), (1.6, 2.4), (3.2, 4.8), (6.4, 9.6)]
    ok = all(lo <= d <= hi for d, (lo, hi) in zip(raw, bands))
    check("1s -> 2s -> 4s -> 8s (jitter)", ok, str([round(d, 2) for d in raw]))

    restore = with_env(DEEPSEEK_RETRY_MAX="3")
    capped = retry.compute_delay(10)
    check("dibatasi DEEPSEEK_RETRY_MAX", capped <= 3.6, f"{capped:.2f}s")
finally:
    restore()

print("\n=== jumlah percobaan ===")
restore = with_env(DEEPSEEK_MAX_RETRIES="3")
try:
    check("default 3", retry.max_retries() == 3, str(retry.max_retries()))
    restore = with_env(DEEPSEEK_MAX_RETRIES="0")
    check("0 = matikan", retry.max_retries() == 0)
    restore = with_env(DEEPSEEK_MAX_RETRIES="bukan-angka")
    check("nilai rusak -> 3", retry.max_retries() == 3)
finally:
    restore()

print("\n=== call_with_retry ===")


async def test_call_retry():
    restore = with_env(DEEPSEEK_MAX_RETRIES="3", DEEPSEEK_RETRY_BASE="0.01")
    try:
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RateLimitError("too frequent")
            return "ok"

        result = await retry.call_with_retry(flaky)
        check("gagal 2x lalu sukses", result == "ok", f"percobaan={calls['n']}")

        calls["n"] = 0

        async def always_limited():
            calls["n"] += 1
            raise RateLimitError("too frequent")

        try:
            await retry.call_with_retry(always_limited)
            check("menyerah setelah 3 retry", False, "tidak melempar")
        except RateLimitError:
            check("menyerah setelah 3 retry", calls["n"] == 4, f"percobaan={calls['n']}")

        calls["n"] = 0

        async def unauth():
            calls["n"] += 1
            raise AuthError("bad token")

        try:
            await retry.call_with_retry(unauth)
            check("AuthError tidak diulang", False, "tidak melempar")
        except AuthError:
            check("AuthError tidak diulang", calls["n"] == 1, f"percobaan={calls['n']}")

        calls["n"] = 0
        restore = with_env(DEEPSEEK_MAX_RETRIES="0")
        try:
            async def limited_once():
                calls["n"] += 1
                raise RateLimitError("too frequent")

            try:
                await retry.call_with_retry(limited_once)
            except RateLimitError:
                pass
            check("MAX_RETRIES=0 -> 1 percobaan", calls["n"] == 1, f"percobaan={calls['n']}")
        finally:
            restore()
    finally:
        restore()


asyncio.run(test_call_retry())

print("\n=== iter_with_retry ===")


async def test_iter_retry():
    restore = with_env(DEEPSEEK_MAX_RETRIES="3", DEEPSEEK_RETRY_BASE="0.01")
    try:
        # Gagal sebelum item pertama -> diulang.
        state = {"n": 0}

        async def fail_before():
            state["n"] += 1
            if state["n"] < 3:
                raise RateLimitError("too frequent")
            yield "a"
            yield "b"

        got = [x async for x in retry.iter_with_retry(fail_before)]
        check("gagal sebelum item -> ulang", got == ["a", "b"], f"{got} n={state['n']}")

        # Gagal SETELAH item terkirim -> tidak diulang (hindari duplikasi).
        state2 = {"n": 0}

        async def fail_after():
            state2["n"] += 1
            yield "pertama"
            raise RateLimitError("too frequent")

        collected = []
        try:
            async for item in retry.iter_with_retry(fail_after):
                collected.append(item)
            check("gagal setelah item -> error", False, "tidak melempar")
        except RateLimitError:
            check(
                "gagal setelah item -> tidak ulang",
                collected == ["pertama"] and state2["n"] == 1,
                f"{collected} n={state2['n']}",
            )
    finally:
        restore()


asyncio.run(test_iter_retry())

print()
if failures:
    print(f"GAGAL: {len(failures)} pemeriksaan")
    sys.exit(1)
print("SEMUA LULUS")