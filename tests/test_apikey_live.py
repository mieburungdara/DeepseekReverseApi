"""Uji gateway dengan API key acak + chat sungguhan (butuh token.json)."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PY = r".venv\Scripts\python.exe"
PORT = 17510
BASE = f"http://127.0.0.1:{PORT}"


def post(path, body, key, stream=False):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
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
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(BASE + "/healthz", timeout=1).read()
                break
            except Exception:
                time.sleep(0.3)

        # Key acak yang benar-benar sembarang
        random_key = "sk-uji-" + os.urandom(12).hex()
        print("API key acak dipakai:", random_key)

        # 1. models dengan key acak
        req = urllib.request.Request(
            BASE + "/v1/models", headers={"Authorization": f"Bearer {random_key}"}
        )
        models = json.loads(urllib.request.urlopen(req, timeout=10).read())
        print("models:", [m["id"] for m in models["data"]])

        # 2. auth check
        req = urllib.request.Request(
            BASE + "/v1/auth/check", headers={"Authorization": f"Bearer {random_key}"}
        )
        print("auth/check:", json.loads(urllib.request.urlopen(req, timeout=10).read()))

        # 3. chat sungguhan dengan key acak
        print("\n=== chat dengan API key acak ===")
        r = post("/v1/chat/completions", {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Balas: 'gateway ok'."}],
        }, random_key)
        body = json.loads(r.read())
        text = body["choices"][0]["message"]["content"]
        print("jawaban:", repr(text[:80]))

        # 4. streaming dengan key acak
        print("\n=== streaming dengan API key acak ===")
        r = post("/v1/chat/completions", {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Hitung 6*7."}],
            "stream": True,
        }, random_key)
        raw = b""
        while True:
            c = r.read(4096)
            if not c:
                break
            raw += c
        chunks = []
        for line in raw.decode("utf-8", "replace").splitlines():
            if line.startswith("data: ") and "[DONE]" not in line:
                d = json.loads(line[6:])
                c = d["choices"][0]["delta"].get("content")
                if c:
                    chunks.append(c)
        joined = "".join(chunks)
        print("chunks:", len(chunks), "->", repr(joined))

        ok = text.strip() and joined.strip()
        print("\nHASIL:", "LULUS" if ok else "GAGAL")
        return 0 if ok else 1
    finally:
        proc.kill()
        proc.wait()


sys.exit(main())
