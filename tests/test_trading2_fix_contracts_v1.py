# -*- coding: utf-8 -*-
"""SÖZLEŞME KAPILARI (2026-09-17) — onarılan davranışın SESSİZCE gerilemesini önleyen paket geneli denetimler.

Bu dosya DAVRANIŞ testi değildir (davranış kanıtı `test_trading2_fix_*` dosyalarındadır): burada yalnız
"onarımın dayandığı çağrı sözleşmesi hâlâ kuruluyor mu" sorusu kaynağın kendisinden denetlenir. Bir bakım
düzenlemesi `market=` ya da `funding_rate_lookup=` bağını düşürürse davranış testleri fark etmeyebilir
(sentetik kurulum eksik alanı tolere eder) ama bu kapı düşer.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.dashboard import state as dstate, terminal as term  # noqa: E402
from tradingbot.pattern_trader import book as pbook, scheduler as psched  # noqa: E402
from tradingbot.strategy_paper import BAR_CORRUPT, BAR_MARKET_MISMATCH, BAR_SCALE_UNVERIFIED  # noqa: E402


def _calls(path: Path, name: str) -> list[ast.Call]:
    """Kaynaktaki `<...>.name(...)` çağrıları (AST; içe aktarma/çalıştırma YOK)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Attribute) and n.func.attr == name)
                 or (isinstance(n.func, ast.Name) and n.func.id == name))]


def _kw(call: ast.Call) -> set[str]:
    return {k.arg for k in call.keywords if k.arg}


# ---------------------------------------------------------------- BULGU 1: piyasa kimliği bar uygulamasına bağlanır
@pytest.mark.parametrize(("dosya", "fonksiyon"), [
    ("tradingbot/engine_v3.py", "_paper_closed_bars"),
    ("tradingbot/pattern_trader/scheduler.py", "_scan_symbol"),
])
def test_every_producer_of_closed_bar_specs_declares_its_market(dosya, fonksiyon):
    """Bar sözleşmesini ÜRETEN her üretim yolu `market` alanını yazar: kimlik fiyat bandından TAHMİN EDİLMEZ."""
    src = (ROOT / dosya).read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == fonksiyon)
    keys = {k.value for d in ast.walk(fn) if isinstance(d, ast.Dict) for k in d.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert {"tf", "rows", "mark", "market"} <= keys, "%s: bar spec'inde eksik alan (%s)" % (fonksiyon, sorted(keys))


def test_bar_rejection_reasons_are_three_distinct_contracts():
    """Bütünlük / ölçek / piyasa kimliği AYRI gerekçelerdir ve eski birleşik ad geri GELMEZ."""
    assert len({BAR_CORRUPT, BAR_SCALE_UNVERIFIED, BAR_MARKET_MISMATCH}) == 3
    src = inspect.getsource(sys.modules["tradingbot.strategy_paper"].apply_closed_bars_to_ledger)
    assert "BAR_OUT_OF_RANGE" not in src, "birleşik gerekçe geri döndü: bütünlük ile makullük yine tek koşulda"
    for reason in (BAR_CORRUPT, BAR_SCALE_UNVERIFIED, BAR_MARKET_MISMATCH):
        assert reason in src, reason


def test_the_bar_scale_verdict_reads_the_close_not_the_extremes():
    """ÖLÇEK hükmü barın KAPANIŞINA bakar. Uçlara (high/low) dönerse sert fitiller yine elenir."""
    src = inspect.getsource(sys.modules["tradingbot.strategy_paper"].apply_closed_bars_to_ledger)
    line = next(ln for ln in src.splitlines() if "BAR_SCALE_TOLERANCE" in ln and "ref" in ln)
    assert "cl" in line and " hi " not in line and " lo " not in line, line


# ---------------------------------------------------------------- BULGU 3: funding her iki üretim yoluna bağlanır
@pytest.mark.parametrize("fonksiyon", ["apply_closed_bars", "tick"])
def test_scheduler_binds_funding_on_both_ledger_call_paths(fonksiyon):
    """`_scan_symbol` (bar uçları) ve `exit_check` (çıkış izleyicisi) çağrılarının İKİSİ de oran kaynağını verir."""
    calls = _calls(ROOT / "tradingbot" / "pattern_trader" / "scheduler.py", fonksiyon)
    assert calls, "scheduler artık %s çağırmıyor" % fonksiyon
    for c in calls:
        assert "funding_rate_lookup" in _kw(c), "%s çağrısı funding oranını BAĞLAMIYOR" % fonksiyon


def test_pattern_book_never_estimates_an_unknown_funding_rate():
    """Bilinmeyen oran TAHMİNLE doldurulmaz (`fallback_to_last_known=False`) — bilinmeyen dönem BEKLER."""
    src = inspect.getsource(pbook.PatternBook.bind_funding)
    assert "fallback_to_last_known = False" in src


# ---------------------------------------------------------------- BULGU 4: süre kontrolü TEK tanım, girişin başında
def test_expiry_rule_is_defined_once_and_checked_before_any_entry():
    """`is_expired` tek tanımdır ve `_try_open` onu gerçekleşmeden ÖNCE çağırır."""
    src_exp = inspect.getsource(pbook.PatternBook.is_expired)
    assert "int(as_of_ms) > v" in src_exp, "sınır kuralı (kesin büyük) değişti"
    assert "parse_ts_ms(raw)" in src_exp and "isinstance(raw, (int, float))" in src_exp, \
        "`_ms` alani sayisalken DOGRUDAN milisaniye okunmali (saniye/ms sezgisi uygulanmamali)"
    assert "return True" in src_exp, "okunamayan geçerlilik alanı FAIL-CLOSED olmalı"
    src = inspect.getsource(pbook.PatternBook._try_open)
    i_exp, i_open = src.index("is_expired"), src.index("apply_action(")
    assert i_exp < i_open, "süre kontrolü emirden SONRA geliyor"
    # `_try_open` içinde `expires_at_ms` ile YAPILAN İKİNCİ BİR KARŞILAŞTIRMA olmamalı (kural tek yerde).
    fn = ast.parse("".join(l[4:] if l.startswith("    ") else l for l in src.splitlines(keepends=True))).body[0]
    cmps = [c for c in ast.walk(fn) if isinstance(c, ast.Compare)
            and "expires_at_ms" in ast.dump(c)]
    assert cmps == [], "ikinci bir süre karşılaştırması var (tek tanım bozuldu): %d" % len(cmps)


# ---------------------------------------------------------------- BULGU 5: panel kimliği uçtan uca
def test_panel_scope_functions_take_book_and_market():
    """Kapsam üreten panel fonksiyonları defter ve piyasa kimliğini PARAMETRE olarak alır."""
    assert "market" in inspect.signature(term.account_snapshot).parameters
    assert {"book_id", "market"} <= set(inspect.signature(term.closed_block).parameters)
    assert "market" in inspect.signature(term.live_refresh_js).parameters
    assert {"book_id", "market"} <= set(inspect.signature(term.pattern_plans_for).parameters)
    assert "market" in inspect.signature(dstate.StateReader.book_positions).parameters
    assert {"book_id", "market"} <= set(inspect.signature(term.trade_qs).parameters)


def test_authoritative_book_readers_distinguish_empty_from_missing():
    """Boş ama OKUNABİLİR defter, özet projeksiyonuna DÜŞMEZ (eski `if not pos:` kalıbı geri gelmesin)."""
    for src in (inspect.getsource(dstate.StateReader.book_positions), inspect.getsource(dstate.StateReader._book_history)):
        assert "isinstance(led, dict)" in src, src
        assert "if not pos:" not in src and "if not rows:" not in src


def test_live_refresh_also_targets_the_plan_box():
    js = term.live_refresh_js("strategy_paper", market="futures")
    for host in ("cardshost", "poshost", "closedhost", "planhost"):
        assert host in js, host


# ---------------------------------------------------------------- KARŞIT DOĞRULAMA sonrası eklenen kapılar
def test_an_unapplied_bar_never_abandons_the_rest_of_the_chain():
    """`break` GERİ GELMEMELİ: uygulanamayan bar yalnız kendini atlar (aksi hâlde sonraki geçerli barın
    koruyucu stop'u pencere boyunca düşer)."""
    src = inspect.getsource(sys.modules["tradingbot.strategy_paper"].apply_closed_bars_to_ledger)
    loop = src.split("for r in rows:", 1)[1]
    for reason in (BAR_CORRUPT, BAR_SCALE_UNVERIFIED):
        after = loop.split("_gap(pos, tf, o, %s" % reason, 1)[1]
        blk = after.split("continue", 1)[0]             # gerekçe → o dalın SONRAKİ akış deyimine kadar
        assert "break" not in blk, "%s dalı zinciri durduruyor (break)" % reason
        assert "cursor = max(cursor, o)" in blk, "%s dalında imleç ilerlemiyor" % reason
    assert "o not in pending" in loop, "boşluk kaydı olan bar yeniden denenmeli"


def test_extremes_keep_a_magnitude_bound():
    """Ölçek hükmü kapanışa bakınca uçlar BAĞSIZ kalmamalı."""
    src = inspect.getsource(sys.modules["tradingbot.strategy_paper"].apply_closed_bars_to_ledger)
    assert "BAR_EXTREME_MIN_RATIO" in src and "BAR_EXTREME_MAX_RATIO" in src


def test_the_pattern_book_never_writes_to_the_shared_filters_cache():
    """`FiltersCache.put()` önbellek geneli `verified_at` damgasını yeniler ve ANA BOTun resmî yenilemesini
    atlatır; ayrıca nesne kilitsizdir ve tarayıcı AYRI iş parçacığıdır."""
    src = inspect.getsource(pbook.PatternBook)
    assert "filters_cache.put(" not in src, "paylaşılan filtre önbelleğine yazılıyor"
    assert "_filters_memo" in src, "tur içi tekrar için defterin KENDİ belleği olmalı"


def test_market_identity_is_read_from_provenance_not_hardcoded():
    """Bar ve giriş yollarının ikisinde de piyasa kimliği çerçevenin KENDİ provenansından gelir."""
    from tradingbot.pattern_trader import data as pdata
    src = inspect.getsource(pdata.DataService.bars)
    assert '"market": "USDM_PERP"' not in src and "MARKET_OF" in src, src[:400]
    assert 'or "USDM_PERP"' not in inspect.getsource(psched.PatternScanner._scan_symbol)
    open_src = inspect.getsource(pbook.PatternBook._try_open)
    assert 'market="USDM_PERP"' not in open_src, "giriş hükmü piyasayı SABİT yazıyor"
    assert "DATA_MARKET_" in open_src, "giriş dilimi piyasası DENETLENMELİ"


def test_unknown_funding_interval_is_not_assumed_to_be_eight_hours():
    from tradingbot.pattern_trader import funding as pf
    assert "_intervals_known" in inspect.getsource(pf.FundingRates)
    assert "if not self._intervals_known:" in inspect.getsource(pf.FundingRates.hours_for)


def test_funding_coverage_makes_no_provider_call():
    """Kapsama kapanış yolunda (kilit altında) çağrılır: AĞA çıkmamalı."""
    params = inspect.signature(pbook.PatternBook.funding_coverage).parameters
    assert {"hours_utc", "settled_ts"} <= set(params), sorted(params)
    body = inspect.getsource(pbook.PatternBook.funding_coverage).split('"""', 2)[-1]
    # Saatler ÇAĞRIYLA gelir; `hours_for` yalnız eski (alan taşımayan) kayıtlar için geri düşüştür.
    assert "hours_utc is not None else" in body, body[:300]
    assert "applied_settlements" in body, "gerçekten uygulanan settlement'lar sayılmalı"
    # `_on_closed` kaydın KENDİ alanlarını geçirir: kapanış yolunda saat çözümü AĞA çıkmaz.
    closed = inspect.getsource(pbook.PatternBook._on_closed)
    assert 'hours_utc=f.get("funding_hours_utc")' in closed and 'settled_ts=f.get("funding_settled_ts")' in closed


def test_javascript_values_are_escaped_before_embedding():
    assert "_js(" in inspect.getsource(term.live_refresh_js), "değerler kaçışsız gömülüyor"


def test_market_identity_of_a_trade_has_a_single_definition():
    """ÜRETİM KUSURU (2026-09-17): borsa kimliği (`USDM_PERP`) panel seçim adıyla (`futures`) doğrudan
    karşılaştırılıyordu ve 44 kapanış panelde 0 göründü. Kural TEK yerdedir ve kopyası kalmamalıdır."""
    from tradingbot.dashboard import app as dapp
    from tradingbot.dashboard.state import market_of
    assert market_of({"market_type": "USDM_PERP"}) == "futures"
    assert market_of({"market_type": "SPOT"}) == "spot"
    assert market_of({}) == "futures", "alan yoksa futures (ana defter)"
    for mod, src in (("state", inspect.getsource(dstate)), ("app", inspect.getsource(dapp))):
        body = src.replace(inspect.getsource(market_of), "")
        assert 'market_type") or "futures"' not in body, "%s: kuralin ikinci kopyasi var" % mod
