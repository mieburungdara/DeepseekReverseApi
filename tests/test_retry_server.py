"""Uji integrasi retry lewat endpoint HTTP asli (tanpa jaringan).

Memakai klien palsu yang membalas rate limit beberapa kali lalu sukses,
supaya jalur retry di ``/v1/chat/completions`` benar-benar teruji:
  * non-streaming: 429 dari upstream tidak sampai ke klien bila retry berhasil
  * streaming: role chunk tetap dikirim sekali, teks tidak terduplikasi
  * streaming yang gagal SETELAH mengalir: error diteruskan, bukan diulang
"""

import asyncio
import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from deepseek_web_api import retry, server
from deepseek_web_api.client import TurnResult
from deepseek_web_api.http import RateLimitError

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:54s} {detail}")
    if not cond:
        failures.append(label)


class FakeClient:
    """Klien DeepSeek palsu dengan jumlah kegagalan yang bisa diatur."""

    def __init__(self, fail_times: int, *, fail_after_chunk: bool = False):
        self.fail_times = fail_times
        self.fail_after_chunk = fail_after_chunk
        self.calls = 0

    async def create_session(self, model_type="default"):
        return "sess-fake"

    async def chat_turn(self, prompt, *, chat_session_id, parent_message_id=None, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RateLimitError("Messages too frequent. Try again later.", code=429)
        return TurnResult(
            text="halo dunia", thinking="", response_id="12345"
        )

    async def stream_turn(self, prompt, *, chat_session_id, parent_message_id=None,
                          on_response_id=None, include_thinking=False, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RateLimitError("Messages too frequent. Try again later.", code=429)
        if on_response_id is not None:
            on_response_id("12345")
        yield ("content", "halo ")
        if self.fail_after_chunk:
            raise RateLimitError("putus di tengah", code=429)
        yield ("content", "dunia")


def install_fake(fake):
    """Pasang klien palsu + reset store sesi, kembalikan pembersih."""
    old_client = server._client
    server._client = fake
    server.sessions_mod.STORE.clear()

    def restore():
        server._client = old_client
        server.sessions_mod.STORE.clear()

    return restore


def sse_text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


async def call_endpoint(payload, headers=None):
    """Panggil handler chat_completions langsung, kembalikan responsnya."""
    import json

    body = json.dumps(payload).encode("utf-8")

    class FakeRequest:
        def __init__(self):
            self.headers = headers or {}
            self._body = body

        async def json(self):
            return json.loads(self._body)

    resp = await server.chat_completions(FakeRequest())
    return resp


async def read_stream(resp) -> str:
    out = []
    async for chunk in resp.body_iterator:
        out.append(chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else str(chunk))
    return "".join(out)


async def main():
    # Percepat retry supaya test tidak menunggu detik-an.
    os.environ["DEEPSEEK_RETRY_BASE"] = "0.01"
    os.environ["DEEPSEEK_MIN_INTERVAL"] = "0"

    print("=== non-streaming: 429 2x lalu sukses ===")
    fake = FakeClient(fail_times=2)
    restore = install_fake(fake)
    try:
        resp = await call_endpoint(
            {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "halo"}],
            }
        )
        ok = getattr(resp, "status_code", None) == 200
        body = b""
        if ok:
            body = resp.body
        import json as _json

        data = _json.loads(body) if ok else {}
        text = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if ok
            else ""
        )
        check("tidak meneruskan 429", ok, f"status={getattr(resp,'status_code',None)}")
        check("jawaban akhir benar", text == "halo dunia", repr(text))
        check("retry 2x (3 percobaan)", fake.calls == 3, f"calls={fake.calls}")
    finally:
        restore()

    print("\n=== non-streaming: gagal terus -> 429 ke klien ===")
    fake = FakeClient(fail_times=99)
    restore = install_fake(fake)
    try:
        resp = await call_endpoint(
            {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "halo"}],
            }
        )
        check("429 setelah menyerah", resp.status_code == 429, f"status={resp.status_code}")
        check("percobaan dibatasi 4", fake.calls == 4, f"calls={fake.calls}")
        check("ada Retry-After", "retry-after" in {k.lower() for k in resp.headers})
    finally:
        restore()

    print("\n=== streaming: 429 2x lalu sukses ===")
    fake = FakeClient(fail_times=2)
    restore = install_fake(fake)
    try:
        resp = await call_endpoint(
            {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "halo"}],
                "stream": True,
            }
        )
        text = await read_stream(resp)
        check("ada role chunk", '"role": "assistant"' in text or '"role":"assistant"' in text)
        check("role hanya sekali", text.count('"role"') == 1, f"count={text.count(chr(34)+'role'+chr(34))}")
        check("role dikirim pertama", text.index('"role"') < text.index("halo"), "urutan benar")
        check("teks tidak terduplikasi", text.count("halo dunia") == 1)
        check("ada [DONE]", "[DONE]" in text)
        check("retry 2x (3 percobaan)", fake.calls == 3, f"calls={fake.calls}")
    finally:
        restore()

    print("\n=== streaming: putus SETELAH mengalir -> tidak diulang ===")
    fake = FakeClient(fail_times=0, fail_after_chunk=True)
    restore = install_fake(fake)
    try:
        resp = await call_endpoint(
            {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": "halo"}],
                "stream": True,
            }
        )
        text = await read_stream(resp)
        check("hanya 1 percobaan", fake.calls == 1, f"calls={fake.calls}")
        check("error dilaporkan ke klien", "upstream error" in text, repr(text[:120]))
        check("role chunk tetap ada", '"role"' in text)
        # "halo " masih ditahan buffer tool-call detector, jadi tidak terkirim;
        # yang penting tidak ada pengulangan isi.
        check("tidak ada duplikasi teks", text.count("halo ") <= 1, f"count={text.count('halo ')}")
    finally:
        restore()


asyncio.run(main())

print()
if failures:
    print(f"GAGAL: {len(failures)} pemeriksaan")
    sys.exit(1)
print("SEMUA LULUS - retry terintegrasi")