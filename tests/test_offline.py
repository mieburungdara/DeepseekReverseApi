"""Offline unit tests: PoW solver, delta accumulator, request shaping.

These run without network access: ``python -m tests.test_offline``
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deepseek_web_api.pow import ALGORITHM  # noqa: E402
from deepseek_web_api.server import MODEL_MAP, _flatten_messages, _resolve_model  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def test_pow() -> None:
    print("\n[PoW solver - Keccak 23 ronde]")
    from deepseek_web_api.keccak23 import (
        DEEPSEEK_ROUNDS,
        deepseek_hash_v1,
        sha3_256_hex,
    )
    from deepseek_web_api.pow import PowChallenge, solve, solve_to_answer

    # 1) 24 ronde HARUS identik dengan hashlib -> membuktikan implementasi benar
    for msg in (b"", b"abc", b"x" * 135, b"y" * 136, b"z" * 300):
        check(
            f"24 ronde == hashlib ({len(msg)}B)",
            sha3_256_hex(msg) == hashlib.sha3_256(msg).hexdigest(),
        )

    # 2) 23 ronde HARUS berbeda dari SHA3-256
    check(
        "23 ronde != sha3-256",
        deepseek_hash_v1("abc") != hashlib.sha3_256(b"abc").hexdigest(),
    )
    check(
        "23 ronde deterministik",
        deepseek_hash_v1("abc") == deepseek_hash_v1(b"abc"),
    )

    # 3) prefix memakai expire_at, BUKAN difficulty
    salt, expire_at, difficulty, secret = "s4lt", 1794209943102, 5000, 3133
    target = deepseek_hash_v1(f"{salt}_{expire_at}_{secret}")
    challenge = PowChallenge(
        algorithm=ALGORITHM,
        challenge=target,
        salt=salt,
        difficulty=difficulty,
        signature="sig123",
        expire_at=expire_at,
    )
    check("prefix = salt_expire_at_", challenge.prefix() == f"{salt}_{expire_at}_")

    answer, backend = solve(challenge)
    check("menemukan jawaban benar", answer == secret, f"got {answer} via {backend}")
    check("backend terdeteksi", backend in ("native", "python"), backend)

    # 4) challenge tanpa expire_at harus error jelas
    from deepseek_web_api.pow import PowError

    try:
        PowChallenge(ALGORITHM, target, salt, difficulty, "s").prefix()
        check("expire_at wajib", False, "tidak raise")
    except PowError:
        check("expire_at wajib", True)

    # 5) header = base64(JSON) dengan semua kunci
    ans = solve_to_answer(challenge, "/api/v0/chat/completion")
    decoded = json.loads(base64.b64decode(ans.to_header_value()).decode())
    check(
        "header base64 berisi semua kunci",
        set(decoded) == {
            "algorithm", "challenge", "salt", "answer", "signature", "target_path",
        },
        str(sorted(decoded)),
    )
    check("header answer benar", decoded["answer"] == secret)


def test_delta() -> None:
    print("\n[Delta accumulator - protokol nyata]")
    from deepseek_web_api.stream import DeltaAccumulator

    # Event pembuka: seluruh objek response dikirim sekaligus
    acc = DeltaAccumulator()
    acc.apply_event({
        "v": {
            "response": {
                "message_id": 2,
                "status": "WIP",
                "fragments": [{"id": 2, "type": "RESPONSE", "content": "Mer"}],
            }
        }
    })
    check("event pembuka mengekstrak content", acc.answer_text() == "Mer", acc.answer_text())

    # Path TANPA slash depan + indeks -1 + p/o diwarisi
    acc.apply_event({"p": "response/fragments/-1/content", "o": "APPEND", "v": "ah"})
    check("APPEND dengan path tanpa slash", acc.answer_text() == "Merah", acc.answer_text())

    acc.apply_event({"v": ", biru"})   # p & o diwarisi
    check("p/o diwarisi dari delta sebelumnya", acc.answer_text() == "Merah, biru", acc.answer_text())

    acc.apply_event({"v": "."})
    check("APPEND berantai", acc.answer_text() == "Merah, biru.", acc.answer_text())

    # BATCH pada root response, path relatif
    acc.apply_event({
        "p": "response",
        "o": "BATCH",
        "v": [
            {"p": "accumulated_token_usage", "v": 54},
            {"p": "status", "v": "FINISHED"},
        ],
    })
    check("BATCH menerapkan status", acc.status == "FINISHED", str(acc.status))

    # THINK tidak boleh masuk jawaban
    acc2 = DeltaAccumulator()
    acc2.apply_event({
        "v": {"response": {
            "status": "WIP",
            "fragments": [
                {"type": "THINK", "content": "berpikir..."},
                {"type": "RESPONSE", "content": "Jawaban"},
            ],
        }}
    })
    check("THINK tidak masuk jawaban", acc2.answer_text() == "Jawaban", acc2.answer_text())
    check("THINK terpisah", acc2.thinking_text() == "berpikir...", acc2.thinking_text())

    # SET menimpa
    acc2.apply_event({"p": "response/fragments/1/content", "o": "SET", "v": "Ganti"})
    check("SET menimpa isi", acc2.answer_text() == "Ganti", acc2.answer_text())


def test_server_shaping() -> None:
    print("\n[Server request shaping]")
    check("deepseek-chat maps to default", _resolve_model("deepseek-chat") == "default")
    check("deepseek-reasoner maps to expert", _resolve_model("deepseek-reasoner") == "expert")
    check("unknown model passes through", _resolve_model("mystery") == "mystery")
    check("empty model defaults", _resolve_model(None) == "default")
    check("all model types covered", set(MODEL_MAP.values()) == {"default", "expert", "vision"})

    _, prompt = _flatten_messages(
        [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "Bye"},
        ]
    )
    check("system prompt folded in", "Be terse." in prompt, prompt)
    check("turns are labelled", "User: Hi" in prompt and "Assistant: Hello" in prompt, prompt)

    _, multimodal = _flatten_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "x"}},
                ],
            }
        ]
    )
    check("multimodal text parts extracted", "describe" in multimodal, multimodal)


def test_server_port() -> None:
    print("\n[Server port selection]")
    from deepseek_web_api.server import (
        DEFAULT_PORT,
        EPHEMERAL_RANGE,
        _describe_port,
        find_free_port,
        port_is_free,
    )

    low, high = EPHEMERAL_RANGE
    check(
        "default port is outside the ephemeral range",
        not (low <= DEFAULT_PORT <= high),
        f"{DEFAULT_PORT} vs {low}-{high}",
    )
    check("default port is not a common dev port", DEFAULT_PORT not in {
        80, 443, 3000, 5000, 8000, 8080, 8888, 9000, 11434,
    }, str(DEFAULT_PORT))
    check("describe warns inside ephemeral range", "PERINGATAN" in _describe_port(low + 1))
    check("describe is plain outside ephemeral range", _describe_port(17395) == "17395")

    # find_free_port must never hand back an ephemeral port
    try:
        found = find_free_port(17395)
        check("find_free_port skips ephemeral", not (low <= found <= high), str(found))
    except RuntimeError as exc:
        check("find_free_port skips ephemeral", False, str(exc))

    # port_is_free should report False for a port we are actively holding
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        busy_port = held.getsockname()[1]
        check("port_is_free detects a busy port", not port_is_free(busy_port), str(busy_port))


def test_token_parsing() -> None:
    print("\n[Token file parsing]")
    import json
    import tempfile

    from deepseek_web_api.http import TokenState
    from deepseek_web_api.save_token import extract_token

    # extract_token: berbagai bentuk tempelan
    check("raw token", extract_token("abc123") == "abc123")
    check("quoted token", extract_token('"abc123"') == "abc123")
    check("JSON with token key", extract_token('{"token":"xyz"}') == "xyz")
    check("JSON DevTools value", extract_token('{"value":"xyz","__version":"0"}') == "xyz")
    check("empty input -> empty", extract_token("") == "")
    check("garbage JSON -> empty", extract_token("{bukan json}") == "")

    # TokenState.load: tiga bentuk file
    cases = {
        "raw": ("abc123", "abc123"),
        "json": (json.dumps({"token": "abc123"}), "abc123"),
        "devtools": (json.dumps({"value": "abc123", "__version": "0"}), "abc123"),
    }
    for name, (content, expected) in cases.items():
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(content)
            path = fh.name
        try:
            got = TokenState.load(path).token
            check(f"load {name} form", got == expected, f"got {got!r}")
        finally:
            os.unlink(path)

    # File tanpa token harus error yang jelas, bukan KeyError
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(json.dumps({"foo": "bar"}))
        path = fh.name
    try:
        try:
            TokenState.load(path)
            check("invalid file raises ValueError", False, "tidak raise")
        except ValueError as exc:
            check("invalid file raises ValueError", "token" in str(exc).lower())
        except KeyError:
            check("invalid file raises ValueError", False, "masih KeyError")
    finally:
        os.unlink(path)


def test_auth() -> None:
    print("\n[API key auth]")
    from deepseek_web_api import auth

    def check_with(mode: str, headers: dict, expected_key: str | None = None):
        old_mode = os.environ.get("DEEPSEEK_AUTH_MODE")
        old_key = os.environ.get("DEEPSEEK_API_KEY")
        os.environ["DEEPSEEK_AUTH_MODE"] = mode
        if expected_key is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = expected_key
        try:
            return auth.check(headers)
        finally:
            if old_mode is None:
                os.environ.pop("DEEPSEEK_AUTH_MODE", None)
            else:
                os.environ["DEEPSEEK_AUTH_MODE"] = old_mode
            if old_key is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old_key

    # extract_key: berbagai header
    check("extract Bearer", auth.extract_key({"authorization": "Bearer abc"}) == "abc")
    check("extract raw", auth.extract_key({"authorization": "abc"}) == "abc")
    check("extract x-api-key", auth.extract_key({"x-api-key": "abc"}) == "abc")
    check("extract api-key", auth.extract_key({"api-key": "abc"}) == "abc")
    check("extract kosong", auth.extract_key({"authorization": "Bearer "}) is None)
    check("extract tanpa header", auth.extract_key({}) is None)

    # mode open: apa pun lolos
    check("open tanpa key", check_with("open", {}).ok)
    check("open key acak", check_with("open", {"authorization": "Bearer apa-saja"}).ok)
    check("open key aneh", check_with("open", {"x-api-key": "123!@#"}).ok)

    # mode any: wajib ada key, isi bebas
    check("any tanpa key ditolak", not check_with("any", {}).ok)
    check("any key apa pun lolos", check_with("any", {"authorization": "Bearer x"}).ok)

    # mode strict
    check(
        "strict key benar",
        check_with("strict", {"authorization": "Bearer rahasia"}, "rahasia").ok,
    )
    check(
        "strict key salah",
        not check_with("strict", {"authorization": "Bearer salah"}, "rahasia").ok,
    )
    check(
        "strict tanpa key",
        not check_with("strict", {}, "rahasia").ok,
    )
    check(
        "strict tanpa DEEPSEEK_API_KEY ditolak",
        not check_with("strict", {"authorization": "Bearer apa"}, None).ok,
    )

    # mode tak dikenal jatuh ke open
    check("mode tak dikenal -> open", check_with("ngawur", {}).ok)


def test_watchdog() -> None:
    print("\n[Watchdog proses yatim]")
    from deepseek_web_api.server import _getppid

    ppid = _getppid()
    check("PPID terdeteksi", ppid > 0, f"ppid={ppid}")
    check("PPID bukan diri sendiri", ppid != os.getpid(), f"ppid={ppid}")

    # Watchdog bisa dimatikan lewat env (penting untuk service/daemon).
    from deepseek_web_api.server import _start_parent_watchdog

    os.environ["DEEPSEEK_NO_PARENT_WATCHDOG"] = "1"
    try:
        _start_parent_watchdog()  # tidak boleh melempar / membuat thread
        check("watchdog bisa dinonaktifkan via env", True)
    finally:
        os.environ.pop("DEEPSEEK_NO_PARENT_WATCHDOG", None)


def test_retry() -> None:
    print("\n[Retry otomatis - rate limit 429]")
    import asyncio

    from deepseek_web_api import retry
    from deepseek_web_api.http import (
        AuthError,
        ChallengeRequiredError,
        NetworkError,
        RateLimitError,
    )

    def with_env(**values):
        old = {k: os.environ.get(k) for k in values}
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)
        return old

    def restore_env(old):
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # Hanya error sementara yang diulang.
    check("RateLimitError diulang", retry.is_retryable(RateLimitError("x")))
    check(
        "ChallengeRequiredError diulang",
        retry.is_retryable(ChallengeRequiredError("x")),
    )
    check("PoW 40301 diulang", retry.is_retryable(NetworkError("x", code=40301)))
    check("AuthError TIDAK diulang", not retry.is_retryable(AuthError("x")))
    check("NetworkError biasa TIDAK diulang", not retry.is_retryable(NetworkError("x")))
    check("ValueError TIDAK diulang", not retry.is_retryable(ValueError("x")))

    # Backoff eksponensial 1s -> 2s -> 4s -> 8s, dengan jitter +-20%.
    old = with_env(DEEPSEEK_RETRY_BASE="1", DEEPSEEK_RETRY_MAX="16")
    try:
        bands = [(0.8, 1.2), (1.6, 2.4), (3.2, 4.8), (6.4, 9.6)]
        delays = [retry.compute_delay(i) for i in range(4)]
        check(
            "backoff 1s -> 2s -> 4s -> 8s",
            all(lo <= d <= hi for d, (lo, hi) in zip(delays, bands)),
            str([round(d, 2) for d in delays]),
        )
        check("dibatasi DEEPSEEK_RETRY_MAX", retry.compute_delay(20) <= 19.2)
    finally:
        restore_env(old)

    # Jumlah percobaan: default 3, 0 mematikan, nilai rusak -> 3.
    old = with_env(DEEPSEEK_MAX_RETRIES="3")
    try:
        check("default 3 retry", retry.max_retries() == 3)
        old2 = with_env(DEEPSEEK_MAX_RETRIES="0")
        try:
            check("0 mematikan retry", retry.max_retries() == 0)
        finally:
            restore_env(old2)
        old2 = with_env(DEEPSEEK_MAX_RETRIES="rusak")
        try:
            check("nilai rusak -> 3", retry.max_retries() == 3)
        finally:
            restore_env(old2)
    finally:
        restore_env(old)

    async def run_cases() -> None:
        old_e = with_env(DEEPSEEK_MAX_RETRIES="3", DEEPSEEK_RETRY_BASE="0.001")
        try:
            calls = {"n": 0}

            async def flaky():
                calls["n"] += 1
                if calls["n"] < 3:
                    raise RateLimitError("too frequent")
                return "ok"

            result = await retry.call_with_retry(flaky)
            check("gagal 2x lalu sukses", result == "ok", f"n={calls['n']}")

            calls["n"] = 0

            async def always():
                calls["n"] += 1
                raise RateLimitError("too frequent")

            try:
                await retry.call_with_retry(always)
                check("menyerah setelah batas", False, "tidak melempar")
            except RateLimitError:
                check("menyerah setelah batas", calls["n"] == 4, f"n={calls['n']}")

            # Streaming: gagal sebelum item pertama -> diulang.
            state = {"n": 0}

            async def fail_before():
                state["n"] += 1
                if state["n"] < 2:
                    raise RateLimitError("too frequent")
                yield "a"
                yield "b"

            got = [x async for x in retry.iter_with_retry(fail_before)]
            check("stream gagal awal -> diulang", got == ["a", "b"], str(got))

            # Streaming: gagal SETELAH item -> tidak diulang (anti duplikasi).
            state2 = {"n": 0}

            async def fail_after():
                state2["n"] += 1
                yield "pertama"
                raise RateLimitError("too frequent")

            collected = []
            try:
                async for item in retry.iter_with_retry(fail_after):
                    collected.append(item)
                check("stream gagal setelah item", False, "tidak melempar")
            except RateLimitError:
                check(
                    "stream gagal setelah item -> tidak diulang",
                    collected == ["pertama"] and state2["n"] == 1,
                    f"{collected} n={state2['n']}",
                )
        finally:
            restore_env(old_e)

    asyncio.run(run_cases())


def test_p0_hardening() -> None:
    """Regresi untuk perbaikan P0: target PoW, bind policy, dan kunci async."""
    import asyncio
    import contextlib
    import io

    import httpx

    from deepseek_web_api import config, server
    from deepseek_web_api.client import TurnResult
    from deepseek_web_api.pow import PowChallenge, PowError, solve
    from deepseek_web_api.sessions import SessionStore

    print("\n[P0 hardening]")

    # --- 1. Target PoW wajib 64 hex, baik dari API maupun langsung ----------
    good = "a" * 64
    base = {
        "algorithm": "DeepSeekHashV1",
        "challenge": good,
        "salt": "s",
        "difficulty": 100,
        "signature": "",
        "expire_at": 1,
    }
    for label, patch in (
        ("target pendek", {"challenge": "abc"}),
        ("target 65 char", {"challenge": good + "a"}),
        ("target non-hex", {"challenge": "z" * 64}),
        ("field hilang", {"challenge": None}),
        ("difficulty rusak", {"difficulty": "bukan-angka"}),
    ):
        payload = dict(base)
        payload.update(patch)
        try:
            PowChallenge.from_api(payload)
            check(f"from_api tolak {label}", False, "tidak melempar")
        except PowError:
            check(f"from_api tolak {label}", True)

    ch = PowChallenge.from_api(dict(base))
    check("from_api menerima target valid", ch.challenge == good)
    check(
        "from_api menormalkan huruf besar",
        PowChallenge.from_api({**base, "challenge": "A" * 64}).challenge == good,
    )

    bad = PowChallenge(
        algorithm="DeepSeekHashV1", challenge="abc", salt="s",
        difficulty=10, signature="", expire_at=1,
    )
    try:
        solve(bad)
        check("solve() tolak target pendek", False, "tidak melempar")
    except PowError:
        check("solve() tolak target pendek", True)

    # --- 2. Nilai env rusak tidak boleh membuat request gagal --------------
    old = {k: os.environ.get(k) for k in
           ("DEEPSEEK_MIN_INTERVAL", "DEEPSEEK_POW_TIMEOUT",
            "DEEPSEEK_AUTH_MODE", "DEEPSEEK_API_KEY", "DEEPSEEK_MAX_RETRIES")}
    try:
        os.environ["DEEPSEEK_MIN_INTERVAL"] = "abc"
        os.environ["DEEPSEEK_POW_TIMEOUT"] = "xyz"
        check("min_interval env rusak -> default", config.min_interval() == config.MIN_INTERVAL)
        check("pow_timeout env rusak -> default", config.pow_timeout() == config.POW_TIMEOUT)
        os.environ["DEEPSEEK_POW_TIMEOUT"] = "0"
        check("pow_timeout 0 = tanpa batas", config.pow_timeout() == 0.0)

        # --- 3. Bind non-loopback wajib strict -----------------------------
        for host, expected in (
            ("127.0.0.1", True), ("localhost", True), ("::1", True),
            ("0.0.0.0", False), ("::", False), ("192.168.1.5", False),
            ("example.com", False),
        ):
            check(f"loopback({host})", server.is_loopback_host(host) is expected)

        os.environ["DEEPSEEK_AUTH_MODE"] = "open"
        os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                server._enforce_bind_policy("0.0.0.0")
            check("bind 0.0.0.0 + open ditolak", False, "tidak melempar")
        except SystemExit:
            check("bind 0.0.0.0 + open ditolak", True)

        os.environ["DEEPSEEK_AUTH_MODE"] = "strict"
        os.environ["DEEPSEEK_API_KEY"] = "sk-uji"
        try:
            server._enforce_bind_policy("0.0.0.0")
            check("bind 0.0.0.0 + strict diizinkan", True)
        except SystemExit:
            check("bind 0.0.0.0 + strict diizinkan", False)
        os.environ["DEEPSEEK_AUTH_MODE"] = "open"

        # Pertahanan kedua: aplikasi dijalankan langsung lewat uvicorn
        # (tanpa lewat main) tetap menolak peer non-loopback.
        class FakeReq:
            def __init__(self, host, headers=None):
                self.client = type("Peer", (), {"host": host})()
                self.headers = headers or {}

        check("peer loopback diizinkan (open)",
              server._require_auth(FakeReq("127.0.0.1")) is None)
        denied = server._require_auth(FakeReq("192.168.1.5"))
        check("peer non-loopback ditolak (open)",
              denied is not None and denied.status_code == 401)
        os.environ["DEEPSEEK_AUTH_MODE"] = "strict"
        os.environ["DEEPSEEK_API_KEY"] = "sk-uji"
        check("peer non-loopback lolos strict + key",
              server._require_auth(FakeReq("192.168.1.5",
                                          {"authorization": "Bearer sk-uji"})) is None)
        os.environ["DEEPSEEK_AUTH_MODE"] = "open"

        # --- 4. Kunci async ------------------------------------------------
        async def run_async_cases():
            # Throttle: dua pemanggil konkuren harus berjarak >= interval.
            os.environ["DEEPSEEK_MIN_INTERVAL"] = "0.12"
            server._last_request_at = __import__("time").monotonic()

            async def one():
                await server._throttle()
                return __import__("time").monotonic()

            a, b = await asyncio.gather(one(), one())
            check("throttle terserialisasi", abs(a - b) >= 0.10, f"delta={abs(a-b):.3f}s")

            # get_client: tepat satu instance walau dipanggil bersamaan.
            class DummyClient:
                pass

            made = []
            old_client = server._client
            old_ctor = server.DeepSeekWebClient
            server._client = None
            server.DeepSeekWebClient = lambda **kw: made.append(DummyClient()) or made[-1]
            try:
                results = await asyncio.gather(*[server.get_client() for _ in range(6)])
                check("get_client satu instance", len(made) == 1 and all(r is made[0] for r in results),
                      f"dibuat={len(made)}")
            finally:
                server.DeepSeekWebClient = old_ctor
                server._client = old_client

            # SessionStore: kunci per-key, stabil, dan model_type tersimpan.
            st = SessionStore()
            g1 = st.guard("k1")
            check("guard stabil per key", st.guard("k1") is g1)
            check("guard beda per key", st.guard("k2") is not g1)
            st.put("k1", "sess", model_type="expert")
            check("model_type tersimpan", st.get("k1").model_type == "expert")

            # Dua request konkuren pada percakapan sama -> SATU sesi baru,
            # dan parent_message_id giliran kedua sudah ter-update.
            class FakeClient:
                def __init__(self):
                    self.created = 0
                    self.parents = []

                async def create_session(self, model_type="default"):
                    self.created += 1
                    await asyncio.sleep(0.03)
                    return "sess-fake"

                async def chat_turn(self, prompt, *, chat_session_id,
                                   parent_message_id=None, **kw):
                    self.parents.append(parent_message_id)
                    return TurnResult(text="ok", thinking="", response_id="42")

                async def stream_turn(self, prompt, *, chat_session_id,
                                     parent_message_id=None, on_response_id=None,
                                     include_thinking=False, **kw):
                    if on_response_id is not None:
                        on_response_id("77")
                    yield ("content", "halo dunia")

            fake = FakeClient()
            old_client = server._client
            server._client = fake
            server.sessions_mod.STORE.clear()
            os.environ["DEEPSEEK_MIN_INTERVAL"] = "0"
            os.environ["DEEPSEEK_MAX_RETRIES"] = "0"
            payload = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "hi"}],
            }
            try:
                tr = httpx.ASGITransport(app=server.app, raise_app_exceptions=False)
                async with httpx.AsyncClient(transport=tr, base_url="http://test") as c:
                    responses = await asyncio.gather(*[
                        c.post("/v1/chat/completions", json=payload,
                               headers={"X-Conversation-Id": "conv-race"})
                        for _ in range(2)
                    ])
                    race_created = fake.created  # sebelum request streaming (percakapan lain)
                    # Streaming menyerahkan kunci ke generator; kunci harus
                    # lepas setelah seluruh chunk dikonsumsi.
                    r = await c.post(
                        "/v1/chat/completions",
                        json={**payload, "stream": True},
                        headers={"X-Conversation-Id": "conv-stream"},
                    )
                check("dua request tetap 200", all(r.status_code == 200 for r in responses))
                check("satu sesi DeepSeek dibuat", race_created == 1,
                      f"created={race_created}")
                check("parent giliran ke-2 ter-update", fake.parents == [None, 42],
                      str(fake.parents))
                key = server.sessions_mod.conversation_key(
                    payload, {"x-conversation-id": "conv-race"}
                )
                check("kunci percakapan dilepas", not server.sessions_mod.STORE.guard(key).locked())

                check("stream tetap 200", r.status_code == 200, str(r.status_code))
                check("stream punya [DONE]", "[DONE]" in r.text)
                key2 = server.sessions_mod.conversation_key(
                    payload, {"x-conversation-id": "conv-stream"}
                )
                check("guard streaming dilepas",
                      not server.sessions_mod.STORE.guard(key2).locked())
            finally:
                server._client = old_client
                server.sessions_mod.STORE.clear()

            # --- 5. PoW tidak membekukan event loop -------------------------
            import time as _time

            import deepseek_web_api.client as client_mod

            class StubAnswer:
                backend = "stub"

                def to_header_value(self):
                    return "stub"

            original_solver = client_mod.solve_to_answer

            def slow_solve(_challenge, _path):
                _time.sleep(0.12)
                return StubAnswer()

            async def fake_challenge(_self, _path):
                return PowChallenge.from_api(dict(base))

            client_mod.solve_to_answer = slow_solve
            os.environ["DEEPSEEK_POW_TIMEOUT"] = "5"
            try:
                probe = client_mod.DeepSeekWebClient(token="dummy")
                probe.create_pow_challenge = lambda path: fake_challenge(probe, path)
                ticks = 0
                done = False

                async def ticker():
                    nonlocal ticks
                    while not done:
                        ticks += 1
                        await asyncio.sleep(0.01)

                task = asyncio.create_task(ticker())
                header = await probe._pow_header("/api/v0/chat/completion")
                done = True
                await task
                check("PoW header tetap terbentuk", "x-ds-pow-response" in header)
                check("event loop responsif saat PoW", ticks >= 5, f"ticks={ticks}")
            finally:
                client_mod.solve_to_answer = original_solver

            # Batas waktu solver dihormati.
            os.environ["DEEPSEEK_POW_TIMEOUT"] = "0.02"
            client_mod.solve_to_answer = slow_solve
            try:
                probe = client_mod.DeepSeekWebClient(token="dummy")
                probe.create_pow_challenge = lambda path: fake_challenge(probe, path)
                try:
                    await probe._pow_header("/api/v0/chat/completion")
                    check("PoW timeout ditegakkan", False, "tidak melempar")
                except Exception as exc:
                    check("PoW timeout ditegakkan", "batas waktu" in str(exc), str(exc))
            finally:
                client_mod.solve_to_answer = original_solver

            # Body rusak -> 400, bukan 500.
            tr = httpx.ASGITransport(app=server.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=tr, base_url="http://test") as c:
                r1 = await c.post("/v1/chat/completions", json=[{"role": "user"}])
                r2 = await c.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "hi"}, "rusak"]})
            check("body list -> 400", r1.status_code == 400, str(r1.status_code))
            check("pesan bukan objek -> 400", r2.status_code == 400, str(r2.status_code))

        asyncio.run(run_async_cases())
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    test_pow()
    test_delta()
    test_server_shaping()
    test_server_port()
    test_token_parsing()
    test_auth()
    test_retry()
    test_watchdog()
    test_p0_hardening()
    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All offline tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

