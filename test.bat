@echo off
REM ============================================================
REM  test.bat - Uji koneksi ke DeepSeek
REM  Memeriksa token, PoW challenge, dan chat sungguhan.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo   TEST Koneksi DeepSeek
echo ============================================================
echo.

if exist "token.json" (
    set "DEEPSEEK_TOKEN_FILE=token.json"
    echo Memakai token dari token.json
) else if defined DEEPSEEK_TOKEN (
    echo Memakai token dari environment DEEPSEEK_TOKEN
) else (
    echo [GAGAL] Token tidak ditemukan.
    echo.
    echo   Simpan token dulu dengan:  set-token.bat
    echo   Atau set environment  :  set DEEPSEEK_TOKEN=^<token^>
    echo.
    pause
    exit /b 1
)

if exist "cookie.txt" (
    echo Cookie browser dimuat dari cookie.txt
) else (
    echo [PERINGATAN] cookie.txt tidak ada.
    echo   Tanpa cookie, endpoint PoW diblokir AWS WAF ^(HTTP 202^).
    echo   Ambil cookie dari browser dan simpan: set-cookie.bat
    echo.
)

echo Menjalankan probe: solver PoW, auth, dan streaming chat ...
echo.
"%PY%" -m tests.live_probe
set "CODE=%errorlevel%"

echo.
echo ============================================================
if "%CODE%"=="0" (
    echo   BERHASIL - token valid, PoW terpecahkan, streaming jalan.
) else if "%CODE%"=="2" (
    echo   BELUM LENGKAP - token belum ada, alur chat belum diuji.
) else (
    echo   GAGAL - token ditolak atau alur chat bermasalah.
    echo.
    echo   Bila token ditolak ^(INVALID_TOKEN^):
    echo     - Token mungkin sudah kedaluwarsa, ambil ulang dari browser
    echo     - Pastikan menyalin nilai "value", bukan seluruh JSON
) 
echo ============================================================
echo.
pause
REM `pause` menimpa errorlevel, jadi simpan exit code sebelum itu.
endlocal & exit /b %CODE%
