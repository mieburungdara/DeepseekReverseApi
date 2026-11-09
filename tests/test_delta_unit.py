"""Uji logika delta adaptif (tanpa jaringan)."""

import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
from deepseek_web_api.sessions import SessionStore, messages_fingerprint

failures = []


def check(label, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label:50s} {detail}")
    if not cond:
        failures.append(label)


print("=== keputusan delta ===")
st = SessionStore()

msgs1 = [
    {"role": "system", "content": "S"},
    {"role": "user", "content": "Halo"},
]
# Sesi belum ada -> harus full
ok, out = st.should_send_delta("k", msgs1)
check("sesi belum ada -> full", not ok)

st.put("k", "sess-1")
# Belum seeded -> full
ok, out = st.should_send_delta("k", msgs1)
check("belum seeded -> full", not ok)

# Tandai sudah dikirim
st.mark_sent("k", msgs1)

# Giliran 2: riwayat bertambah -> delta
msgs2 = msgs1 + [
    {"role": "assistant", "content": "Hai!"},
    {"role": "user", "content": "Siapa saya?"},
]
ok, out = st.should_send_delta("k", msgs2)
check("lanjutan -> delta", ok)
check("delta hanya pesan baru", len(out) == 2, str(len(out)))
check("delta isi benar", out[0]["content"] == "Hai!" and out[1]["content"] == "Siapa saya?")

# Riwayat sama persis dengan yang TERAKHIR DICATAT -> full (tidak ada pesan baru).
# Catat dulu msgs2, baru periksa.
st.mark_sent("k", msgs2)
ok, _ = st.should_send_delta("k", msgs2)
check("tanpa pesan baru -> full", not ok)

# Klien mengganti riwayat (bukan lanjutan) -> full
st.mark_sent("k", msgs2)
msgs3 = [
    {"role": "system", "content": "S"},
    {"role": "user", "content": "Pertanyaan sama sekali berbeda"},
]
ok, _ = st.should_send_delta("k", msgs3)
check("riwayat diganti -> full", not ok)

# Setelah riwayat baru dikirim, lanjutan lagi -> delta
st.mark_sent("k", msgs3)
msgs4 = msgs3 + [
    {"role": "assistant", "content": "Jawaban"},
    {"role": "user", "content": "Lanjut"},
]
ok, out = st.should_send_delta("k", msgs4)
check("lanjutan setelah ganti -> delta", ok)
check("delta 2 pesan", len(out) == 2, str(len(out)))

# Sesi baru (store kosong) -> full
st2 = SessionStore()
ok, _ = st2.should_send_delta("k", msgs4)
check("store kosong -> full", not ok)

print("\n=== sidik jari ===")
a = messages_fingerprint([{"role": "user", "content": "x"}])
b = messages_fingerprint([{"role": "user", "content": "x"}])
c = messages_fingerprint([{"role": "user", "content": "y"}])
check("deterministik", a == b)
check("beda isi -> beda sidik", a != c)
check(
    "role memengaruhi",
    messages_fingerprint([{"role": "user", "content": "x"}])
    != messages_fingerprint([{"role": "assistant", "content": "x"}]),
)

print("\n=== system prompt diabaikan saat delta ===")
st3 = SessionStore()
st3.put("k", "s")
st3.mark_sent("k", [{"role": "system", "content": "S"}, {"role": "user", "content": "a"}])
ok, out = st3.should_send_delta(
    "k",
    [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ],
)
check("system tidak ikut delta", ok and all(m["role"] != "system" for m in out), str(out))

print()
if failures:
    print("GAGAL:", failures)
    sys.exit(1)
print("SEMUA LULUS - delta adaptif")
