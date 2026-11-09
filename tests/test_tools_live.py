"""Uji tool calling end-to-end ke DeepSeek sungguhan lewat server OpenAI."""

import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PY = r".venv\Scripts\python.exe"
PORT = 17520
BASE = f"http://127.0.0.1:{PORT}"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Jalankan perintah shell di mesin pengguna",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "perintah shell"}
                },
                "required": ["command"],
            },
        },
    }
]


def post(body, key="sk-uji"):
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=180)


def main() -> int:
    env = os.environ.copy()
    env["DEEPSEEK_AUTH_MODE"] = "open"
    env["DEEPSEEK_TOKEN_FILE"] = "token.json"
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "deepseek_web_api.server:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    failures = []
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(BASE + "/healthz", timeout=1).read()
                break
            except Exception:
                time.sleep(0.3)

        # --- 1. Non-streaming dengan tools ---
        print("=== non-streaming + tools ===")
        r = post({
            "model": "deepseek-chat",
            "messages": [
                {"role": "user", "content": "Tolong jalankan `pwd; ls -la` pakai tool bash."}
            ],
            "tools": TOOLS,
        })
        body = json.loads(r.read())
        choice = body["choices"][0]
        msg = choice["message"]
        print("finish_reason:", choice["finish_reason"])
        print("content:", repr((msg.get("content") or "")[:100]))
        print("tool_calls:", json.dumps(msg.get("tool_calls"), ensure_ascii=False)[:300])

        if choice["finish_reason"] != "tool_calls":
            failures.append("non-stream: finish_reason bukan tool_calls")
        if not msg.get("tool_calls"):
            failures.append("non-stream: tidak ada tool_calls")
        else:
            fn = msg["tool_calls"][0]["function"]
            if fn["name"] != "bash":
                failures.append(f"non-stream: nama {fn['name']}")
            try:
                args = json.loads(fn["arguments"])
                if "pwd" not in str(args.get("command", "")):
                    failures.append(f"non-stream: argumen {args}")
                else:
                    print("  argumen OK:", args)
            except json.JSONDecodeError:
                failures.append("non-stream: arguments bukan JSON")
        if "DSML" in (msg.get("content") or ""):
            failures.append("non-stream: markup DSML bocor ke content")

        # --- 2. Streaming dengan tools ---
        print("\n=== streaming + tools ===")
        r = post({
            "model": "deepseek-chat",
            "messages": [
                {"role": "user", "content": "Pakai tool bash untuk menjalankan `echo halo`."}
            ],
            "tools": TOOLS,
            "stream": True,
        })
        raw = b""
        while True:
            c = r.read(4096)
            if not c:
                break
            raw += c

        text_chunks = []
        tool_chunks = []
        finish = None
        for line in raw.decode("utf-8", "replace").splitlines():
            if not line.startswith("data: ") or "[DONE]" in line:
                continue
            d = json.loads(line[6:])
            ch = d["choices"][0]
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
            delta = ch.get("delta") or {}
            if delta.get("content"):
                text_chunks.append(delta["content"])
            if delta.get("tool_calls"):
                tool_chunks.extend(delta["tool_calls"])

        text = "".join(text_chunks)
        print("finish_reason:", finish)
        print("teks:", repr(text[:100]))
        print("tool_calls:", json.dumps(tool_chunks, ensure_ascii=False)[:300])

        if finish != "tool_calls":
            failures.append(f"stream: finish {finish}")
        if not tool_chunks:
            failures.append("stream: tidak ada tool_calls")
        if "DSML" in text:
            failures.append("stream: markup DSML bocor ke content")

        # --- 3. Tanpa tools: jawaban biasa tetap normal ---
        print("\n=== tanpa tools (regresi) ===")
        r = post({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Balas: ok"}],
        })
        body = json.loads(r.read())
        msg = body["choices"][0]["message"]
        print("content:", repr(msg.get("content")))
        print("tool_calls:", msg.get("tool_calls"))
        if not (msg.get("content") or "").strip():
            failures.append("tanpa tools: content kosong")
        if msg.get("tool_calls"):
            failures.append("tanpa tools: ada tool_calls tak terduga")

    finally:
        proc.kill()
        proc.wait()

    print()
    if failures:
        print("GAGAL:", failures)
        return 1
    print("SEMUA LULUS - tool calling end-to-end")
    return 0


sys.exit(main())
