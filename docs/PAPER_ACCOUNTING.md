# PAPER ACCOUNTING (Decimal)

## Futures (`accounting/futures_ledger.py` — FuturesLedgerV2)
- İzole marj, tek yön, sembol başına bir pozisyon; `schema_version: 2`, `futures_ledger.json` v1 **otomatik içe aktarılır** (equity→wallet_balance; history verbatim + legacy anahtarlar).
- **amount_type**: `NOTIONAL` (amount = pozisyon büyüklüğü) | `MARGIN` (amount = teminat) | `QUANTITY`. Komisyon **fill notional** üzerinden.
- 50 USDT / 2x ETH=3000, taker %0.05, step 0.001 (testle doğrulandı): NOTIONAL → qty 0.016, notional 48, margin 24, entry_fee 0.024; +2% → exit_fee 0.02448, brüt 0.96, **net 0.91152**; −2% → net −1.00752. MARGIN → qty 0.033, notional 99, margin 49.5, entry_fee 0.0495; +2% → net 1.88001; −2% → net −2.07801.
- Kurallar: tick/step/min_notional/max_qty (SymbolFilters; futures filtreleri ayrı), `margin + entry_fee ≤ available` yoksa `INSUFFICIENT_MARGIN` (sessiz küçültme yok), leverage bracket/MMR ile likidasyon (`liquidation.py`: ETH 3000 2x mmr 0.4% → 1506.02), liq ücreti + zarar marja kırpılır, funding 00/08/16 UTC settlement — kaçırılan bütün dönemler toplu (LONG öder rate>0), mark fiyat (yoksa last), intrabar high/low varsa stop/TP uçlarla; aynı tikte stop+hedef → **stop** (worst_case), liq > stop; TP1 kısmi kapama sonra **gerçek başa-baş** (gidiş-dönüş komisyon+kayma dahil), MAE/MFE, `bars_held` (bar_advance).
- TradeRecord: legacy anahtarlar + `market_type, amount_type, requested/effective margin+notional, quantity, entry_fee, exit_fee, funding_paid/received, slippage_cost, spread_cost, tax_estimate, gross_pnl, net_pnl, exit_price, liquidation_price, fills`.

## Spot (`accounting/spot_ledger.py`)
FIFO lot, cash/asset/locked, MARKET/LIMIT/STOP/STOP_LIMIT/OCO-benzeri, kısmi dolum, trailing stop, gap-through-stop, komisyon/fee asset, tick/step/min_notional, kayma; short **reddedilir**; `portfolio.json` v1 içe aktarma. Not: legacy spot WFO döngüsü (`run`) hâlâ `portfolio.py` kullanır; v3 SPOT_LONG planları `state/spot_ledger.json`'a yazılır.

## Ücret / funding / vergi ayrımı
`FeeSchedule` (maker/taker, source, verified_at, effective_from, BNB indirim bayrağı) → PAPER'da config tarifesi; `FeeSource` protokolü ile ileride Binance hesap komisyonu. `TaxPolicy` versiyonlu, **varsayılan kapalı**, `status=UNVERIFIED_OR_NOT_EFFECTIVE`; `enabled ∧ manually_confirmed` olmadan 0. Her işlemde vergi tahmini, borsa komisyonu, funding, kayma, spread ve gerçekleşen kâr ayrı alanlarda; `export-tax` UTC + Europe/Istanbul zaman, FIFO lot, policy sürümü ile CSV. Mali müşavir yerine geçmez.
