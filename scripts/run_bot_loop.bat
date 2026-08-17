@echo off
REM Trading Bot — sürekli döngü: her 240 dakikada bir (4h bar) yeniden analiz eder.
REM Pencereyi kapatmak botu durdurur. Ctrl+C ile de durur.
chcp 65001 >nul
cd /d "%~dp0.."
python -m tradingbot run --loop 240 %*
