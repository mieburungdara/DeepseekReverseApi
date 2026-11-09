"""Uji jembatan tool calling: prompt, parsing respons, dan riwayat pesan."""

import io
import json
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
from deepseek_web_api import tools as T
from deepseek_web_api.server import _flatten_messages

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:46s} {detail}")
    if not cond:
        failures.append(label)


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Jalankan perintah shell",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }
]

# ---------- 1. Prompt tool ----------
print("=== prompt tool ===")
p = T.build_tool_prompt(TOOLS)
check("prompt memuat nama tool", "bash" in p)
check("prompt menjelaskan format do:", "do:" in p)
check("prompt tanpa tool -> kosong", T.build_tool_prompt(None) == "")
check("tool_choice none", "JANGAN" in T.build_tool_prompt(TOOLS, "none"))
check("tool_choice required", "WAJIB" in T.build_tool_prompt(TOOLS, "required"))
check(
    "tool_choice spesifik",
    'WAJIB memanggil alat bernama "bash"' in T.build_tool_prompt(
        TOOLS, {"type": "function", "function": {"name": "bash"}}
    ),
)

# ---------- 2. Parse jawaban DSML (kasus pengguna) ----------
print("\n=== jawaban DSML pengguna ===")
dsml_reply = """

<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="bash">
<｜｜DSML｜｜ parameter name="command" string="true">pwd; ls -la</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>
"""
content, calls = T.parse_response(dsml_reply, TOOLS)
check("DSML -> tool_calls", len(calls) == 1, str(len(calls)))
check("content kosong (bukan markup)", content.strip() == "", repr(content[:40]))
if calls:
    check("nama tool bash", calls[0]["function"]["name"] == "bash")
    args = json.loads(calls[0]["function"]["arguments"])
    check("argumen command benar", args.get("command") == "pwd; ls -la", str(args))

# ---------- 3. Parse format do: ----------
print("\n=== format do: ===")
content, calls = T.parse_response('do: {"name":"bash","arguments":{"command":"ls"}}', TOOLS)
check("do: -> tool_calls", len(calls) == 1)
check("do: content kosong", content == "", repr(content))
if calls:
    check("do: nama", calls[0]["function"]["name"] == "bash")

# do: dengan teks penjelasan
content, calls = T.parse_response(
    'Saya cek dulu.\ndo: {"name":"bash","arguments":{"command":"ls"}}', TOOLS
)
check("do: + teks -> ada call", len(calls) == 1)
check("do: + teks -> teks tersisa", content == "Saya cek dulu.", repr(content))

# beberapa do: sekaligus
content, calls = T.parse_response(
    'do: {"name":"bash","arguments":{"command":"a"}}\n'
    'do: {"name":"bash","arguments":{"command":"b"}}',
    TOOLS,
)
check("dua do: -> dua call", len(calls) == 2, str(len(calls)))

# ---------- 4. Jawaban biasa tidak terganggu ----------
print("\n=== jawaban biasa ===")
content, calls = T.parse_response("Halo! Ada yang bisa saya bantu?", TOOLS)
check("tanpa tool call", calls == [])
check("teks utuh", content == "Halo! Ada yang bisa saya bantu?", repr(content))

# Teks yang kebetulan mengandung "do:" di tengah kalimat
content, calls = T.parse_response("Saya do: yang terbaik.", TOOLS)
check("'do:' di tengah kalimat bukan call", calls == [], str(calls))

# ---------- 5. Parameter string tetap string ----------
print("\n=== tipe parameter ===")
content, calls = T.parse_response(
    'do: {"name":"bash","arguments":{"command":123}}', TOOLS
)
if calls:
    args = json.loads(calls[0]["function"]["arguments"])
    check("command (string) tetap string", args.get("command") == "123", repr(args.get("command")))

# ---------- 6. Riwayat pesan ----------
print("\n=== riwayat pesan ===")
_, prompt = _flatten_messages([
    {"role": "user", "content": "List file"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command":"ls"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "name": "bash", "content": "a.txt\nb.txt"},
    {"role": "user", "content": "Ada berapa file?"},
])
check("riwayat: tool call asisten disertakan", "bash" in prompt and "ls" in prompt, prompt[:100])
check("riwayat: hasil tool berlabel benar", "Tool result (bash)" in prompt, prompt[:200])
check("riwayat: bukan 'User:' untuk tool", "User: a.txt" not in prompt)

# ---------- 7. tool_names & string keys ----------
print("\n=== util ===")
check("tool_names", T.tool_names(TOOLS) == ["bash"])
check("string keys", T.string_param_keys(TOOLS) == {"command"})
check("format_assistant_tool_calls", "do:" in T.format_assistant_tool_calls(
    [{"function": {"name": "bash", "arguments": '{"command":"ls"}'}}]
))

print()
if failures:
    print("GAGAL:", failures)
    sys.exit(1)
print("SEMUA LULUS - jembatan tool calling")
