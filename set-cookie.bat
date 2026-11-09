@echo off
REM ============================================================
REM  set-cookie.bat - Simpan cookie browser DeepSeek ke cookie.txt
REM
REM  Cookie WAF dibutuhkan agar endpoint PoW (create_pow_challenge) tidak
REM  diblokir AWS WAF (HTTP 202).
REM
REM  Ambil cookie dari browser:
REM    1. Buka https://chat.deepseek.com di browser (login)
REM    2. DevTools (F12) -> Application -> Cookies -> https://chat.deepseek.com
REM    3. Salin value cookie "aws-waf-token"
REM    4. Jalankan file ini dan tempel nilai tersebut
REM
REM  Alternatif: salin SELURUH header Cookie dari request
REM  (Network tab -> request -> Request Headers -> cookie)
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo   SIMPAN COOKIE BROWSER
echo ============================================================
echo.
echo   Di browser:
echo     1. Buka https://chat.deepseek.com ^& login
echo     2. F12 -^> Application -^> Cookies -^> https://chat.deepseek.com
echo     3. Salin VALUE cookie "aws-waf-token"
echo.
echo   ^(Boleh juga salin seluruh header Cookie, misal:^
echo    "aws-waf-token=...; ds_session_id=..."^)
echo.

set "COOKIE="
set /p "COOKIE=Tempel cookie di sini: "

if not defined COOKIE (
    echo.
    echo [GAGAL] Cookie kosong.
    pause
    exit /b 1
)

REM --- Simpan lewat Python agar tidak ada karakter yang hilang -----------
"%PY%" -c "import sys,pathlib; t=sys.argv[1].strip(); pathlib.Path('cookie.txt').write_text(t, encoding='utf-8'); print('[OK] Tersimpan ke cookie.txt, panjang', len(t))" "!COOKIE!"
if errorlevel 1 (
    echo.
    echo [GAGAL] Gagal menyimpan cookie.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   COOKIE TERSIMPAN di cookie.txt
echo ============================================================
echo.
echo   Client memuatnya otomatis - tidak perlu set env.
echo   Verifikasi dengan: test.bat
echo.
pause
endlocal
