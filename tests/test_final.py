"""Verifikasi akhir: gateway + OpenAI SDK-style, dengan retry saat 429."""

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
PORT = 17560
BASE = f"http://127.0.0.1:{PORT}"
HDR = {"Authorization": "Bearer sk-lokal", "X-Conversation-Id": "final-1"}


def post(path, body, retries=4, wait=20):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={**HDR, "Content-Type": "application/json"}, method="POST",
    )
    for attempt in range(retries):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=180).read())
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            print(f"  [429, tunggu {wait}s]")
            time.sleep(wait)
    raise RuntimeError("masih 429 setelah retry")


def main() -> int:
    env = os.environ.copy()
    env["DEEPSEEK_AUTH_MODE"] = "open"
    env["DEEPSEEK_TOKEN_FILE"] = "token.json"
    env["DEEPSEEK_MIN_INTERVAL"] = "2.5"
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

        # 1. models
        models = json.loads(urllib.request.urlopen(
            urllib.request.Request(BASE + "/v1/models", headers=HDR), timeout=10).read())
        print("models:", [m["id"] for m in models["data"]])

        # 2. chat pertama
        history = [{"role": "user", "content": "Nama saya Sari, saya di Surabaya."}]
        r1 = post("/v1/chat/completions", {"model": "deepseek-chat", "messages": list(history)})
        a1 = r1["choices"][0]["message"]["content"] or ""
        print("turn 1:", repr(a1[:70]))
        history.append({"role": "assistant", "content": a1})
        if not a1.strip():
            failures.append("turn 1 kosong")

        # 3. chat kedua (delta)
        history.append({"role": "user", "content": "Siapa nama saya dan di mana?"})
        r2 = post("/v1/chat/completions", {"model": "deepseek-chat", "messages": list(history)})
        a2 = r2["choices"][0]["message"]["content"] or ""
        print("turn 2:", repr(a2[:90]))
        if "sari" not in a2.lower() or "surabaya" not in a2.lower():
            failures.append(f"konteks hilang: {a2[:80]}")

        # 4. tool calling
        tools = [{
            "type": "function",
            "function": {
                "name": "bash",
                "parameters": {"type": "object",
                               "properties": {"command": {"type": "string"}},
                               "required": ["command"]},
            },
        }]
        r3 = post("/v1/chat/completions", {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Pakai bash: pwd"}],
            "tools": tools,
            "user": "final-tool",
        })
        c3 = r3["choices"][0]
        print("tool: finish_reason =", c3["finish_reason"],
              "| calls =", len(c3["message"].get("tool_calls") or []))
        if c3["finish_reason"] != "tool_calls":
            failures.append("tool call tidak terdeteksi")

        # 5. sessions endpoint
        sess = json.loads(urllib.request.urlopen(
            urllib.request.Request(BASE + "/v1/sessions", headers=HDR), timeout=10).read())
        print("percakapan aktif:", sess["conversations"])

    finally:
        proc.kill()
        proc.wait()

    print()
    if failures:
        print("GAGAL:", failures)
        return 1
    print("SEMUA LULUS - gateway siap dipakai")
    return 0


sys.exit(main())
