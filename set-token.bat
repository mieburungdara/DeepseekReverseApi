@echo off
REM ============================================================
REM  set-token.bat - Simpan token DeepSeek ke token.json
REM
REM  Cara pakai:
REM    set-token.bat              -> tempel token saat diminta
REM    set-token.bat <token>      -> langsung dari argumen
REM
REM  Token diambil dari browser:
REM    chat.deepseek.com > F12 > Application > Local Storage
REM    > https://chat.deepseek.com > key "userToken"
REM    Salin nilai "value"-nya (boleh juga tempel JSON utuhnya).
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo   SIMPAN TOKEN DeepSeek
echo ============================================================
echo.
echo   Ambil token dari browser:
echo     1. Buka chat.deepseek.com dan login
echo     2. Tekan F12, buka tab Application
echo     3. Local Storage ^> https://chat.deepseek.com
echo     4. Cari key "userToken", salin nilai "value"-nya
echo.
echo   Boleh tempel token mentah, atau JSON lengkapnya sekaligus.
echo.

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [GAGAL] Python tidak ditemukan. Jalankan setup.bat dulu.
    pause
    exit /b 1
)

if not "%~1"=="" (
    "%PY%" -m deepseek_web_api.save_token "%~1"
) else (
    "%PY%" -m deepseek_web_api.save_token
)

if errorlevel 1 (
    echo.
    echo [GAGAL] Token tidak tersimpan.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   SELESAI
echo ============================================================
echo.
echo   Verifikasi : test.bat
echo   Jalankan   : start.bat
echo.
pause
endlocal
