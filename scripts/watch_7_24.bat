@echo off
REM Trading Bot — 7/24 AI TRADER (paper)
REM  Her 15 dakikada bir TUR: TARA (tüm Binance USDT perpetual evreni, her 2 turda bir) → ANALİZ (8 uzman ajan / coin)
REM  → SETUP + PLAN (coin yöneticisi) → RİSK (baş yönetici, R/R, kaldıraç tavanı, P(kazanç)) → KAĞIT FUTURES (tetik/stop/TP)
REM  → İZLE → ÖĞREN (her kapanan işlemden neden kâr/zarar) → Obsidian (Scanner, Agents/, Paper Futures, Learning/, Charts/)
REM  Her 4h bar kapanışında ayrıca spot walk-forward döngüsü. Gerçek emir GÖNDERMEZ.
REM Pencereyi kapatmak izlemeyi durdurur. Çökerse 60 sn sonra kendini yeniden başlatır.
chcp 65001 >nul
cd /d "%~dp0.."
:loop
python -m tradingbot watch --interval 15 --scan-every 2 %*
echo [%date% %time%] watch cikti, 60 sn sonra yeniden baslatiliyor...
timeout /t 60 /nobreak >nul
goto loop
