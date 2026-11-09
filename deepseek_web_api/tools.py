"""Jembatan tool calling: prompt untuk model web + penerjemah ke format OpenAI.

DeepSeek web tidak punya API tool resmi, jadi kita:

1. Menyuntikkan definisi tool ke prompt dengan format yang mudah diparse.
2. Membaca output model, menangkap tool call (DSML atau ``do: {...}``).
3. Mengubahnya menjadi ``tool_calls`` OpenAI yang sah.
4. Menyusun riwayat percakapan agar ``role: "tool"`` dan ``tool_calls``
   asisten tidak hilang saat dipadatkan menjadi satu string prompt.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .dsml import has_dsml, parse_tool_calls, strip_leaked_markers

#: Penanda baris tool call sederhana yang lebih stabil daripada DSML.
DO_PREFIX = "do:"


def tool_names(tools: Optional[list[dict[str, Any]]]) -> list[str]:
    """Ambil daftar nama tool dari definisi gaya OpenAI."""
    names: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def string_param_keys(tools: Optional[list[dict[str, Any]]]) -> set[str]:
    """Kumpulkan nama parameter bertipe string agar tidak salah di-cast."""
    keys: set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(fn, dict):
            continue
        params = fn.get("parameters") or {}
        props = params.get("properties") if isinstance(params, dict) else None
        if not isinstance(props, dict):
            continue
        for name, spec in props.items():
            if isinstance(spec, dict) and spec.get("type") == "string":
                keys.add(name)
    return keys


def build_tool_prompt(
    tools: Optional[list[dict[str, Any]]],
    tool_choice: Any = None,
) -> str:
    """Susun instruksi tool untuk disisipkan ke prompt.

    Return string kosong bila tidak ada tool.
    """
    if not tools:
        return ""

    try:
        definitions = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        definitions = str(tools)

    lines = [
        "[Alat yang tersedia]",
        definitions,
        "",
        "Cara memanggil alat: tulis SATU baris yang dimulai dengan 'do:' diikuti",
        'JSON dengan tepat dua field: "name" dan "arguments". Contoh:',
        'do: {"name":"nama_alat","arguments":{"arg":"nilai"}}',
        "Untuk beberapa panggilan, tulis satu baris 'do:' per panggilan.",
        "Jangan memakai blok kode markdown dan jangan menambahkan penjelasan",
        "pada baris 'do:'. Bila tidak perlu alat, jawab seperti biasa.",
        "Alat dijalankan oleh klien; jangan mengaku sudah menjalankannya.",
    ]

    # tool_choice: "none" melarang, "required" mewajibkan
    if isinstance(tool_choice, str):
        if tool_choice == "none":
            lines.append("JANGAN memanggil alat apa pun pada balasan ini.")
        elif tool_choice == "required":
            lines.append("Kamu WAJIB memanggil minimal satu alat pada balasan ini.")
    elif isinstance(tool_choice, dict):
        fn = tool_choice.get("function") or {}
        name = fn.get("name") if isinstance(fn, dict) else None
        if name:
            lines.append(f'Kamu WAJIB memanggil alat bernama "{name}" pada balasan ini.')

    return "\n".join(lines)


def format_assistant_tool_calls(tool_calls: Any) -> str:
    """Ubah riwayat ``tool_calls`` asisten menjadi teks yang bisa dibaca model."""
    if isinstance(tool_calls, str):
        try:
            tool_calls = json.loads(tool_calls)
        except (json.JSONDecodeError, ValueError):
            return ""
    if not isinstance(tool_calls, list):
        return ""

    lines: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name") or ""
        if not name:
            continue
        args = fn.get("arguments", call.get("arguments", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                args = {"_raw": args}
        if not isinstance(args, dict):
            args = {"_raw": str(args)}
        payload = json.dumps({"name": name, "arguments": args}, ensure_ascii=False)
        lines.append(f"{DO_PREFIX} {payload}")
    return "\n".join(lines)


def parse_response(
    text: str,
    tools: Optional[list[dict[str, Any]]] = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Pisahkan teks jawaban dari tool call.

    Menangani dua format:
      * DSML (``<｜｜DSML｜｜ invoke ...>``) — format bawaan model
      * baris ``do: {...}`` — format yang kita instruksikan

    Return ``(content, tool_calls)``.
    """
    if not text:
        return "", []

    names = tool_names(tools)
    keys = string_param_keys(tools)
    content = strip_leaked_markers(text)

    # 1. Format DSML (model mungkin mengabaikan instruksi kita)
    if has_dsml(content):
        calls, cleaned = parse_tool_calls(content, names, keys)
        if calls:
            return cleaned, calls

    # 2. Format "do: {...}"
    lines = content.splitlines()
    kept: list[str] = []
    calls: list[dict[str, Any]] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith(DO_PREFIX):
            payload = stripped[len(DO_PREFIX):].strip()
            call = _parse_do_payload(payload, names, keys)
            if call:
                calls.append(call)
                continue
        kept.append(line)

    if calls:
        cleaned = "\n".join(kept).strip()
        cleaned = strip_leaked_markers(cleaned)
        return cleaned, calls

    # 3. Tidak ada tool call: pastikan markup DSML tidak bocor ke pengguna
    if has_dsml(content):
        _, cleaned = parse_tool_calls(content, names, keys)
        return cleaned, []

    return content, []


def _parse_do_payload(
    payload: str,
    names: list[str],
    keys: set[str],
) -> Optional[dict[str, Any]]:
    """Parse satu baris ``do: {...}`` menjadi tool_call OpenAI."""
    import uuid

    payload = payload.strip().rstrip("`")
    if not payload.startswith("{"):
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    name = data.get("name") or data.get("tool")
    args = data.get("arguments", data.get("args", {}))
    if not isinstance(name, str) or not name:
        return None

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, ValueError):
            args = {"_raw": args}
    if not isinstance(args, dict):
        args = {"_raw": str(args)}

    # Jaga parameter string tetap string
    for key in list(args):
        if key in keys and args[key] is not None and not isinstance(args[key], str):
            args[key] = str(args[key])

    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }
