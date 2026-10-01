"""FORMASYON PAPER TRADER V1 (2026-09-16) — yeni listelenen coin öncelikli, 15m/1h/4h mum formasyonu, koşullu plan, AYRI PAPER defteri.

Zincir: keşif (resmi USDⓈ-M exchangeInfo) → veri (MarketFeed, kapalı barlar, artımlı önbellek) → bulgu (mevcut
`learn.candle_context` şekilleri + bağlam) → plan (protokol katalogu, sürümlü) → tetik (kapalı bar) → risk (ortak
`RiskEngine`) → PAPER giriş (`strategy_paper.apply_action`, gerçek `FuturesLedgerV2`) → yönetim (stop/hedef/zaman) →
kapanış → rapor (maliyet sonrası, kohort). Ana bot, T2 ve M2 defterleri/evrenleri DEĞİŞMEZ. Gerçek para YOK.
"""
from .book import PatternBook
from .data import CsvCandleCache, DataService, REQUIREMENTS, TIMEFRAMES
from .detect import detect_findings, update_finding
from .report import build_report
from .scheduler import PatternScanner
from .strategy import FAMILIES, PROTOCOL_VERSION, build_plans, evaluate_trigger
from .universe import COHORTS, PRIORITY_MAX_AGE_H, cohort_of, discover

__all__ = ["PatternBook", "CsvCandleCache", "DataService", "REQUIREMENTS", "TIMEFRAMES", "detect_findings", "update_finding",
           "build_report", "PatternScanner", "FAMILIES", "PROTOCOL_VERSION", "build_plans", "evaluate_trigger", "COHORTS",
           "PRIORITY_MAX_AGE_H", "cohort_of", "discover"]
