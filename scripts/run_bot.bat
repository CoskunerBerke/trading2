@echo off
REM Trading Bot — tek döngü (veri -> analiz -> karar -> kağıt işlem -> Obsidian)
REM Görev Zamanlayıcı ile 4 saatte bir (bar kapanışından ~2 dk sonra) çalıştırılabilir.
chcp 65001 >nul
cd /d "%~dp0.."
python -m tradingbot run %*
if errorlevel 1 (
  echo.
  echo [HATA] Bot hata ile bitti. Yukaridaki logu kontrol et.
)
