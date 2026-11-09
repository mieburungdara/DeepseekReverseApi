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

## Batasan yang diketahui

- **PoW sekali pakai.** Setiap request chat butuh challenge baru (~0,11 s dengan
  solver native). Memakai ulang jawaban membuat server membalas stream kosong.
- **Tanpa function calling / tool use.** Protokol web tidak mengeksposnya.
- **`temperature`, `top_p`, `max_tokens` diabaikan.** UI web tidak mengirimkannya.
- **AWS WAF** dapat memblokir (HTTP 202). Set `DEEPSEEK_COOKIES` dengan cookie
  browser (`aws-waf-token=...`) bila terjadi.
- **Multi-turn** butuh `chat_turn()` + `parent_message_id` (integer). Lewat
  server OpenAI-compatible, riwayat dilipat menjadi satu transkrip.

## Struktur hasil reverse engineering

Detail lengkap ada di [`docs/REVERSE_ENGINEERING.md`](docs/REVERSE_ENGINEERING.md):
algoritma PoW beserta potongan kode aslinya, format header (base64!), skema
envelope, protokol SSE + JSON-Patch delta, dan peta endpoint.


