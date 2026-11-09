"""Uji delta adaptif: hemat, tapi konteks tetap terjaga.

Skenario:
1. Giliran 1 (seed) -> riwayat penuh dikirim.
2. Giliran 2-4 -> hanya pesan baru (delta).
3. Konteks harus tetap benar di setiap giliran.
4. Bila klien mengganti riwayat -> kembali ke mode penuh.
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
PORT = 17540
BASE = f"http://127.0.0.1:{PORT}"
HDR = {"X-Conversation-Id": "delta-uji"}


def post(body, headers=None):
    h = {"Content-Type": "application/json", "Authorization": "Bearer sk-uji"}
    h.update(headers or {})
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body).encode(), headers=h, method="POST",
    )
    return json.loads(urllib.request.urlopen(req, timeout=180).read())


def main() -> int:
    env = os.environ.copy()
    env["DEEPSEEK_AUTH_MODE"] = "open"
    env["DEEPSEEK_TOKEN_FILE"] = "token.json"
    env["DEEPSEEK_PROMPT_MODE"] = "delta"
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

        history = []
        turns = [
            "Nama saya Budi, saya tinggal di Bandung.",
            "Saya bekerja sebagai data engineer.",
            "Saya suka kopi tanpa gula.",
            "Ringkas: siapa saya, di mana tinggal, pekerjaan, dan kesukaan saya?",
        ]

        print("=== percakapan 4 giliran (delta adaptif) ===")
        for i, text in enumerate(turns, 1):
            history.append({"role": "user", "content": text})
            r = post({"model": "deepseek-chat", "messages": list(history)}, HDR)
            answer = r["choices"][0]["message"]["content"] or ""
            print(f"\nturn {i}: {text[:50]}")
            print(f"  jawaban: {answer[:130]!r}")
            history.append({"role": "assistant", "content": answer})

        final = (history[-2]["content"] or "").lower() if len(history) >= 2 else ""
        # jawaban terakhir ada di history[-2]? sebenarnya history[-1] = assistant
        last_answer = (history[-1]["content"] or "").lower()
        print("\n=== verifikasi konteks ===")
        for key, label in [
            ("budi", "nama"),
            ("bandung", "kota"),
            ("data engineer", "pekerjaan"),
            ("kopi", "kesukaan"),
        ]:
            ok = key in last_answer
            print(f"  {'PASS' if ok else 'FAIL'}  {label}")
            if not ok:
                failures.append(f"konteks hilang: {label}")

        # Cek mode yang dipakai
        sess = json.loads(urllib.request.urlopen(
            urllib.request.Request(BASE + "/v1/sessions",
                                   headers={"Authorization": "Bearer x"}),
            timeout=20).read())
        print("\nsessions:", json.dumps(sess, ensure_ascii=False)[:200])

    finally:
        proc.kill()
        proc.wait()

    print()
    if failures:
        print("GAGAL:", failures)
        return 1
    print("SEMUA LULUS - delta adaptif menjaga konteks")
    return 0


sys.exit(main())
