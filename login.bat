@echo off
REM ============================================================
REM  login.bat - Ambil token DeepSeek dan simpan ke token.json
REM
REM  Email dan password diminta oleh Python (bukan oleh .bat),
REM  supaya input tidak tertelan lebih dulu.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo   LOGIN DeepSeek
echo ============================================================
echo.

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [GAGAL] Python tidak ditemukan. Jalankan setup.bat dulu.
    echo.
    pause
    exit /b 1
)

REM --- Cek apakah token sudah ada dan masih valid ---------------------
if exist "token.json" (
    echo Menemukan token.json yang sudah ada.
    choice /C YN /N /M "Periksa dan pakai ulang token ini? [Y/N] "
    if !errorlevel! equ 1 (
        echo.
        echo Memeriksa token ...
        "%PY%" -m deepseek_web_api.login --token-file token.json --check
        if not errorlevel 1 (
            echo.
            echo Token masih valid.
            echo.
            pause
            exit /b 0
        )
        echo.
        echo Token tidak valid atau kedaluwarsa. Silakan login ulang.
        echo.
    )
)

REM --- Login: email + password ditanyakan oleh Python -----------------
echo Masukkan kredensial DeepSeek.
echo.
"%PY%" -m deepseek_web_api.login --save token.json

if errorlevel 1 (
    echo.
    echo ============================================================
    echo   LOGIN GAGAL
    echo ============================================================
    echo.
    echo   Kemungkinan penyebab:
    echo     - Email/password salah
    echo     - Akun butuh verifikasi tambahan ^(captcha/MFA^)
    echo     - Diblokir AWS WAF sementara
    echo.
    echo   Alternatif: ambil token manual dari browser.
    echo     DevTools ^> Application ^> Local Storage ^> chat.deepseek.com
    echo     Cari key "userToken", salin bagian "value"-nya.
    echo     Lalu set:  set DEEPSEEK_TOKEN=^<token^>
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   LOGIN BERHASIL - token disimpan di token.json
echo ============================================================
echo.
echo   Lanjutkan dengan: start.bat
echo.
pause
endlocal
