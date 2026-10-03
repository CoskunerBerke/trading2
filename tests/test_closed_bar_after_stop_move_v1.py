# -*- coding: utf-8 -*-
"""CANLI MUHASEBE HATASI (2026-09-29): stop FİYAT izlemesiyle taşındıktan SONRA uygulanan kapanmış 1h bar.

Durum: pozisyon 15:50'de açık (LONG 100, stop 98, hedefler 103/106). 60 sn koruyucu izleyici 16:40'ta +1R'yi görür ve stopu
başa-başa (ana defter: `breakeven_at_mfe_r` 1,0) ya da TP1 dolumuyla başa-başa taşır. 17:05 turu kapanmış 16:00–17:00 1h
barını uygular: bar 16:40'tan ÖNCE açılmıştır, açılışı (99,0) YENİ stopun altındadır; `exit_decision` açılışı "ilk gözlem"
sayıp pozisyonu 98,97'den ("başa-baş stop", ≈ −0,57R) kapatıyordu — o fiyat stop taşınmadan ÖNCE gözlenmişti (o anda stop 98
idi, fiyat stopun üstündeydi). Aynı kural ortak yapı sıkılaştırmasında zaten vardı (`engine_v3._main_closed_bars`,
`meta.structure_stop.at`); MFE başa-baş ve TP1 başa-başı için yoktu.

Düzeltme (`strategy_paper.apply_closed_bars_to_ledger`): stopun son taşındığı andan (MFE başa-baş `meta.be_by_mfe.at`, TP1
dolumu) ÖNCE açılmış bar GÜNCEL stopa sınanmaz. İnceleme bulgusu (2026-09-29): ilk düzeltme böyle barları hep ATLIYORDU ve
her bar içi sırada kesin olan stop/hedef olaylarını düşürüyordu (60 sn izlemenin kaçırdığı fitiller). Şimdi bar kendi
süresinde geçerli stoplarla sınanır: açılıştaki stopa göre kesin çıkış ya da hiçbir sırada stop olmayan (hedef dolabilen)
bar o stop durumuyla uygulanır; yalnız sıra BELİRSİZSE (ters uç yeni stopun ötesinde, eski stopun berisinde) bar tüketilir
(`BAR_SKIPPED` / `OPENED_BEFORE_STOP_MOVE`). Barlar gerçek motor/defter nesneleriyle (StrategyBook.protect →
StrategyBook.apply_closed_bars; ana defter guarded_tick → `_main_closed_bars` → `apply_closed_bars_to_ledger`) sınanır.
1–3 ve 6, 7, 9, 10, 11. testler 7ec832c'de düşer; 4–5 ve 8 koruma testleridir (7ec832c ile aynı sonuç; 8 atla düzeltmesinde
düşüyordu)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tradingbot.accounting import AmountType, SizeSpec, TickData  # noqa: E402
from tradingbot.accounting.filters import FiltersCache  # noqa: E402
from tradingbot.accounting.models import MarketType, SymbolFilters  # noqa: E402
from tradingbot.config import BotConfig  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.core import iso  # noqa: E402
from tradingbot.protective_monitor import guarded_tick  # noqa: E402
from tradingbot.risk.killswitch import KillSwitch  # noqa: E402
from tradingbot.risk.profiles import PROFILES  # noqa: E402
from tradingbot.strategy_paper import BookSpec, StrategyBook, apply_closed_bars_to_ledger  # noqa: E402

UTC = timezone.utc
SYM = "X/USDT"
T_OPEN = datetime(2026, 9, 28, 15, 50, tzinfo=UTC)
H1 = 3_600_000
F = SymbolFilters(symbol="XUSDT", market_type=MarketType.USDM_PERP, price_tick=Decimal("0.01"), qty_step=Decimal("0.001"),
                  min_qty=Decimal("0.001"), min_notional=Decimal("5"), max_leverage=20, source="exchange")


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _book(tmp_path: Path, *, be: float) -> StrategyBook:
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    cfg.v3 = load_v3({})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    cfg.cache_path.mkdir(parents=True, exist_ok=True)
    spec = BookSpec(name="t2_trend_regime", state_dir="sp_be", starting_equity_usdt=1000.0, breakeven_at_mfe_r=be)
    book = StrategyBook(cfg, profile=PROFILES["PAPER_RESEARCH"], killswitch=KillSwitch(),
                        filters_cache=FiltersCache(cfg.cache_path / "symbol_filters.json"), run_id="rid", spec=spec)
    assert book.ledger.open(SYM, "LONG", Decimal("100"), SizeSpec(Decimal("30"), AmountType.NOTIONAL, 10), stop=Decimal("98"),
                            targets=[Decimal("103"), Decimal("106")], filters=F, now=T_OPEN) is not None
    return book


def _monitor(book: StrategyBook, minute: int, price: str) -> list:
    """60 sn koruyucu izleyici adımı (canlı yol: `StrategyBook.protect` → `guarded_tick`), taze kaynak zamanlı fiyatla."""
    now = T_OPEN + timedelta(minutes=minute)
    p = Decimal(price)
    td = TickData(last=p, mark=p, ts=iso(now), src_ts=iso(now), fetched_ts=iso(now))
    return book.protect({SYM: td}, {SYM: float(p)}, {}, now=now, expect=book.held_ids(), apply_clock=lambda: _ms(now))


def _bar(hour: int, o: float, h: float, lo: float, c: float) -> dict:
    return {"timestamp": _ms(datetime(2026, 9, 28, hour, 0, tzinfo=UTC)), "open": o, "high": h, "low": lo, "close": c}


def _apply(book: StrategyBook, rows: list[dict], minute: int, mark: float) -> list:
    """Tur adımı (canlı yol: `StrategyBook.apply_closed_bars`) — kapanmış 1h barlar."""
    return book.apply_closed_bars({SYM: {"tf": "1h", "rows": rows, "mark": mark, "market": "USDM_PERP"}},
                                  now=T_OPEN + timedelta(minutes=minute))


# ============================================================================ 1) MFE başa-baş (ana defter kuralı)
def test_a_bar_opened_before_a_mark_driven_break_even_move_does_not_close_at_its_earlier_open(tmp_path):
    book = _book(tmp_path, be=1.0)
    for m, p in ((10, "99.0"), (25, "100.5"), (50, "102.1"), (70, "101.8")):
        assert _monitor(book, m, p) == []
    pos = book.ledger.positions[SYM]
    assert pos.meta.get("be_by_mfe") and pos.stop > Decimal("100"), "izleyici 16:40'ta stopu başa-başa taşıdı"
    recs = _apply(book, [_bar(16, 99.0, 102.2, 98.9, 101.8)], 75, 101.8)
    assert recs == [] and SYM in book.ledger.positions, \
        "ÖNCE: 16:00 barının açılışı (99,0) yeni stopa 'ilk gözlem' sayıldı → 'başa-baş stop' @ 98,97, r −0,571"
    pos = book.ledger.positions[SYM]
    assert pos.meta["ohlc_cursor"]["1h"] == _ms(datetime(2026, 9, 28, 16, tzinfo=UTC)), "bar TÜKETİLDİ (yeniden denenmez)"
    ev = [e for e in book.data_events if e["kind"] == "BAR_SKIPPED"]
    assert ev and ev[-1]["reason"] == "OPENED_BEFORE_STOP_MOVE" and ev[-1]["bar_open_ms"] == _ms(datetime(2026, 9, 28, 16, tzinfo=UTC))
    # aynı bar sonraki turda da uygulanmaz; stop taşındıktan SONRA açılmış bar yeni stopu NORMAL tetikler
    assert _apply(book, [_bar(16, 99.0, 102.2, 98.9, 101.8)], 81, 101.8) == []
    recs = _apply(book, [_bar(16, 99.0, 102.2, 98.9, 101.8), _bar(17, 101.8, 102.0, 99.5, 99.8)], 135, 99.8)
    assert len(recs) == 1 and recs[0].exit_reason == "başa-baş stop"
    assert recs[0].features["exit_fill"]["basis"] == "STOP_CLOSE_BEYOND_LEVEL_PRUDENT", "sonraki barın kuralları değişmedi"


# ============================================================================ 2) TP1 başa-başı (iki hedefli defter)
def test_a_bar_opened_before_a_mark_driven_tp1_fill_does_not_close_the_rest_at_its_earlier_open(tmp_path):
    book = _book(tmp_path, be=0.0)
    for m, p in ((10, "99.0"), (25, "100.5"), (50, "103.1"), (70, "101.8")):
        assert _monitor(book, m, p) == []
    pos = book.ledger.positions[SYM]
    assert pos.tp1_done and pos.qty < pos.initial_qty and pos.stop > Decimal("100")
    recs = _apply(book, [_bar(16, 99.0, 103.2, 98.9, 101.8)], 75, 101.8)
    assert recs == [] and SYM in book.ledger.positions, "ÖNCE: kalan yarı 16:00 açılışından (98,97) kapanıyordu"
    # bar bir sonraki hedefe (106) yetişmediği için yalnız tüketildi; TP2 fiyat izlemesiyle normal dolar
    assert _monitor(book, 140, "106.2") and SYM not in book.ledger.positions


# ============================================================================ 3) ana defter (motor yolu)
def test_main_ledger_bar_after_a_break_even_move_uses_the_same_rule(tmp_path, monkeypatch):
    from test_runtime_fixes_v1 import DAY, _apply_main, _h1, _main
    eng, sym = _main(tmp_path, monkeypatch, started=DAY)                   # LONG 100, stop 95, açılış 10:20
    eng.ledger2.breakeven_at_mfe_r = Decimal("1.0")                          # config.yaml `futures_v3.breakeven_at_mfe_r`
    at = DAY.replace(hour=11, minute=40)
    tick = TickData(last=Decimal("105.2"), mark=Decimal("105.2"), ts=iso(at), src_ts=iso(at), fetched_ts=iso(at))
    recs, _info = guarded_tick(eng.ledger2, {sym: tick}, now=at, expect={sym: eng.ledger2.positions[sym].id},
                               apply_clock=lambda: _ms(at))
    assert recs == [] and eng.ledger2.positions[sym].meta.get("be_by_mfe")
    bar = (DAY.replace(hour=11), 99.5, 105.5, 99.0, 104.8)                   # 11:00 açıldı (taşımadan ÖNCE), açılış < başa-baş
    assert _apply_main(eng, sym, _h1([bar]), DAY.replace(hour=12, minute=5)) == []
    assert sym in eng.ledger2.positions, "ÖNCE: ana defter 11:00 açılışından (≈ 99,47) 'başa-baş stop' ile kapanıyordu"
    after = (DAY.replace(hour=12), 104.8, 105.0, 99.8, 104.0)                # taşımadan SONRA açıldı: yeni stop gerçek olay
    recs = _apply_main(eng, sym, _h1([bar, after]), DAY.replace(hour=13, minute=5))
    assert len(recs) == 1 and recs[0].exit_reason == "başa-baş stop"
    assert recs[0].features["exit_fill"]["basis"] == "STOP_AT_LEVEL"


# ============================================================================ 4) bar kendisi taşırsa sonraki bar normal
def test_a_move_made_by_an_applied_bar_does_not_skip_the_next_bar(tmp_path):
    """Stopu BAR tick'i taşıdıysa taşıma anı o barın kapanışıdır: sonraki bar (o anda açılır) normal uygulanır. Bu yol
    değişmedi; yalnız fiyat izlemesiyle taşınmış stopa göre ÖNCEDEN açılmış barlar atlanır."""
    book = _book(tmp_path, be=1.0)
    rows = [_bar(16, 100.2, 102.4, 100.15, 101.9), _bar(17, 101.9, 102.0, 99.4, 99.6)]
    recs = _apply(book, rows, 135, 99.6)
    assert len(recs) == 1 and recs[0].exit_reason == "başa-baş stop"
    assert not [e for e in book.data_events if e.get("reason") == "OPENED_BEFORE_STOP_MOVE"]


@pytest.mark.parametrize("be", [0.0, 1.0])
def test_without_a_stop_move_closed_bars_apply_exactly_as_before(tmp_path, be):
    """Stop taşınmadıysa (başa-baş/TP1 yok) davranış aynı: açılış stopun altındaysa açılıştan dolum (gerçek boşluk)."""
    book = _book(tmp_path, be=be)
    recs = _apply(book, [_bar(16, 97.5, 99.5, 97.0, 99.0)], 75, 99.0)
    assert len(recs) == 1 and recs[0].exit_reason == "stop"
    assert recs[0].features["exit_fill"]["basis"] == "GAP_FILL_AT_FIRST_OBSERVATION"


# ============================================================================ 6-11) inceleme bulgusu (2026-09-29)
# Taşımadan ÖNCE açılmış bar KESİN bilgi taşıyorsa (her bar içi sırada doğru olan stop ya da hedef) atlanmaz: bar kendi
# süresinde geçerli stoplarla sınanır. Yalnız sıra belirsizse (ters uç yeni stopun ötesinde, eski stopun berisinde) atlanır.
# 6, 7, 9, 10 önceki düzeltmede (atla) pozisyonu AÇIK bırakıyordu; 7ec832c'de ise başa-baş seviyesinden (≈ 0R) kapatıyordu.
def _pre_move_events(book: StrategyBook) -> list[dict]:
    return [e for e in book.data_events if e["kind"] == "BAR_PRE_MOVE_STOP"]


def test_a_bar_that_closed_before_the_move_and_breached_the_initial_stop_closes_at_the_initial_stop(tmp_path):
    """S6: 16:00–17:00 barı taşımadan (17:01) ÖNCE kapandı; bütün bar boyunca geçerli tek stop 98'di ve düşüğü 97,5 onu
    deldi → pozisyon kesinlikle ≈ 98'den (−1R) durdu. 7ec832c: 'başa-baş stop' r +0,005; atla düzeltmesi: açık."""
    book = _book(tmp_path, be=1.0)
    for m, p in ((20, "100.4"), (50, "100.9"), (71, "102.1")):
        assert _monitor(book, m, p) == []
    assert book.ledger.positions[SYM].meta["be_by_mfe"]["at"] == "2026-09-28T17:01:00+00:00"
    thr = book.ledger.breakeven_at_mfe_r
    recs = _apply(book, [_bar(16, 100.5, 101.0, 97.5, 100.8)], 75, 101.9)
    assert len(recs) == 1 and SYM not in book.ledger.positions
    r = recs[0]
    assert r.exit_reason == "stop", "o anda başa-baş YOKTU: etiket o anki duruma göre (başa-baş değil)"
    assert r.closed_at == "2026-09-28T17:00:00+00:00" and float(r.r_multiple) == pytest.approx(-1.063, abs=2e-3)
    ef = r.features["exit_fill"]
    assert ef["basis"] == "STOP_AT_LEVEL" and ef["stop"] == "98" and ef["stop_in_force"] == "PRE_MOVE"
    assert Decimal(ef["stop_after_move"]) > Decimal("100"), "taşımadan sonraki (başa-baş) stop kayıtta ayrıca durur"
    assert book.ledger.breakeven_at_mfe_r == thr, "defterin başa-baş eşiği geri kondu"
    ev = _pre_move_events(book)
    assert ev and ev[-1]["certain_exit"] is True and ev[-1]["stop_at_bar_open"] == "98"


def test_a_straddling_bar_whose_low_breached_the_initial_stop_closes_at_the_initial_stop(tmp_path):
    """S4: taşıma 16:40'ta (bar içinde); düşük 97,5 ilk stopun (98) da altında → HER sırada durdu (önce olduysa 98'den,
    sonra olduysa başa-başdan): ihtiyatlı dal seviye 98. 7ec832c: açılış 100,1 < başa-baş → 'başa-baş stop' r −0,030."""
    book = _book(tmp_path, be=1.0)
    for m, p in ((10, "100.2"), (25, "100.9"), (50, "102.1"), (70, "101.8")):
        assert _monitor(book, m, p) == []
    recs = _apply(book, [_bar(16, 100.1, 102.2, 97.5, 101.8)], 75, 101.8)
    assert len(recs) == 1 and recs[0].exit_reason == "stop"
    assert float(recs[0].r_multiple) == pytest.approx(-1.063, abs=2e-3)
    assert recs[0].features["exit_fill"]["basis"] == "STOP_AT_LEVEL"


def test_a_straddling_bar_that_reached_the_next_target_with_no_possible_stop_fills_the_target(tmp_path):
    """S5: TP1 izleyiciyle 16:40'ta doldu (stop başa-başa); 16:00 barının yükseği 106,3 TP2'ye (106) ulaştı ve düşüğü
    (100,3) başa-başın da üstünde → hiçbir sırada stop yok, hedef KESİN. Atla düzeltmesi pozisyonu açık bırakıyordu
    (7ec832c ile aynı sonuç: koruma testi)."""
    book = _book(tmp_path, be=0.0)
    for m, p in ((10, "100.6"), (25, "100.9"), (50, "103.1"), (70, "104.0")):
        assert _monitor(book, m, p) == []
    assert book.ledger.positions[SYM].tp1_done
    recs = _apply(book, [_bar(16, 100.5, 106.3, 100.3, 104.0)], 75, 104.0)
    assert len(recs) == 1 and recs[0].exit_reason == "hedef2"
    assert float(recs[0].r_multiple) == pytest.approx(2.154, abs=2e-3)


def test_a_bar_before_a_later_move_that_reached_tp1_fills_it_and_keeps_the_tighter_stop(tmp_path):
    """Bar taşımadan (17:01) önce kapandı, yükseği TP1'e (103) ulaştı, düşüğü ilk stopun berisinde: TP1 KESİN. Kalan
    pozisyon açık kalır; stop güncel başa-baştan GEVŞEMEZ, `be_by_mfe` işareti ve defter eşiği aynen durur."""
    book = _book(tmp_path, be=1.0)
    for m, p in ((20, "100.4"), (50, "100.9"), (71, "102.1")):
        assert _monitor(book, m, p) == []
    pos = book.ledger.positions[SYM]
    be0, stop0, thr = dict(pos.meta["be_by_mfe"]), pos.stop, book.ledger.breakeven_at_mfe_r
    assert _apply(book, [_bar(16, 100.5, 103.4, 99.2, 101.9)], 75, 101.9) == []
    pos = book.ledger.positions[SYM]
    assert pos.tp1_done and pos.qty < pos.initial_qty, \
        "hedef1 kesin — 7ec832c: düşük 99,2 o anda geçerli OLMAYAN başa-başa sınanıp kapanıyordu; atla düzeltmesi düşürüyordu"
    assert pos.stop >= stop0 and pos.meta["be_by_mfe"] == be0 and book.ledger.breakeven_at_mfe_r == thr


def test_short_mirror_of_a_pre_move_bar_breaching_the_initial_stop(tmp_path):
    """SHORT ayna: SHORT 100, stop 102, hedefler 97/94; izleyici 17:01'de başa-başa taşır; 16:00 barının yükseği 102,5."""
    book = _book(tmp_path, be=1.0)
    book.ledger.positions.pop(SYM)
    assert book.ledger.open(SYM, "SHORT", Decimal("100"), SizeSpec(Decimal("30"), AmountType.NOTIONAL, 10), stop=Decimal("102"),
                            targets=[Decimal("97"), Decimal("94")], filters=F, now=T_OPEN) is not None
    for m, p in ((20, "99.6"), (50, "99.1"), (71, "97.9")):
        assert _monitor(book, m, p) == []
    assert book.ledger.positions[SYM].meta.get("be_by_mfe")
    recs = _apply(book, [_bar(16, 99.5, 102.5, 99.0, 99.2)], 75, 98.1)
    assert len(recs) == 1 and recs[0].exit_reason == "stop" and recs[0].features["exit_fill"]["stop"] == "102"
    assert float(recs[0].r_multiple) == pytest.approx(-1.063, abs=3e-3)


def test_main_ledger_bar_closed_before_a_break_even_move_uses_the_stop_in_force(tmp_path, monkeypatch):
    """Ana defter (motor yolu `_main_closed_bars`): LONG 100, stop 95; izleyici 12:01'de başa-başa taşır; 11:00–12:00
    barının düşüğü 94 ilk stopu deldi → 'stop' seviyeden. Atla düzeltmesi: açık; 7ec832c: başa-baş seviyesinden."""
    from test_runtime_fixes_v1 import DAY, _apply_main, _h1, _main
    eng, sym = _main(tmp_path, monkeypatch, started=DAY)
    eng.ledger2.breakeven_at_mfe_r = Decimal("1.0")
    at = DAY.replace(hour=12, minute=1)
    tick = TickData(last=Decimal("105.2"), mark=Decimal("105.2"), ts=iso(at), src_ts=iso(at), fetched_ts=iso(at))
    recs, _info = guarded_tick(eng.ledger2, {sym: tick}, now=at, expect={sym: eng.ledger2.positions[sym].id},
                               apply_clock=lambda: _ms(at))
    assert recs == [] and eng.ledger2.positions[sym].meta.get("be_by_mfe")
    bar = (DAY.replace(hour=11), 100.5, 101.0, 94.0, 100.8)
    recs = _apply_main(eng, sym, _h1([bar]), DAY.replace(hour=12, minute=5))
    assert len(recs) == 1 and recs[0].exit_reason == "stop" and sym not in eng.ledger2.positions
    assert recs[0].features["exit_fill"]["basis"] == "STOP_AT_LEVEL" and recs[0].features["exit_fill"]["stop"] == "95"
    assert eng.ledger2.breakeven_at_mfe_r == Decimal("1.0")
