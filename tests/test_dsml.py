"""Uji parser DSML dengan berbagai bentuk nyata."""

import io
import json
import sys

# Pastikan output UTF-8 (karakter pipa full-width U+FF5C tidak ada di cp1252).
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
from deepseek_web_api.dsml import has_dsml, normalize_markup, parse_tool_calls

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:48s} {detail}")
    if not cond:
        failures.append(label)


# ---- 1. Contoh persis dari pengguna (pipa full-width dobel) ----
raw1 = """

<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="bash">
<｜｜DSML｜｜ parameter name="command" string="true">pwd; ls -la</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>
"""
calls, rest = parse_tool_calls(raw1, tool_names=["bash"])
check("contoh pengguna: 1 tool call", len(calls) == 1, str(len(calls)))
if calls:
    fn = calls[0]["function"]
    check("contoh pengguna: nama tool", fn["name"] == "bash", fn["name"])
    args = json.loads(fn["arguments"])
    check("contoh pengguna: argumen command", args.get("command") == "pwd; ls -la", str(args))
    check("contoh pengguna: id berawalan call_", calls[0]["id"].startswith("call_"))
    check("contoh pengguna: type=function", calls[0]["type"] == "function")
check("contoh pengguna: teks sisa kosong", rest == "", repr(rest))

# ---- 2. Format standar <|DSML|tool_calls> ----
raw2 = '''<|DSML|tool_calls>
<|DSML|invoke name="get_weather">
<|DSML|parameter name="city" string="true">Jakarta</|DSML|parameter>
<|DSML|parameter name="days">3</|DSML|parameter>
</|DSML|invoke>
</|DSML|tool_calls>'''
calls, rest = parse_tool_calls(raw2, tool_names=["get_weather"])
check("format standar: 1 call", len(calls) == 1)
if calls:
    args = json.loads(calls[0]["function"]["arguments"])
    check("format standar: city", args.get("city") == "Jakarta", str(args))
    check("format standar: days jadi int", args.get("days") == 3, str(args))

# ---- 3. Beberapa invoke sekaligus ----
raw3 = """<tool_calls>
<invoke name="read_file"><parameter name="path">a.txt</parameter></invoke>
<invoke name="write_file"><parameter name="path">b.txt</parameter><parameter name="content"><![CDATA[hello
world]]></parameter></invoke>
</tool_calls>"""
calls, rest = parse_tool_calls(raw3, tool_names=["read_file", "write_file"])
check("multi invoke: 2 call", len(calls) == 2, str(len(calls)))
if len(calls) == 2:
    check("multi invoke: nama 1", calls[0]["function"]["name"] == "read_file")
    check("multi invoke: nama 2", calls[1]["function"]["name"] == "write_file")
    a2 = json.loads(calls[1]["function"]["arguments"])
    check("multi invoke: CDATA multiline", a2.get("content") == "hello\nworld", repr(a2.get("content")))

# ---- 4. Tanpa wrapper (model lupa) ----
raw4 = '<invoke name="bash"><parameter name="command">echo hi</parameter></invoke>'
calls, _ = parse_tool_calls(raw4, tool_names=["bash"])
check("tanpa wrapper: tetap terdeteksi", len(calls) == 1)

# ---- 5. Nama tool beda kapital / snake_case ----
raw5 = '<invoke name="ReadFile"><parameter name="path">x</parameter></invoke>'
calls, _ = parse_tool_calls(raw5, tool_names=["read_file"])
check("nama dinormalkan ke snake_case", calls and calls[0]["function"]["name"] == "read_file",
      calls[0]["function"]["name"] if calls else "-")

# ---- 6. Teks biasa (bukan tool call) ----
calls, rest = parse_tool_calls("Halo, ini jawaban biasa.", tool_names=["bash"])
check("teks biasa: tidak ada call", calls == [])
check("teks biasa: teks utuh", rest == "Halo, ini jawaban biasa.", repr(rest))

# ---- 7. Teks + tool call bercampur ----
raw7 = 'Saya akan cek dulu.\n<invoke name="bash"><parameter name="command">ls</parameter></invoke>'
calls, rest = parse_tool_calls(raw7, tool_names=["bash"])
check("campuran: ada call", len(calls) == 1)
check("campuran: teks tersisa", "Saya akan cek dulu." in rest, repr(rest))
check("campuran: markup dibuang", "invoke" not in rest, repr(rest))

# ---- 8. Parameter JSON kompleks ----
raw8 = """<invoke name="edit">
<parameter name="options">{"recursive": true, "depth": 2}</parameter>
</invoke>"""
calls, _ = parse_tool_calls(raw8, tool_names=["edit"])
if calls:
    args = json.loads(calls[0]["function"]["arguments"])
    check("JSON di parameter ter-parse", args.get("options") == {"recursive": True, "depth": 2}, str(args))

# ---- 9. has_dsml ----
check("has_dsml: contoh pengguna", has_dsml(raw1))
check("has_dsml: teks biasa", not has_dsml("halo dunia"))
check("has_dsml: <tool_calls>", has_dsml("<tool_calls>x</tool_calls>"))

# ---- 10. normalize_markup ----
norm = normalize_markup(raw1)
check("normalize: jadi <tool_calls>", "<tool_calls>" in norm, norm.split("\n")[1] if "\n" in norm else norm[:60])
check("normalize: jadi <invoke>", "<invoke name=\"bash\">" in norm)

# ---- 11. Penanda bocor ----
from deepseek_web_api.dsml import strip_leaked_markers
leak = "<｜begin_of_sentence｜>Halo<｜end_of_sentence｜>"
check("strip penanda bocor", strip_leaked_markers(leak) == "Halo", repr(strip_leaked_markers(leak)))

# ---- 12. CDATA tidak ditutup (output terpotong) ----
raw12 = '<invoke name="bash"><parameter name="command"><![CDATA[echo hello'
calls, _ = parse_tool_calls(raw12, tool_names=["bash"])
check("CDATA terpotong: tetap dapat argumen", bool(calls), str(calls)[:80])

print()
if failures:
    print("GAGAL:", failures)
    sys.exit(1)
print("SEMUA LULUS - parser DSML")
