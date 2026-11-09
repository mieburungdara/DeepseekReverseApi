"""Uji server OpenAI-compatible terhadap DeepSeek sungguhan.

Menjalankan server di port sementara, lalu memanggil /v1/chat/completions
(non-streaming dan streaming) seperti SDK OpenAI.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORT = 17493
BASE = f"http://127.0.0.1:{PORT}"


def post(path: str, body: dict, stream: bool = False):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer x"},
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=180)


def main() -> int:
    # 1. tunggu server siap
    for _ in range(40):
        try:
            urllib.request.urlopen(BASE + "/healthz", timeout=2).read()
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("server tidak siap")
        return 1

    # 2. /v1/models
    models = json.loads(urllib.request.urlopen(BASE + "/v1/models", timeout=10).read())
    print("models:", [m["id"] for m in models["data"]])

    # 3. non-streaming
    print("\n=== non-streaming ===")
    resp = post("/v1/chat/completions", {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "Jawab sangat singkat."},
            {"role": "user", "content": "Sebutkan 3 warna primer."},
        ],
    })
    body = json.loads(resp.read())
    print("finish_reason:", body["choices"][0]["finish_reason"])
    print("content:", repr(body["choices"][0]["message"]["content"]))
    print("usage:", body["usage"])

    # 4. streaming
    print("\n=== streaming ===")
    resp = post("/v1/chat/completions", {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "Hitung 7*8."}],
        "stream": True,
    })
    collected = []
    buffer = ""
    while True:
        raw = resp.read(1)
        if not raw:
            break
        buffer += raw.decode("utf-8", "replace")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for line in block.splitlines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    print("  [DONE]")
                    buffer = ""
                    break
                chunk = json.loads(payload)
                delta = chunk["choices"][0]["delta"]
                if delta.get("content"):
                    collected.append(delta["content"])
        if not buffer and "[DONE]" in block:
            break
    text = "".join(collected)
    print("chunks:", len(collected))
    print("gabungan:", repr(text))
    return 0 if text.strip() else 1


if __name__ == "__main__":
    sys.exit(main())

