"""Parser tool-call DSML milik DeepSeek.

Model DeepSeek web (yang tidak punya API tool resmi) mengeluarkan tool call
dalam format internal "DSML". Bentuknya bisa bervariasi karena tokenizer
menghasilkan karakter pipa penuh (U+FF5C) atau ASCII:

    <｜｜DSML｜｜ calls>
    <｜｜DSML｜｜ invoke name="bash">
    <｜｜DSML｜｜ parameter name="command" string="true">pwd; ls -la</｜｜DSML｜｜ parameter>
    </｜｜DSML｜｜ invoke>
    </｜｜DSML｜｜ calls>

Varian lain yang terlihat di lapangan:

    <|DSML|tool_calls> ... </|DSML|tool_calls>
    <dsml-tool_calls> ... </dsml-tool_calls>
    <tool_calls> ... </tool_calls>          (tanpa prefix DSML)
    <invoke name="x"> ... </invoke>         (tanpa wrapper, model kadang lupa)

Modul ini menormalkan semuanya, mem-parse, lalu mengembalikan:
  * ``tool_calls`` dalam bentuk OpenAI (id, type=function, function.name/arguments)
  * ``content`` yang sudah dibersihkan dari markup
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

#: Karakter pipa yang dipakai DeepSeek: ASCII '|' dan full-width U+FF5C.
_PIPES = "|｜"
#: Karakter yang dianggap "noise" di dalam nama tag DSML.
_NS_CHARS = set("|｜ \t\r\n")

#: Pola tag pembuka/penutup DSML, dinormalkan menjadi nama tag polos.
#: Toleran terhadap variasi nyata:
#:   <｜｜DSML｜｜ calls>      (pipa dobel + SPASI sebelum nama tag)
#:   <|DSML|tool_calls>     (pipa tunggal, tanpa spasi)
#:   <dsml-invoke name="x"> (gaya hyphen)
#:   <invoke name="x">      (tanpa prefix)
_TAG_RE = re.compile(
    r"<(?P<close>/)?"
    r"(?P<prefix>[<" + _PIPES + r"\s]*)"
    r"(?:dsml[-\s" + _PIPES + r"]*)?"
    r"(?P<name>tool[_-]?calls?|function[_-]?calls?|invoke|parameter|calls?)"
    r"(?P<attrs>[^>]*)>",
    re.IGNORECASE,
)

_CDATA_OPEN = "<![CDATA["
_CDATA_CLOSE = "]]>"

#: Atribut yang tidak boleh dianggap sebagai parameter.
_RESERVED_ATTRS = {"name", "string", "type", "index", "id"}


def normalize_markup(text: str) -> str:
    """Ubah semua varian tag DSML menjadi tag polos ``<invoke>`` dsb.

    Ini memungkinkan parsing dengan satu pola saja.
    """
    if not text:
        return text

    def _repl(m: re.Match[str]) -> str:
        close = "/" if m.group("close") else ""
        name = m.group("name").lower().replace("-", "_")
        if name in ("tool_call", "function_call", "function_calls", "call"):
            name = "tool_calls"
        elif name == "calls":
            name = "tool_calls"
        attrs = m.group("attrs") or ""
        return f"<{close}{name}{attrs}>"

    return _TAG_RE.sub(_repl, text)


def _unescape(text: str) -> str:
    for entity, char in (
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&apos;", "'"),
        ("&amp;", "&"),
    ):
        text = text.replace(entity, char)
    return text


def _coerce(raw: str, string_keys: set[str]) -> Any:
    """Ubah isi parameter menjadi tipe JSON bila masuk akal."""
    value = raw.strip()
    if value.startswith(_CDATA_OPEN):
        if value.endswith(_CDATA_CLOSE):
            value = value[len(_CDATA_OPEN):-len(_CDATA_CLOSE)]
        else:  # CDATA tidak ditutup (model terpotong)
            value = value[len(_CDATA_OPEN):]
        value = value.replace("]]]]><![CDATA[>", "]]>")
        value = _unescape(value)
        # CDATA berarti teks apa adanya, kecuali JSON valid.
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value

    value = _unescape(value)
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        pass

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "none"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


_PARAM_RE = re.compile(
    r"<parameter(?P<attrs>[^>]*)>(?P<value>.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)
_INVOKE_RE = re.compile(
    r"<invoke(?P<attrs>[^>]*)>(?P<body>.*?)</invoke>",
    re.DOTALL | re.IGNORECASE,
)
_TOOLCALLS_RE = re.compile(
    r"<tool_calls[^>]*>(?P<body>.*?)</tool_calls>",
    re.DOTALL | re.IGNORECASE,
)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _attrs(text: str) -> dict[str, str]:
    return {k.lower(): v for k, v in _ATTR_RE.findall(text or "")}


def _parse_params(body: str, string_keys: set[str]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for m in _PARAM_RE.finditer(body):
        attrs = _attrs(m.group("attrs"))
        name = attrs.get("name")
        if not name:
            continue
        value = _coerce(m.group("value"), string_keys)
        if name in args:
            existing = args[name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                args[name] = [existing, value]
        else:
            args[name] = value
    return args


def _make_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def _clean_text(text: str) -> str:
    text = re.sub(r"<tool_calls[^>]*>.*?</tool_calls>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<invoke[^>]*>.*?</invoke>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<parameter[^>]*>.*?</parameter>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<!\[CDATA\[.*?\]\]>", "", text, flags=re.DOTALL)
    text = re.sub(r"\[citation:\d+\]", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _resolve_name(name: str, known: list[str]) -> str:
    """Cocokkan nama tool ke daftar yang dikenal (case/snake insensitive)."""
    if not known:
        return name
    if name in known:
        return name
    for candidate in known:
        if candidate.lower() == name.lower():
            return candidate
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name).lower()
    for candidate in known:
        if candidate.lower() == snake:
            return candidate
    return name


def parse_tool_calls(
    text: str,
    tool_names: Optional[list[str]] = None,
    string_keys: Optional[set[str]] = None,
) -> tuple[list[dict[str, Any]], str]:
    """Parse tool calls DSML dari teks model.

    Return ``(tool_calls, sisa_teks)``. ``tool_calls`` kosong bila tidak ada.
    """
    if not text:
        return [], ""

    string_keys = string_keys or set()
    normalized = normalize_markup(text)
    calls: list[dict[str, Any]] = []

    # 1. Blok <tool_calls> ... </tool_calls>
    blocks = [m.group("body") for m in _TOOLCALLS_RE.finditer(normalized)]
    if not blocks:
        blocks = [normalized]  # model kadang lupa wrapper

    for block in blocks:
        for m in _INVOKE_RE.finditer(block):
            attrs = _attrs(m.group("attrs"))
            name = attrs.get("name")
            if not name:
                continue
            name = _resolve_name(name, tool_names or [])
            args = _parse_params(m.group("body"), string_keys)
            calls.append(_make_call(name, args))

    # 2. Output terpotong: <invoke> tanpa penutup (model berhenti di tengah).
    if not calls:
        calls.extend(_parse_truncated(normalized, tool_names, string_keys))

    return calls, _clean_text(normalized)


def _parse_truncated(
    text: str,
    tool_names: Optional[list[str]],
    string_keys: set[str],
) -> list[dict[str, Any]]:
    """Selamatkan tool call yang terpotong (tanpa tag penutup).

    Ini penting untuk streaming: model kadang berhenti sebelum menutup tag,
    dan tanpa penanganan ini seluruh tool call hilang.
    """
    calls: list[dict[str, Any]] = []

    # <invoke name="x"> ... (tanpa </invoke>)
    for m in re.finditer(
        r"<invoke(?P<attrs>[^>]*)>(?P<body>.*)$",
        text,
        re.DOTALL | re.IGNORECASE,
    ):
        attrs = _attrs(m.group("attrs"))
        name = attrs.get("name")
        if not name:
            continue
        body = m.group("body")
        args: dict[str, Any] = {}

        # Parameter lengkap di dalam body
        args.update(_parse_params(body, string_keys))

        # Parameter terakhir yang belum ditutup
        tail = re.search(
            r"<parameter(?P<attrs>[^>]*)>(?P<value>.*)$",
            body,
            re.DOTALL | re.IGNORECASE,
        )
        if tail:
            tail_attrs = _attrs(tail.group("attrs"))
            pname = tail_attrs.get("name")
            if pname:
                args[pname] = _coerce(tail.group("value"), string_keys)

        calls.append(_make_call(_resolve_name(name, tool_names or []), args))

    return calls


def has_dsml(text: str) -> bool:
    """Cepat: apakah teks mengandung penanda DSML sama sekali?"""
    if not text:
        return False
    return bool(re.search(r"[<｜]\s*[<｜|]*\s*(?:dsml|DSML)\s*[>｜|]", text)) or bool(
        re.search(r"<tool_calls|<invoke\s+name=", text, re.IGNORECASE)
    )


def strip_leaked_markers(text: str) -> str:
    """Buang penanda internal DeepSeek yang bocor ke output."""
    if not text:
        return text
    # <｜begin_of_sentence｜>, <｜end_of_sentence｜>, <｜tool｜>, <｜assistant｜>, dst.
    text = re.sub(
        r"<[｜|]\s*(?:begin|end)[_▁]of[_▁]\w+\s*[｜|]>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"<[｜|]\s*(?:assistant|tool|user|system|thinking)\s*[｜|]>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text
