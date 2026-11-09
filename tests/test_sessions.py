"""Uji: satu percakapan harus memakai SATU sesi DeepSeek.

Sebelum perbaikan, setiap request membuat sesi baru sehingga konteks hilang.
"""

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
PORT = 17530
BASE = f"http://127.0.0.1:{PORT}"


def post(body, headers=None, retries=5, wait=25):
    h = {"Content-Type": "application/json", "Authorization": "Bearer sk-uji"}
    h.update(headers or {})
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers=h,
        method="POST",
    )
    last = None
    for _ in range(retries):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=180).read())
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            last = e
            print(f"  [429 rate limit, tunggu {wait}s]")
            time.sleep(wait)
    raise last  # type: ignore[misc]


def get(path):
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": "Bearer sk-uji"}
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


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

        # ---- 1. Dua request dengan X-Conversation-Id sama ----
        print("=== dua giliran, satu percakapan ===")
        hdr = {"X-Conversation-Id": "uji-1"}
        r1 = post({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Nama saya Budi."}],
        }, hdr)
        t1 = r1["choices"][0]["message"]["content"]
        print("turn 1:", repr(t1[:70]))

        r2 = post({
            "model": "deepseek-chat",
            "messages": [
                {"role": "user", "content": "Nama saya Budi."},
                {"role": "assistant", "content": t1},
                {"role": "user", "content": "Siapa nama saya?"},
            ],
        }, hdr)
        t2 = r2["choices"][0]["message"]["content"]
        print("turn 2:", repr(t2[:80]))

        stats = get("/v1/sessions")
        print("sessions:", json.dumps(stats, ensure_ascii=False)[:220])
        if stats["conversations"] != 1:
            failures.append(f"harus 1 percakapan, dapat {stats['conversations']}")
        else:
            item = stats["items"][0]
            if item["turns"] < 2:
                failures.append(f"turns harus >=2, dapat {item['turns']}")
            if item["parent_message_id"] is None:
                failures.append("parent_message_id tidak tersimpan")
            else:
                print("  parent_message_id tersimpan:", item["parent_message_id"])
        if "budi" not in t2.lower():
            failures.append(f"konteks hilang: {t2[:80]}")

        # ---- 2. Percakapan berbeda -> sesi berbeda ----
        print("\n=== percakapan berbeda -> sesi berbeda ===")
        post({
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Halo, ini percakapan lain."}],
        }, {"X-Conversation-Id": "uji-2"})
        stats2 = get("/v1/sessions")
        print("jumlah percakapan:", stats2["conversations"])
        if stats2["conversations"] != 2:
            failures.append(f"harus 2 percakapan, dapat {stats2['conversations']}")
        ids = {i["chat_session_id"] for i in stats2["items"]}
        if len(ids) != 2:
            failures.append("chat_session_id tidak unik antar percakapan")

        # ---- 3. Tanpa header: sidik jari pesan pertama ----
        print("\n=== tanpa header (sidik jari) ===")
        body3 = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "Percakapan ketiga tanpa header."}],
        }
        post(body3)
        post(body3)
        stats3 = get("/v1/sessions")
        print("jumlah percakapan:", stats3["conversations"])
        if stats3["conversations"] != 3:
            failures.append(
                f"request identik tanpa header harus 1 percakapan baru, "
                f"dapat {stats3['conversations']}"
            )

        # ---- 4. Field "user" ala OpenAI ----
        print("\n=== field user ===")
        post({
            "model": "deepseek-chat",
            "user": "agen-abc",
            "messages": [{"role": "user", "content": "Halo dari user A."}],
        })
        post({
            "model": "deepseek-chat",
            "user": "agen-abc",
            "messages": [{"role": "user", "content": "Pesan kedua user A."}],
        })
        stats4 = get("/v1/sessions")
        print("jumlah percakapan:", stats4["conversations"])
        if stats4["conversations"] != 4:
            failures.append(
                f"dua request dengan user sama harus 1 percakapan, "
                f"dapat {stats4['conversations']}"
            )

    finally:
        proc.kill()
        proc.wait()

    print()
    if failures:
        print("GAGAL:", failures)
        return 1
    print("SEMUA LULUS - sesi dipakai ulang dengan benar")
    return 0


sys.exit(main())
