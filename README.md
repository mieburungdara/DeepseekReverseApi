# DeepSeek Web → OpenAI-compatible API

Membongkar `chat.deepseek.com` (antarmuka web) menjadi API yang bisa dipakai
lewat SDK OpenAI. Termasuk solver **Proof-of-Work `DeepSeekHashV1`** yang
dibutuhkan setiap request chat.

> Proyek ini untuk riset/pemakaian pribadi dengan **akun Anda sendiri**.
> Antarmuka web bukan API publik: bisa berubah sewaktu-waktu dan pemakaian
> otomatis berisiko akun diblokir. Untuk produksi, pakai API resmi
> [platform.deepseek.com](https://platform.deepseek.com).

## Isi

| Path | Keterangan |
|---|---|
| `setup.bat` | Setup sekali klik: virtualenv + dependensi |
| `set-token.bat` | **Simpan token ke `token.json`** |
| `login.bat` | Login otomatis (email/password) |
| `start.bat` | Jalankan server (port bisa diatur) |
| `stop.bat` | **Hentikan server & bersihkan proses sisa** |
| `test.bat` | Uji koneksi ke DeepSeek |
| `build.bat` | Kompilasi ulang solver native (opsional) |
| `token.json` | **Token Anda** (dibuat sendiri, tidak di-commit) |
| `deepseek_web_api/keccak23.py` | **Keccak 23 ronde** (hash khas DeepSeek) |
| `deepseek_web_api/pow.py` | Solver `DeepSeekHashV1` (native C / Python) |
| `deepseek_web_api/stream.py` | Parser delta SSE (stateful JSON-Patch) |
| `deepseek_web_api/dsml.py` | Parser tool-call DSML bawaan DeepSeek |
| `deepseek_web_api/tools.py` | Jembatan tool calling → format OpenAI |
| `deepseek_web_api/sessions.py` | Pemetaan percakapan → satu sesi DeepSeek |
| `deepseek_web_api/client.py` | Client async: login, PoW, chat, multi-turn |
| `deepseek_web_api/server.py` | Server OpenAI-compatible |
| `native/deepseek_pow.c` | Solver C (~1,3 juta hash/detik) |
| `docs/REVERSE_ENGINEERING.md` | **Bukti kode & cara menemukannya** |
| `tests/test_offline.py` | Unit test tanpa jaringan |
| `tests/live_probe.py` | Probe ke server asli |

## Catatan solver native

`native/deepseek_pow.dll` disertakan sudah terkompilasi (Windows x64). Bila tidak
cocok dengan CPU Anda atau ingin membangun ulang:

```bat
build.bat
```

Butuh gcc/clang. Tanpa DLL, solver otomatis memakai Python murni yang
**~1000x lebih lambat** (~2 menit per challenge vs 0,11 detik).

## Status verifikasi

Semua ini **diuji terhadap server DeepSeek sungguhan**, bukan hanya dari kode:

| Uji | Hasil |
|---|---|
| PoW `DeepSeekHashV1` (difficulty 144000) | ✅ ~0,11 detik |
| Chat non-streaming | ✅ |
| Chat streaming | ✅ chunk demi chunk |
| Multi-turn (konteks tersambung) | ✅ |
| Server `/v1/chat/completions` | ✅ stream & non-stream |

> **Penting:** hash DeepSeek **bukan** SHA3-256 standar — ia memakai Keccak
> **23 ronde** (`RC[1..23]`) dan prefix `salt_expire_at_`. Lihat
> `docs/REVERSE_ENGINEERING.md` §2.

## Instalasi

### Cara cepat (Windows)

Klik dua kali atau jalankan berurutan:

```bat
setup.bat     :: sekali saja - buat .venv + install dependensi + cek
set-token.bat :: simpan token DeepSeek ke token.json
start.bat     :: jalankan server di http://127.0.0.1:17395
```

`start.bat` menerima argumen port:

```bat
start.bat              :: port default 17395
start.bat 19999        :: port tertentu
start.bat auto         :: cari port bebas otomatis bila bentrok
```

`test.bat` menjalankan probe koneksi sungguhan (token, PoW, streaming chat).

### Cara manual

```bash
pip install -r requirements.txt
```

## 1. Ambil token

### Cara paling mudah: `set-token.bat`

```bat
set-token.bat
```

Lalu tempel token saat diminta. Script ini juga menerima JSON lengkap hasil
copy-paste dari DevTools.

**Dari mana token-nya?**
1. Login di `https://chat.deepseek.com`
2. Tekan **F12** → tab **Application**
3. **Local Storage** → `https://chat.deepseek.com`
4. Cari key **`userToken`**, salin nilai **`value`**-nya

### Di mana file token diletakkan?

Letakkan di **folder proyek**, sejajar dengan `start.bat`:

```
DeepseekReverseApi\
├── start.bat
├── token.json          <-- di sini
└── deepseek_web_api\
```

Isinya cukup:

```json
{
  "token": "token-panjang-dari-browser"
}
```

Tiga format ini **semuanya diterima**:

| Isi `token.json` | Keterangan |
|---|---|
| `{"token": "abc123"}` | format utama |
| `{"value": "abc123", "__version": "0"}` | tempelan mentah dari DevTools |
| `abc123` | token mentah, satu baris saja |

> `token.json` sudah masuk `.gitignore`, jadi tidak akan ter-commit.

### Alternatif: login otomatis

```bash
python -m deepseek_web_api.login --save token.json
# email & password ditanyakan interaktif
```

### Alternatif: environment variable

```powershell
$env:DEEPSEEK_TOKEN="<token>"
```

Urutan prioritas yang dibaca `start.bat`: `token.json` → `DEEPSEEK_TOKEN`.

## 2. Pakai sebagai library

```python
import asyncio
from deepseek_web_api import DeepSeekWebClient

async def main():
    async with DeepSeekWebClient(token_file="token.json") as client:
        # sekali jalan
        print(await client.chat("Jelaskan rekursi dalam satu paragraf."))

        # streaming
        async for chunk in client.stream_text("Tulis puisi pendek tentang senja."):
            print(chunk, end="", flush=True)

asyncio.run(main())
```

Mode Expert (reasoning) dan web search:

```python
async for chunk in client.stream_text(
    "Bukti bahwa akar 2 irasional.",
    model_type="expert",
    thinking_enabled=True,
    search_enabled=True,
):
    print(chunk, end="")
```

## 3. Pakai sebagai server OpenAI-compatible

Port default **17395** — dipilih karena berada di luar rentang ephemeral Windows
(49152–65535, tempat koneksi keluar bisa "mencuri" port) dan tidak dipakai
layanan umum mana pun.

```bash
# PowerShell
$env:DEEPSEEK_TOKEN_FILE="token.json"
python -m deepseek_web_api.server            # http://127.0.0.1:17395
```

### API key

Banyak agen AI (Open WebUI, LobeChat, Cursor, Continue, dll.) **mewajibkan**
kolom API key diisi. Gateway ini punya tiga mode:

| Mode | Perilaku | Kapan dipakai |
|---|---|---|
| `open` *(default)* | Key **apa pun** diterima, bahkan tanpa key | Uji coba lokal |
| `any` | Key apa pun diterima **asalkan tidak kosong** | Ingin agen tetap "login" |
| `strict` | Harus sama dengan `DEEPSEEK_API_KEY` | Server diekspos ke jaringan |

**Jadi pada mode default, isi kolom API key dengan string apa saja** — misal
`sk-lokal`, `123`, atau `bebas`. Semua berhasil.

```bat
python -m deepseek_web_api.server                        :: mode open
python -m deepseek_web_api.server --auth any             :: wajib ada key
python -m deepseek_web_api.server --auth strict          :: butuh DEEPSEEK_API_KEY
```

Untuk mode `strict`:

```powershell
$env:DEEPSEEK_API_KEY="sk-rahasia-saya"
python -m deepseek_web_api.server --auth strict
```

Header yang dikenali: `Authorization: Bearer <key>`, `Authorization: <key>`,
`x-api-key: <key>`, `api-key: <key>`.

Cek cepat apakah key Anda diterima:

```bash
curl -H "Authorization: Bearer apa-saja" http://127.0.0.1:17395/v1/auth/check
```

Opsi port:

```bash
python -m deepseek_web_api.server --port 19999   # port lain
python -m deepseek_web_api.server --auto-port    # geser otomatis bila bentrok
$env:DEEPSEEK_PORT="18500"                       # lewat environment
```

### Menghentikan server & membersihkan proses sisa

```bat
stop.bat
```

`start.bat` menjalankan Python **langsung** — satu proses saja (~49 MB) — dan
memasang *watchdog*: bila jendela terminal ditutup, bahkan dibunuh paksa,
server mematikan dirinya sendiri dalam ~2 detik sehingga port tidak tertahan.

Bila Anda menjalankan `python -m deepseek_web_api.server` manual dan jendelanya
hilang tanpa shutdown, jalankan `stop.bat`. Skrip itu **hanya** menghentikan
proses `deepseek_web_api.server`, bukan Python lain milik Anda.

> `.venv\Scripts\python.exe` adalah *launcher* yang men-spawn proses anak.
> `start.bat` sengaja memakai `python.exe` asli di dalam venv supaya hanya ada
> satu proses dan tidak ada yang tertinggal.

Lalu arahkan SDK OpenAI biasa ke server lokal:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:17395/v1",
    api_key="sk-lokal",          # string apa pun, mode open
)

resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "Halo!"}],
)
print(resp.choices[0].message.content)
```

Pemetaan model:

| nama OpenAI-style | `model_type` web | mode |
|---|---|---|
| `deepseek-chat` / `deepseek-instant` | `default` | Instant |
| `deepseek-reasoner` / `deepseek-expert` | `expert` | Expert (thinking) |
| `deepseek-vision` | `vision` | Vision |

> Catatan: endpoint web menerima **satu string `prompt`**, bukan array pesan.
> Server ini melipat riwayat percakapan menjadi satu transkrip berlabel
> (`User:` / `Assistant:`). Untuk percakapan multi-turn yang benar-benar
> stateful, gunakan `chat_session_id` di library.

## 4. Uji

```bash
python -m tests.test_offline     # tanpa jaringan, 20 assertion
python -m tests.live_probe       # ke server asli; butuh DEEPSEEK_TOKEN
```

## Efisiensi prompt

Endpoint web menerima **satu string** prompt, jadi seluruh riwayat dan definisi
tool ikut dikirim setiap request. Tiga lapis optimasi:

### 1. Delta adaptif (default) — penghematan terbesar

DeepSeek menyimpan konteks di sesinya, jadi giliran lanjutan **cukup mengirim
pesan baru**. Riwayat penuh hanya dikirim saat "seed":

- sesi DeepSeek baru dibuat,
- server baru restart (peta sesi hilang),
- klien mengganti/mengulang riwayatnya.

Deteksinya memakai sidik jari urutan pesan, jadi aman: bila klien mengubah
riwayat, gateway otomatis kembali mengirim penuh.

```bash
$env:DEEPSEEK_PROMPT_MODE="full"    # matikan delta, selalu kirim riwayat
```

### 2. Definisi tool diringkas

Deskripsi panjang dan JSON Schema verbose dibuang; tersisa nama tool, nama
parameter, tipe, `enum`, dan `required`. Terukur pada 32 tool asli:

| | Ukuran | Porsi prompt |
|---|---|---|
| Verbose | 32.401 char | 43,6% |
| Ringkas | 5.450 char | 7,3% |

`DEEPSEEK_TOOL_DESC=1` mengembalikan deskripsi bila model sering salah panggil.

### 3. Riwayat dipangkas saat seed

Saat mengirim penuh, hanya `DEEPSEEK_MAX_HISTORY_TURNS` (default 12) pesan
terakhir yang utuh; sisanya menjadi ringkasan. Hasil tool dipotong pada
`DEEPSEEK_MAX_TOOL_RESULT` karakter (default 4000).

### Hasil terukur (`contoh.txt`: 28 pesan, 32 tool)

| Strategi | Char | Token~ | Hemat |
|---|---|---|---|
| Lama (riwayat penuh + tool verbose) | 55.926 | 13.981 | — |
| Sebelumnya (riwayat 12 + tool ringkas) | 29.272 | 7.318 | 47,7% |
| **Delta + tool ringkas (default)** | **13.245** | **3.311** | **76,3%** |

System prompt **selalu** disertakan pada giliran delta — murah dan menjaga
aturan tetap aktif.

```bash
$env:DEEPSEEK_MAX_HISTORY_TURNS="20"   # kirim lebih banyak riwayat saat seed
$env:DEEPSEEK_MAX_TOOL_RESULT="8000"   # potong hasil tool lebih longgar
```

> Catatan: system prompt tidak pernah diduplikasi — muncul sekali per request.
> Yang membengkak adalah definisi tool dan riwayat percakapan.

## Sesi percakapan

Satu percakapan klien memakai **satu** `chat_session_id` DeepSeek. Sebelumnya
setiap request membuat sesi baru, sehingga riwayat menumpuk di server DeepSeek
dan model kehilangan konteks antar giliran.

Penentuan percakapan, berurutan:

1. Header **`X-Conversation-Id`** (atau `X-Session-Id`) — paling andal.
2. Field **`user`** pada body (konvensi OpenAI).
3. **Sidik jari** dari pesan sistem + pesan pengguna pertama.

`parent_message_id` juga dilacak otomatis: id pesan asisten terakhir dikirim
sebagai induk pada giliran berikutnya, sehingga server DeepSeek menyambung
percakapan.

```python
# klien yang sama -> sesi yang sama
client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "Nama saya Budi."}],
    extra_headers={"X-Conversation-Id": "chat-123"},
)
client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "Siapa nama saya?"}],
    extra_headers={"X-Conversation-Id": "chat-123"},
)
# -> "Nama Anda Budi!"
```

Periksa dan bersihkan pemetaan sesi:

```bash
curl -H "Authorization: Bearer x" http://127.0.0.1:17395/v1/sessions
curl -X DELETE -H "Authorization: Bearer x" http://127.0.0.1:17395/v1/sessions
```

Pemetaan disimpan di memori (default maksimum 200 percakapan, kedaluwarsa
setelah 6 jam). Menghapusnya **tidak** menghapus riwayat di akun DeepSeek Anda.

## Tool calling (function calling)

DeepSeek web tidak punya API tool resmi, jadi gateway ini menjembatani:

1. Definisi `tools` dari request disuntikkan ke prompt.
2. Model diminta membalas `do: {"name":...,"arguments":{...}}`.
3. Bila model tetap mengeluarkan format internal **DSML** (seperti
   `<｜｜DSML｜｜ invoke name="bash">`), gateway mem-parse-nya juga.
4. Hasilnya dikembalikan sebagai `tool_calls` OpenAI yang sah, dengan
   `finish_reason: "tool_calls"`.

Berlaku untuk **non-streaming maupun streaming**. Riwayat `role: "tool"` dan
`tool_calls` asisten ikut dipadatkan dengan benar, jadi agen bisa melakukan
beberapa putaran tool.

```python
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "Jalankan pwd; ls -la"}],
    tools=[{
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
    }],
)
print(resp.choices[0].finish_reason)            # "tool_calls"
print(resp.choices[0].message.tool_calls[0].function.arguments)
```

`tool_choice` didukung: `"none"`, `"required"`, atau
`{"type":"function","function":{"name":"..."}}`.

> Karena berbasis prompt, kepatuhan model tidak 100%. Bila tool call tidak
> terdeteksi, coba `deepseek-reasoner` (mode expert) atau perjelas deskripsi
> tool-nya.

## Reasoning / thinking (deepseek-reasoner)

Model `deepseek-reasoner` memakai mode **expert** DeepSeek dan menghasilkan
proses berpikir (fragmen `THINK`) sebelum jawaban. Gateway mengeksposnya
sebagai **`reasoning_content`**, konvensi DeepSeek API resmi.

| Model | `model_type` | Fragmen | `reasoning_content` |
|---|---|---|---|
| `deepseek-chat` | `default` | `RESPONSE` | kosong |
| `deepseek-reasoner` | `expert` | `THINK`, `RESPONSE` | **terisi** |

Non-streaming:

```python
r = client.chat.completions.create(
    model="deepseek-reasoner",
    messages=[{"role": "user", "content": "Hitung 17*23, tunjukkan langkah."}],
)
print(r.choices[0].message.reasoning_content)   # proses berpikir
print(r.choices[0].message.content)             # jawaban akhir
```

Streaming — delta membawa `reasoning_content` lebih dulu, lalu `content`:

```python
for ch in client.chat.completions.create(
    model="deepseek-reasoner", messages=[...], stream=True
):
    d = ch.choices[0].delta
    if getattr(d, "reasoning_content", None):
        print("[thinking]", d.reasoning_content)
    elif d.content:
        print(d.content, end="")
```

Lewat library:

```python
turn = await client.chat_turn("Hitung 17*23", chat_session_id=sid)
print(turn.thinking)   # proses berpikir
print(turn.text)       # jawaban
print(turn.response_id)
```

Thinking bisa dimatikan walau memakai model expert:

```python
client.chat.completions.create(
    model="deepseek-chat",
    messages=[...],
    extra_body={"thinking_enabled": False},
)
```

> Catatan: proses berpikir ditulis model dalam bahasa Inggris (internal),
> sedangkan jawabannya mengikuti bahasa pengguna.

## Batasan yang diketahui

- **PoW sekali pakai.** Setiap request chat butuh challenge baru (~0,11 s dengan
  solver native). Memakai ulang jawaban membuat server membalas stream kosong.
- **Rate limit (`Messages too frequent`).** Bila pesan dikirim terlalu rapat,
  DeepSeek membalas event `hint` berisi *"Messages too frequent. Try again
  later."* lalu menutup stream tanpa jawaban. Gateway mengubahnya menjadi
  **HTTP 429** dengan `Retry-After`, bukan "jawaban kosong".
  Atur jeda antar-pesan dengan `DEEPSEEK_MIN_INTERVAL` (default 1,5 detik,
  `0` = matikan).
- **hCaptcha (40303 CHALLENGE_REQUIRED).** DeepSeek kadang meminta verifikasi
  captcha. Gateway melaporkannya sebagai error `429 challenge_required`
  (bukan "jawaban kosong"). Selesaikan captcha di browser, lalu coba lagi.
- **Tool calling berbasis prompt.** DeepSeek web tidak punya API tool resmi,
  jadi definisi tool disuntikkan ke prompt. Kepatuhan model tidak 100% — kalau
  tool call tak terdeteksi, coba `deepseek-reasoner` atau perjelas `description`.
- **`parent_message_id` harus DIHILANGKAN, bukan `null`,** untuk pesan pertama.
  Mengirim `null` membuat server membalas stream kosong (55 byte). Client sudah
  menangani ini.
- **`temperature`, `top_p`, `max_tokens` diabaikan.** UI web tidak mengirimkannya.
- **AWS WAF** dapat memblokir (HTTP 202). Set `DEEPSEEK_COOKIES` dengan cookie
  browser (`aws-waf-token=...`) bila terjadi.
- **Mode delta bergantung pada memori server.** Sesi percakapan disimpan in-memory
  (default 200 percakapan, TTL 6 jam). Restart server => seed ulang (aman,
  otomatis). Set `DEEPSEEK_PROMPT_MODE=full` untuk selalu mengirim riwayat.
## Struktur hasil reverse engineering

Detail lengkap ada di [`docs/REVERSE_ENGINEERING.md`](docs/REVERSE_ENGINEERING.md):
algoritma PoW beserta potongan kode aslinya, format header (base64!), skema
envelope, protokol SSE + JSON-Patch delta, dan peta endpoint.










