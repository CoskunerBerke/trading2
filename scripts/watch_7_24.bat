@echo off
REM Trading Bot — 7/24 İZLEME
REM  * Her 15 dakikada: tüm coinler için uzman ajanlar + coin yöneticileri + baş yönetici (TradingView + Binance canlı)
REM  * Her 4h bar kapanışında: tam döngü (walk-forward backtest, spot karar, kağıt portföy)
REM  * Sonuçlar Obsidian kasasına (Agents/, Coins/, Dashboard, canvas) yazılır; karar değişiklikleri Agents/Alarmlar.md'ye düşer
REM Pencereyi kapatmak izlemeyi durdurur. Bilgisayar açık kaldığı sürece çalışır.
chcp 65001 >nul
cd /d "%~dp0.."
:loop
python -m tradingbot watch --interval 15 %*
echo [%date% %time%] watch cikti, 60 sn sonra yeniden baslatiliyor...
timeout /t 60 /nobreak >nul
goto loop
