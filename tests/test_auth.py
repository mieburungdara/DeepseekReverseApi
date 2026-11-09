"""Uji lapisan API key pada ketiga mode (open / any / strict)."""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PY = r".venv\Scripts\python.exe"


def start_server(port: int, env_extra: dict, auth_flag: str = ""):
    env = os.environ.copy()
    env.update(env_extra)
    args = [PY, "-m", "uvicorn", "deepseek_web_api.server:app",
            "--host", "127.0.0.1", "--port", str(port)]
    proc = subprocess.Popen(
        args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1).read()
            return proc
        except Exception:
            time.sleep(0.3)
    proc.kill()
    raise RuntimeError("server tidak siap")


def call(port: int, path: str, headers: dict | None = None, method="GET"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def show(label, status, body):
    ok = "OK " if status == 200 else "401"
    msg = ""
    if status != 200 and isinstance(body, dict):
        msg = (body.get("error") or {}).get("message", "")[:60]
    print(f"  [{ok}] {label:52s} {msg}")


def main() -> int:
    failures = []

    # ---------- mode open ----------
    print("=== mode: open (default) ===")
    p = start_server(17501, {"DEEPSEEK_AUTH_MODE": "open"})
    try:
        cases = [
            ("tanpa header", {}),
            ("Bearer sk-lokal", {"Authorization": "Bearer sk-lokal"}),
            ("Bearer apa-saja-123", {"Authorization": "Bearer apa-saja-123"}),
            ("x-api-key: kunci-bebas", {"x-api-key": "kunci-bebas"}),
            ("api-key: lain", {"api-key": "lain"}),
            ("Authorization tanpa Bearer", {"Authorization": "rawkey"}),
            ("Bearer kosong", {"Authorization": "Bearer "}),
        ]
        for label, h in cases:
            s, b = call(17501, "/v1/models", h)
            show(label, s, b)
            if s != 200:
                failures.append(f"open/{label} -> {s}")
        # chat endpoint juga harus lolos auth
        s, b = call(17501, "/v1/auth/check", {"Authorization": "Bearer x"})
        show("auth/check", s, b)
        if s != 200:
            failures.append("open/auth-check")
    finally:
        p.kill(); p.wait()

    # ---------- mode any ----------
    print("\n=== mode: any (key wajib, isi bebas) ===")
    p = start_server(17502, {"DEEPSEEK_AUTH_MODE": "any"})
    try:
        s, b = call(17502, "/v1/models", {})
        show("tanpa header (harus 401)", s, b)
        if s != 401:
            failures.append("any/tanpa-header seharusnya 401")
        s, b = call(17502, "/v1/models", {"Authorization": "Bearer bebas"})
        show("Bearer bebas (harus 200)", s, b)
        if s != 200:
            failures.append("any/bearer seharusnya 200")
    finally:
        p.kill(); p.wait()

    # ---------- mode strict ----------
    print("\n=== mode: strict (harus sama) ===")
    p = start_server(17503, {
        "DEEPSEEK_AUTH_MODE": "strict",
        "DEEPSEEK_API_KEY": "sk-rahasia-123",
    })
    try:
        s, b = call(17503, "/v1/models", {})
        show("tanpa header (harus 401)", s, b)
        if s != 401:
            failures.append("strict/tanpa-header")
        s, b = call(17503, "/v1/models", {"Authorization": "Bearer salah"})
        show("key salah (harus 401)", s, b)
        if s != 401:
            failures.append("strict/key-salah")
        s, b = call(17503, "/v1/models", {"Authorization": "Bearer sk-rahasia-123"})
        show("key benar (harus 200)", s, b)
        if s != 200:
            failures.append("strict/key-benar")
        s, b = call(17503, "/v1/models", {"x-api-key": "sk-rahasia-123"})
        show("key benar via x-api-key (200)", s, b)
        if s != 200:
            failures.append("strict/x-api-key")
    finally:
        p.kill(); p.wait()

    # ---------- endpoint model tunggal ----------
    print("\n=== endpoint /v1/models/{id} ===")
    p = start_server(17504, {"DEEPSEEK_AUTH_MODE": "open"})
    try:
        s, b = call(17504, "/v1/models/deepseek-chat", {"Authorization": "Bearer x"})
        show("model valid", s, b)
        if s != 200:
            failures.append("model-valid")
        s, b = call(17504, "/v1/models/tidak-ada", {"Authorization": "Bearer x"})
        print(f"  [404] model tidak dikenal -> {s} (harus 404)")
        if s != 404:
            failures.append("model-tidak-ada")
    finally:
        p.kill(); p.wait()

    print()
    if failures:
        print("GAGAL:", failures)
        return 1
    print("SEMUA MODE API KEY LULUS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
