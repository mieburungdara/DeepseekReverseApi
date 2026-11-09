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


def main() -> int:
    test_pow()
    test_delta()
    test_server_shaping()
    test_server_port()
    test_token_parsing()
    test_auth()
    print("\n" + "=" * 60)
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All offline tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

