"""Uji penghematan prompt: ringkas tool + pangkas riwayat."""

import io
import json
import os
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
import deepseek_web_api.server as S
from deepseek_web_api.tools import build_tool_prompt, compact_tools

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:52s} {detail}")
    if not cond:
        failures.append(label)


BIG_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": f"tool_{i}",
            "description": "Deskripsi panjang. " * 30,
            "parameters": {
                "type": "object",
                "properties": {
                    f"p{j}": {"type": "string", "description": "Penjelasan. " * 20}
                    for j in range(5)
                },
                "required": ["p0"],
            },
        },
    }
    for i in range(10)
]

# ---------- compact_tools ----------
print("=== compact_tools ===")
compact = compact_tools(BIG_TOOLS)
check("jumlah tool dipertahankan", len(compact) == 10, str(len(compact)))
check("nama tool ada", compact[0]["name"] == "tool_0")
check("description tool dibuang", "description" not in compact[0])
check(
    "description parameter dibuang",
    "description" not in compact[0]["parameters"]["properties"]["p0"],
)
check(
    "tipe parameter dipertahankan",
    compact[0]["parameters"]["properties"]["p0"]["type"] == "string",
)
check("required dipertahankan", compact[0]["parameters"]["required"] == ["p0"])

old_size = len(json.dumps(BIG_TOOLS, ensure_ascii=False, separators=(",", ":")))
new_size = len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))
check("ukuran menyusut >80%", new_size < old_size * 0.2,
      f"{old_size} -> {new_size}")

# DEEPSEEK_TOOL_DESC=1 mengembalikan deskripsi tool
os.environ["DEEPSEEK_TOOL_DESC"] = "1"
try:
    with_desc = compact_tools(BIG_TOOLS)
    check("opsi deskripsi tool", "description" in with_desc[0])
finally:
    os.environ.pop("DEEPSEEK_TOOL_DESC", None)

# enum & items tetap
tools2 = [{
    "type": "function",
    "function": {
        "name": "x",
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["a", "b"]},
                "items": {"type": "array", "items": {"type": "object"}},
            },
        },
    },
}]
c2 = compact_tools(tools2)
check("enum dipertahankan", c2[0]["parameters"]["properties"]["mode"]["enum"] == ["a", "b"])
check("items.type dipertahankan", c2[0]["parameters"]["properties"]["items"]["items"] == "object")

# ---------- build_tool_prompt ----------
print("\n=== build_tool_prompt ===")
p = build_tool_prompt(BIG_TOOLS)
check("prompt jauh lebih kecil dari JSON penuh", len(p) < old_size * 0.25,
      f"{len(p)} vs {old_size}")
check("tetap menyebut format do:", "do:" in p)
check("tanpa tool -> kosong", build_tool_prompt([]) == "")
check("tool_choice none", "JANGAN" in build_tool_prompt(BIG_TOOLS, "none"))

# ---------- pemangkasan riwayat ----------
print("\n=== pemangkasan riwayat ===")
msgs = [{"role": "system", "content": "SISTEM PENTING"}]
for i in range(30):
    msgs.append({"role": "user", "content": f"pesan {i}"})
    msgs.append({"role": "assistant", "content": f"jawaban {i}"})

old_limit = S.DEFAULT_MAX_HISTORY_TURNS
try:
    S.DEFAULT_MAX_HISTORY_TURNS = 4
    _, prompt = S._flatten_messages(msgs)
    check("system prompt utuh", "SISTEM PENTING" in prompt)
    check("pesan terakhir ada", "pesan 29" in prompt)
    check("ada ringkasan riwayat", "[Ringkasan percakapan sebelumnya]" in prompt)
    # Ringkasan memuat inti pesan lama, jadi hitung blok penuh di luar ringkasan.
    body = prompt.split("[Akhir ringkasan", 1)[-1]
    check("pesan lama tidak dikirim penuh", "User: pesan 0" not in body)
    check("giliran dibatasi", body.count("User: pesan") <= 4, f"{body.count('User: pesan')} user")
    check("prompt jauh lebih kecil", len(prompt) < 1500, str(len(prompt)))

    S.DEFAULT_MAX_HISTORY_TURNS = 0  # tanpa batas
    _, full = S._flatten_messages(msgs)
    check("batas 0 = kirim semua", "pesan 0" in full and "[Ringkasan" not in full)
finally:
    S.DEFAULT_MAX_HISTORY_TURNS = old_limit

# ---------- pemotongan tool result ----------
print("\n=== pemotongan hasil tool ===")
old_clip = S.DEFAULT_MAX_TOOL_RESULT_CHARS
try:
    S.DEFAULT_MAX_TOOL_RESULT_CHARS = 100
    msgs2 = [
        {"role": "user", "content": "cek"},
        {"role": "tool", "name": "bash", "content": "X" * 5000},
    ]
    _, p2 = S._flatten_messages(msgs2)
    check("hasil tool dipotong", len(p2) < 1000, str(len(p2)))
    check("ada penanda potong", "dipotong" in p2)
finally:
    S.DEFAULT_MAX_TOOL_RESULT_CHARS = old_clip

print()
if failures:
    print("GAGAL:", failures)
    sys.exit(1)
print("SEMUA LULUS - penghematan prompt")




