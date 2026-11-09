@echo off
REM ============================================================
REM  stop.bat - Hentikan semua proses server DeepSeek yang tersisa
REM
REM  Gunakan bila port masih terpakai padahal jendela server sudah
REM  ditutup. Skrip ini HANYA menyentuh proses yang menjalankan
REM  `deepseek_web_api.server`, bukan Python lain milik Anda.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   HENTIKAN server DeepSeek
echo ============================================================
echo.

REM --- Kumpulkan PID server lewat PowerShell ---------------------------
set "PIDS="
for /f "usebackq tokens=*" %%P in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*-m deepseek_web_api.server*' -and $_.Name -like '*python*' } | ForEach-Object { $_.ProcessId }"`) do (
    set "PIDS=!PIDS! %%P"
)

if not defined PIDS (
    echo Tidak ada proses server yang berjalan.
) else (
    echo Ditemukan PID:!PIDS!
    echo.
    for %%P in (!PIDS!) do (
        echo Menghentikan PID %%P ...
        taskkill /F /PID %%P >nul 2>&1
        if errorlevel 1 (
            echo   gagal, mungkin sudah berhenti.
        ) else (
            echo   dihentikan.
        )
    )
)

REM --- Tunggu sebentar agar OS melepas port ----------------------------
timeout /t 2 /nobreak >nul

echo.
echo ============================================================
echo   STATUS
echo ============================================================
echo.
powershell -NoProfile -Command ^
  "$ports = 17395,17396,17400,19999; foreach ($p in $ports) { $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; if ($c) { Write-Host ('  port ' + $p + ' : MASIH TERPAKAI oleh PID ' + $c.OwningProcess) } else { Write-Host ('  port ' + $p + ' : bebas') } }"

echo.
pause
endlocal
