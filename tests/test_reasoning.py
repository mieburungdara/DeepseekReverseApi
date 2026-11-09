"""Uji reasoning_content lewat gateway (non-streaming & streaming)."""

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
PORT = 17570
BASE = f"http://127.0.0.1:{PORT}"
HDR = {"Authorization": "Bearer sk-lokal", "Content-Type": "application/json"}

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:44s} {detail}")
    if not cond:
        failures.append(label)


def post(body, retries=5, wait=25):
    req = urllib.request.Request(
        BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
        headers=HDR, method="POST",
    )
    for _ in range(retries):
        try:
            return urllib.request.urlopen(req, timeout=180)
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            print(f"  [429, tunggu {wait}s]")
            time.sleep(wait)
    raise RuntimeError("429 terus")


def main() -> int:
    env = os.environ.copy()
    env["DEEPSEEK_AUTH_MODE"] = "open"
    env["DEEPSEEK_TOKEN_FILE"] = "token.json"
    env["DEEPSEEK_MIN_INTERVAL"] = "3"
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "deepseek_web_api.server:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(BASE + "/healthz", timeout=1).read()
                break
            except Exception:
                time.sleep(0.3)

        # --- 1. Non-streaming: deepseek-chat (tanpa thinking) ---
        print("=== non-streaming: deepseek-chat ===")
        r = post({"model": "deepseek-chat",
                  "messages": [{"role": "user", "content": "Hitung 17*23."}]})
        m = json.loads(r.read())["choices"][0]["message"]
        print("  content:", repr((m.get("content") or "")[:60]))
        print("  reasoning_content:", repr((m.get("reasoning_content") or "")[:60]))
        check("chat: ada content", bool((m.get("content") or "").strip()))
        check("chat: tanpa reasoning_content", not m.get("reasoning_content"))

        # --- 2. Non-streaming: deepseek-reasoner (dengan thinking) ---
        print("\n=== non-streaming: deepseek-reasoner ===")
        r = post({"model": "deepseek-reasoner",
                  "messages": [{"role": "user", "content": "Hitung 17*23, tunjukkan langkah."}]})
        m = json.loads(r.read())["choices"][0]["message"]
        rc = m.get("reasoning_content") or ""
        print("  content:", repr((m.get("content") or "")[:60]))
        print("  reasoning_content:", repr(rc[:120]))
        check("reasoner: ada content", bool((m.get("content") or "").strip()))
        check("reasoner: ADA reasoning_content", bool(rc.strip()), f"{len(rc)} char")

        # --- 3. Streaming: deepseek-reasoner ---
        print("\n=== streaming: deepseek-reasoner ===")
        r = post({"model": "deepseek-reasoner",
                  "messages": [{"role": "user", "content": "Berapa 12*12? Tunjukkan langkah."}],
                  "stream": True})
        raw = b""
        while True:
            c = r.read(4096)
            if not c:
                break
            raw += c

        think_parts, content_parts = [], []
        for line in raw.decode("utf-8", "replace").splitlines():
            if not line.startswith("data: ") or "[DONE]" in line:
                continue
            d = json.loads(line[6:])
            delta = d["choices"][0].get("delta") or {}
            if delta.get("reasoning_content"):
                think_parts.append(delta["reasoning_content"])
            if delta.get("content"):
                content_parts.append(delta["content"])

        think = "".join(think_parts)
        text = "".join(content_parts)
        print(f"  thinking chunks: {len(think_parts)} ({len(think)} char)")
        print(f"  content chunks : {len(content_parts)} ({len(text)} char)")
        print("  thinking:", repr(think[:110]))
        check("stream: ada reasoning_content", bool(think.strip()), f"{len(think)} char")
        check("stream: ada content", bool(text.strip()))
        check("stream: thinking != content", think.strip() != text.strip())

    finally:
        proc.kill()
        proc.wait()

    print()
    if failures:
        print("GAGAL:", failures)
        return 1
    print("SEMUA LULUS - reasoning_content bekerja")
    return 0


sys.exit(main())
