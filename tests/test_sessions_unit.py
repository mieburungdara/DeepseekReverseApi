"""Uji unit SessionStore dan penentuan kunci percakapan (tanpa jaringan)."""

import io
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
from deepseek_web_api.sessions import SessionStore, conversation_key

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:50s} {detail}")
    if not cond:
        failures.append(label)


# ---------- conversation_key ----------
print("=== penentuan kunci percakapan ===")
b1 = {"messages": [{"role": "user", "content": "Halo"}]}
check("header eksplisit menang", conversation_key(b1, {"x-conversation-id": "abc"}) == "hdr:abc")
check("header alternatif", conversation_key(b1, {"x-session-id": "xyz"}) == "hdr:xyz")
check(
    "field user dipakai",
    conversation_key({"user": "u1", "messages": b1["messages"]}, {}).startswith("user:"),
)
check(
    "tanpa penanda -> sidik jari",
    conversation_key(b1, {}).startswith("fp:"),
)
# deterministik
k1 = conversation_key(b1, {})
k2 = conversation_key(b1, {})
check("sidik jari deterministik", k1 == k2)
# percakapan berbeda -> kunci berbeda
k3 = conversation_key({"messages": [{"role": "user", "content": "Beda"}]}, {})
check("pesan berbeda -> kunci berbeda", k1 != k3)
# system prompt memengaruhi sidik jari
k4 = conversation_key(
    {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "Halo"}]},
    {},
)
check("system prompt ikut sidik jari", k4 != k1)
# header kosong diabaikan
check(
    "header kosong diabaikan",
    conversation_key(b1, {"x-conversation-id": "  "}).startswith("fp:"),
)

# ---------- SessionStore ----------
print("\n=== SessionStore ===")
st = SessionStore(max_conversations=3, ttl_seconds=1)

c = st.put("k1", "sess-1")
check("put membuat entri", st.get("k1") is not None)
check("turns awal 1", c.turns == 1, str(c.turns))

st.touch("k1", parent_message_id=5)
c = st.get("k1")
check("touch menaikkan turns", c.turns == 2, str(c.turns))
check("touch menyimpan parent", c.parent_message_id == 5, str(c.parent_message_id))

st.put("k2", "sess-2")
st.put("k3", "sess-3")
check("tiga entri", st.stats()["conversations"] == 3)

# melebihi batas -> yang paling lama dibuang
st.put("k4", "sess-4")
check("eviksi saat melebihi max", st.stats()["conversations"] == 3, str(st.stats()["conversations"]))

# TTL
st2 = SessionStore(max_conversations=10, ttl_seconds=0.2)
st2.put("x", "s")
time.sleep(0.35)
check("entri kedaluwarsa dibuang", st2.get("x") is None)

# forget & clear
st.forget("k2")
check("forget menghapus", st.get("k2") is None)
st.clear()
check("clear mengosongkan", st.stats()["conversations"] == 0)

# parent None tidak menimpa
st3 = SessionStore()
st3.put("a", "s", parent_message_id=7)
st3.touch("a", parent_message_id=None)
check("parent None tidak menimpa", st3.get("a").parent_message_id == 7)

print()
if failures:
    print("GAGAL:", failures)
    sys.exit(1)
print("SEMUA LULUS - SessionStore")
