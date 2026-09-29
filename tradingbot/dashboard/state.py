"""State dizini okuyucu — panel yalnızca okur; eksik/bozuk dosyalar None döner (asla istisna sızdırmaz)."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterator

from ..core import from_iso, read_json, utc_now

# /api/state/{name} beyaz listesi (uzantısız)
STATE_FILES: dict[str, str] = {
    "agents": "agents.json", "futures_ledger": "futures_ledger.json", "portfolio": "portfolio.json", "scan": "scan.json",
    "learning": "learning.json", "signals": "signals.json", "coin_heads": "coin_heads.json", "risk": "risk.json",
    "killswitch": "killswitch.json", "mode": "mode.json", "health": "health.json", "llm_budget": "llm_budget.json",
    "models": "models.json", "universe": "universe.json", "shadow_book": "shadow_book.json", "heartbeat": "heartbeat.json",
    "spot_ledger": "spot_ledger.json",
    "orders": "orders.json", "triggers": "triggers.json",
    "snapshot_telemetry": "snapshot_telemetry.json", "research_policy": "research_policy.json",
    "decision_funnel": "decision_funnel.json",
    "quant_eval": "quant_eval.json",
    "universe_eval": "universe_eval.json",
    # SABIT GIRIS EVRENI: her turda yazilan karar-cercevesi provenansi (salt okunur).
    "frame_provenance": "frame_provenance.json",
    # STRATEJI KAGIT DEFTERI (V10): tek kuralli trend, ayri defter, ileri test (salt okunur ozet).
    "strategy_paper": "strategy_paper.json",
    # FORMASYON PAPER TRADER V1 (2026-09-16) — ayri defter ozeti, ekonomik rapor, tarama/evren durumu (SALT OKUNUR).
    "pattern_trader": "pattern_trader.json",
    "pattern_report": "pattern_report.json",
    "pattern_scan": "pattern_scan.json",
    "pattern_universe": "pattern_universe.json",
    # PAPER LEARNING LOOP INTEGRITY V3 — ikisi de SALT OKUNUR gözlem belgesidir.
    "learning_chain": "learning_chain.json",
    # Kalibrasyon/model durumunu KANITA bağlamak için (salt okunur gösterim).
    "learn_v2": "learn_v2.json",
    "position_management": "position_management.json",
    # EXIT GIVEBACK & PROFIT PROTECTION V1 — salt okunur karşı-olgusal rapor.
    "exit_eval": "exit_eval.json",
    # ENTRY SELECTIVITY CHALLENGER V1 — salt okunur karşı-olgusal giriş raporu.
    "entry_selectivity": "entry_selectivity.json",
    "mtf_eval": "mtf_eval.json",
    # PROFITABILITY EXPERIMENT — iki sürüm AYRI dosyada: v1 tarihsel/salt okunur
    # (SUPERSEDED_INCOMPLETE_ENTRY_INPUT), v1.1 düzeltilmiş ve canlı (SHADOW PAPER ONLY).
    "profitability_experiment": "profitability_experiment.json",
    "profitability_experiment_v1_1": "profitability_experiment_v1_1.json",
    # v1.2: kapsam-eşli E (entry_v1.1.0), aktif SHADOW deney; v1.1 kabul kapalı (drain).
    "profitability_experiment_v1_2": "profitability_experiment_v1_2.json",
    # LLM alt sisteminin GERÇEK durumu (DISABLED / NOT_CONFIGURED / NO_CALLS / ACTIVE).
    "llm_status": "llm_status.json",
    # ÖĞRENME MODU (2026-09-28, öğrenme modu): ilk aktif an (`learning_mode_since`) — öncesi/sonrası ayrımı (salt okunur).
    "learning_mode": "learning_mode.json",
}
#: ORTAK DENEYİM KATMANI v1 (2026-09-29): panel kartı YALNIZ iki küçük dosyayı okur — toplayıcının `status.json`ı ve CLI
#: taramasının `report_summary.json`ı (`shared-experience-report --summary-out`, ayrı süreç). Panel paketi İÇE AKTARMAZ
#: (yalnız motor ve CLI aktarır — AST testi); ad ve şerit sabitleri `shared_experience.report` ile test eşitliğine bağlı.
#: Depo taranmaz; her dosya ≤ `XP_MAX_BYTES` (512M panelde sınırsız okuma YOK), bozuksa kopyalanmadan yok sayılır.
XP_DIR = "shared_experience"
XP_STATUS_FILE = "status.json"
XP_SUMMARY_FILE = "report_summary.json"
XP_SUMMARY_SCHEMA = "shared_experience_summary_v1"
XP_BANNER_TR = "tanımlayıcı; karar yok; kanıt değil"
XP_MAX_BYTES = 1 << 20
XP_TOP_CELLS = 10


def _xp_json(path: Path) -> dict | None:
    """Salt-okur küçük JSON (2026-09-29): yok / büyük / bozuk → None. `read_json`in aksine bozuk dosyayı KOPYALAMAZ."""
    try:
        if path.stat().st_size > XP_MAX_BYTES:
            return None
        d = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _xp_col(d: Any) -> dict[str, Any]:
    d = d if isinstance(d, dict) else {}
    return {k: d.get(k) for k in ("n", "n_final", "win_rate", "mean_r", "tier", "verdict", "verdict_tr")}


#: `str.splitlines()`in `\n` DIŞINDAKİ satır sınırlarının UTF-8 baytları: \r \v \f \x1c–\x1e, NEL, U+2028/U+2029. Bir bayt
#: satırında bunlardan hiçbiri yoksa satır tam BİR metin satırıdır (`errors="replace"` bu karakterleri başka bayttan üretmez).
_EXTRA_BREAKS = (b"\r", b"\x0b", b"\x0c", b"\x1c", b"\x1d", b"\x1e", b"\xc2\x85", b"\xe2\x80\xa8", b"\xe2\x80\xa9")


def _byte_lines(fh, start: int, end: int) -> Iterator[bytes]:
    """[start, end) aralığının bayt satırları (`\\n` dahil; sondaki parça sonsuz olabilir). `end`in ötesi OKUNMAZ (yazıcı
    bu arada eklese de iki geçiş aynı aralığı görür)."""
    fh.seek(start)
    pos = start
    while pos < end:
        raw = fh.readline(end - pos)
        if not raw:
            return
        pos += len(raw)
        yield raw


def _n_text_lines(raw: bytes) -> int:
    """Bir bayt satırının `decode("utf-8", "replace").splitlines()` satır sayısı (dolu satır için en az 1)."""
    if not any(b in raw for b in _EXTRA_BREAKS):         # bayt araması (memchr); düzenli ifade ~10× yavaş
        return 1
    return len(raw.decode("utf-8", errors="replace").splitlines())


def _tail_start(fh, end: int, n: int, block: int) -> int:
    """[c, end) en az `n` bayt satırı tutan EN KISA sonek: c dosya başı ya da bir `\\n` baytının hemen sonrası. Sondan
    geriye sabit boy bloklarla yalnız `\\n` sayılır; bellekte bir blok durur."""
    if end <= 0:
        return 0
    fh.seek(end - 1)
    k = n if fh.read(1) != b"\n" else n + 1          # sondan k'ıncı `\n`in hemen sonrası
    pos, step = end, max(1, int(block))
    while pos > 0:
        size = min(step, pos)
        pos -= size
        fh.seek(pos)
        buf = fh.read(size)
        if len(buf) != size:                        # dosya bu arada kısaldı: baştan akış (sonuç yine tutarlı)
            return 0
        c = buf.count(b"\n")
        if c >= k:
            i = size
            for _ in range(k):
                i = buf.rfind(b"\n", 0, i)
            return pos + i + 1
        k -= c
    return 0


def _tail_text(path: Path, n: int, *, block: int, keep: Callable[[bytes], bool] | None = None) -> Iterator[str]:
    """`tail_lines`in akışlı çekirdeği. `keep(raw) is False` → bu bayt satırının metin satırları ÜRETİLMEZ (çözülmez de);
    yalnız `tail_jsonl`in `needle` süzgeci kullanır (çağıran zaten eşleşmeyen satırı atar)."""
    with open(path, "rb") as fh:
        end = fh.seek(0, 2)
        start = _tail_start(fh, end, n, block)
        # 1. geçiş: aralıktaki metin satırı sayısı (yalnız sayım; satır tutulmaz). Kesimden sonra en az n satır var;
        # fazlası `\n` dışı satır sınırlarından gelir ve BAŞTAN atlanır (`[-n:]`).
        skip = max(0, sum(_n_text_lines(raw) for raw in _byte_lines(fh, start, end)) - n)
        for raw in _byte_lines(fh, start, end):
            drop = 0
            if skip:
                k = _n_text_lines(raw)
                if k <= skip:
                    skip -= k
                    continue
                drop, skip = skip, 0
            if keep is not None and not keep(raw):
                continue
            parts = raw.decode("utf-8", errors="replace").splitlines()
            yield from (parts[drop:] if drop else parts)


def iter_tail_lines(path: Path, n: int, *, block: int = 1 << 20) -> Iterator[str]:
    """`path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]` ile BİREBİR aynı satırlar, aynı sırada — AKIŞLA
    (2026-09-28, öğrenme modu; dördüncü doğrulama turu). Üçüncü turdaki `tail_lines` sonek büyüdükçe tamponu yeniden çözüp
    bölüyordu: tampon + kopya + metin + iki satır listesi aynı anda → tepe ≈ 7× dosya (eski `read_text` yolu ≈ 4×); 512M
    panel `/api/coin-memory` isteğinde trade_memory ~52 MB'ta ölüyordu (eskisi ~67 MB).

    Kesim yalnız bir `\\n` baytından SONRA yapılır: UTF-8'de bu bayt her zaman karakter ve satır sınırıdır, çözücü orada
    sıfırlanır → kesimden sonraki bayt satırlarının `splitlines()` parçaları tam dosyanın son satırlarıyla aynıdır. Önce
    geriye doğru yalnız `\\n` sayılarak kesim bulunur, sonra aralık iki kez satır satır okunur (sayım + üretim). Bellekte
    yalnız bir blok / bir satır durur. `n <= 0` → eski ifade aynen."""
    if n <= 0:
        yield from path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        return
    yield from _tail_text(path, n, block=block)


def tail_lines(path: Path, n: int, *, block: int = 1 << 20) -> list[str]:
    """`path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]` ile BİREBİR aynı liste (`iter_tail_lines`).
    Tepe bellek yalnız dönen satırlardır (2026-09-28, öğrenme modu; dördüncü doğrulama turu)."""
    return list(iter_tail_lines(path, n, block=block))


#: `needle` süzgecinin güvenli olduğu değerler: düz ASCII kimlik/sembol (JSON kaçışı gerektirmez).
_PLAIN_NEEDLE = re.compile(r"[A-Za-z0-9_./:-]+")


def _needle_filter(needle: str | None) -> Callable[[bytes], bool] | None:
    """Bayt satırı ön süzgeci (2026-09-28, öğrenme modu; dördüncü doğrulama turu): `needle` baytları YOKSA ve satırda hiç `\\`
    yoksa o satırdaki hiçbir JSON değeri `str(değer) == needle` olamaz → satır çözülmez/ayrıştırılmaz. Gerekçe: kaçışsız JSON
    dizesinin karakterleri metinde aynen durur; `errors="replace"` geçerli baytları bire bir çözer. Dize olmayan değerlerin
    `str()`i (int → aynı rakamlar; float → '.', 'e', 'inf', 'nan'; bool/None → True/False/None; liste/sözlük → köşeli/kıvrık
    parantez) yalnız düz tamsayı biçiminde eşleşebilir, o da metinde aynen durur. Bu koşulu garanti etmeyen `needle` (boş,
    düz olmayan karakter, True/False/None, tamsayı olmayan sayı biçimi) → süzgeç YOK (tam ayrıştırma)."""
    if not needle or not _PLAIN_NEEDLE.fullmatch(needle) or needle in ("None", "True", "False"):
        return None
    try:
        float(needle)
    except ValueError:
        pass
    else:
        if not needle.isdigit():
            return None
    nb = needle.encode("ascii")
    return lambda raw: nb in raw or b"\\" in raw


def iter_text_lines(path: Path):
    """`path.read_text(encoding="utf-8", errors="replace").splitlines()` ile AYNI satırlar, aynı sırada — ama akışla (dosya
    metin olarak belleğe alınmaz; 2026-09-28, öğrenme modu — üçüncü doğrulama turu). Her bayt satırı (`\\n`e kadar) ayrı
    çözülür ve `splitlines()` ile bölünür: `\\n` her zaman karakter ve satır sınırıdır, çözücü orada sıfırlanır."""
    with open(path, "rb") as fh:
        for raw in fh:
            yield from raw.decode("utf-8", errors="replace").splitlines()


JSONL_FILES: dict[str, str] = {"llm_calls": "llm_calls.jsonl", "trade_memory": "trade_memory.jsonl", "signals_log": "signals_log.jsonl",
                               "decision_journal": "decision_journal.jsonl",
                               "position_path": "position_path.jsonl",
                               "entry_snapshot": "entry_snapshot.jsonl"}


def _f(x: Any) -> float | None:
    """Sayiya cevrilebiliyorsa float, aksi halde None (BILINMEYEN sifira DUSURULMEZ)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def market_of(trade: Any) -> str:
    """Bir islem kaydinin PIYASASI: "spot" ya da "futures".

    GERCEK defter kayitlari `market_type`i borsa kimligiyle tasir (`USDM_PERP`), panelin secim adiyla
    (`futures`) DEGIL. Dogrudan esitlik aramak butun futures kapanislarini eler (2026-09-17'de uretimde
    goruldu: 44 kapanis panelde 0 gorundu). Kural: yalnizca "spot" spottur; digerleri futures.
    """
    v = str((trade or {}).get("market_type") or "").strip().lower()
    return "spot" if v == "spot" else "futures"


def _age(ts: Any) -> float | None:
    if not ts:
        return None
    try:
        return max(0.0, (utc_now() - from_iso(str(ts))).total_seconds())
    except (ValueError, TypeError):
        return None


class StateReader:
    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)

    # ---- ham okuma
    def get(self, name: str) -> Any:
        fn = STATE_FILES.get(name)
        if not fn:
            return None
        return read_json(self.state_dir / fn, default=None)

    def tail_jsonl(self, name: str, n: int = 200, *, needle: str | None = None,
                   project: Callable[[dict], Any] | None = None) -> list:
        """Son `n` satırın sözlük olan JSON'ları (eski: `read_text().splitlines()[-n:]`; okunamazsa []). Satırlar AKIŞLA
        ayrıştırılır (2026-09-28, öğrenme modu; dördüncü doğrulama turu): bellekte satır listesi yok, yalnız sonuç.

        * `project`: her sözlüğe ayrıştırıldığı anda uygulanır; None dönerse satır atlanır → tüketici yalnız gereken alanları
          tutar (ör. 4000 satırlık `trade_memory` kuyruğunda ~74 KB'lık giriş satırlarının tamamı değil).
        * `needle`: `_needle_filter` — `str(alan) == needle` süzgeci uygulayan çağıran için, eşleşemeyecek satırlar hiç
          ayrıştırılmaz. Sonuç, çağıranın süzgecinden sonra eskisiyle AYNIDIR (yalnız eski yolun JSONDecodeError DIŞI bir
          istisna fırlatacağı satırda — ör. 4300+ haneli tamsayı — o satır artık atlanabilir).
        Varsayılanlarla (ikisi de None) çıktı eskisiyle BİREBİR aynıdır."""
        fn = JSONL_FILES.get(name)
        if not fn:
            return []
        p = self.state_dir / fn
        if not p.exists():
            return []
        out: list = []
        try:
            lines = (_tail_text(p, n, block=1 << 20, keep=_needle_filter(needle)) if n > 0
                     else iter_tail_lines(p, n))
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict):
                    if project is None:
                        out.append(d)
                    else:
                        v = project(d)
                        if v is not None:
                            out.append(v)
        except OSError:
            return []
        return out

    def mtimes(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, fn in {**STATE_FILES, **JSONL_FILES}.items():
            try:
                out[k] = os.stat(self.state_dir / fn).st_mtime
            except OSError:
                continue
        return out

    def readable(self) -> bool:
        return self.state_dir.exists() and os.access(self.state_dir, os.R_OK)

    def heartbeat_age(self) -> float | None:
        hb = self.get("heartbeat")
        return _age(hb.get("ts") or hb.get("at")) if isinstance(hb, dict) else None

    # ---- türetilmiş görünümler
    def futures_positions(self) -> list[dict]:
        led = self.get("futures_ledger") or {}
        pos = led.get("positions") or {}
        out: list[dict] = []
        items = pos.values() if isinstance(pos, dict) else pos
        for p in items:
            if not isinstance(p, dict):
                continue
            q = dict(p)
            q.setdefault("qty", q.get("units"))
            q.setdefault("entry_avg", q.get("entry"))
            q.setdefault("isolated_margin", q.get("margin"))
            if q.get("notional") in (None, 0, "0") and q.get("qty") and q.get("entry_avg"):
                try:
                    q["notional"] = float(q["qty"]) * float(q["entry_avg"])
                except (TypeError, ValueError):
                    pass
            if not q.get("liquidation_price") and q.get("qty") and q.get("isolated_margin") and q.get("entry_avg"):
                try:  # izole marj yaklaşımı (paper_futures.liq_price ile aynı)
                    move = float(q["isolated_margin"]) * 0.95 / max(float(q["qty"]), 1e-12)
                    e = float(q["entry_avg"])
                    q["liquidation_price"] = e - move if str(q.get("side", "LONG")).upper() == "LONG" else e + move
                except (TypeError, ValueError):
                    pass
            q.setdefault("amount_type", "notional")
            q.setdefault("fees_paid", q.get("fees", 0))
            if "funding_net" not in q:
                if "funding" in q:
                    q["funding_net"] = q.get("funding")
                else:
                    try:
                        q["funding_net"] = float(q.get("funding_received") or 0) - float(q.get("funding_paid") or 0)
                    except (TypeError, ValueError):
                        q["funding_net"] = 0
            out.append(q)
        return out

    # ---- CHART ANALYSIS V1: defterler (ana + strateji), spot pozisyonlar, karar kaydi (SALT OKUMA)
    def _spot_ledger_positions(self) -> list[dict] | None:
        """V3 motorunun spot defteri (`spot_ledger.json`, SpotLedger lot'lari) -> gosterim satirlari (motorun grafik
        kaydiyla AYNI kaynak/sekil: `chart_analysis.spot_position_to_dict`). Dosya yoksa None (eski portfolio.json'a dusulur)."""
        p = self.state_dir / STATE_FILES["spot_ledger"]
        if not p.exists():
            return None
        try:
            from ..accounting.spot_ledger import SpotLedger
            from ..chart_analysis import spot_position_to_dict
            led = SpotLedger.load(p, starting_cash=0.0)
            rows = [spot_position_to_dict(sym, d) for sym, d in (led.positions() or {}).items()]
            return [r for r in rows if r]
        except Exception:  # noqa: BLE001 - bozuk/eski sema: sessizce eski kaynaga dus
            return None

    def spot_history(self, symbol: str, limit: int = 100) -> list[dict]:
        """Ana botun spot defteri kapanmis islemleri (`spot_ledger.json` history), sembole gore, son `limit`."""
        led = self.get("spot_ledger") or {}
        rows = [h for h in (led.get("history") or []) if isinstance(h, dict) and h.get("symbol") == symbol]
        return rows[-int(limit):]

    def spot_source(self) -> str:
        """Ana botun spot pozisyon/geçmiş kaynağı: `spot_ledger.json` okunabiliyorsa o, değilse eski `portfolio.json`
        (panel `live.source` bunu GERÇEKTEN kullanılan dosya olarak bildirir; 2026-09-16 onarımı #1)."""
        return STATE_FILES["spot_ledger"] if self._spot_ledger_positions() is not None else STATE_FILES["portfolio"]

    def spot_positions(self) -> list[dict]:
        ledger_rows = self._spot_ledger_positions()
        if ledger_rows is not None:
            return ledger_rows
        pf = self.get("portfolio") or {}
        out: list[dict] = []
        pos = pf.get("positions") or {}
        for sym, p in (pos.items() if isinstance(pos, dict) else []):
            if not isinstance(p, dict):
                continue
            try:
                units = float(p.get("units") or p.get("qty") or 0)
            except (TypeError, ValueError):
                units = 0.0
            if units <= 0:
                continue
            out.append({"id": p.get("id") or ("SPOT:%s" % sym), "symbol": p.get("symbol") or sym, "side": "LONG", "qty": units,
                        "entry_avg": p.get("entry_price") or p.get("avg_cost") or p.get("entry"), "stop": (p.get("stop") or None),
                        "targets": [], "opened_at": p.get("entry_time") or p.get("opened_at"), "leverage": 1, "market_type": "SPOT"})
        return out

    def books(self) -> list[dict]:
        """Defter kayit listesi: ana bot + strategy_paper_index.json'daki kagit defterler (kimlik = state dizini)."""
        out = [{"book_id": "main", "name": "main", "label": "Ana bot", "state_dir": None}]
        idx = read_json(self.state_dir / "strategy_paper_index.json", default=None) or {}
        seen = {"main"}
        for b in (idx.get("books") or []):
            if not isinstance(b, dict):
                continue
            key = str(b.get("key") or "")
            if not key or key in seen or not all(ch.isalnum() or ch == "_" for ch in key):
                continue
            seen.add(key)
            name = str(b.get("name") or key)
            label = {"t2_trend_regime": "T2 · EMA200 trend", "m2_tsmom28": "M2 · 28g momentum",
                     "b1_box_fade": "B1 · Box (önceki gün aralığı, 5m)",
                     "d4_donchian_20_10": "D4 · 4h trend takibi (gözlem, kanıtlanmadı)",
                     "c4_candle_variations": "C4 · Mum varyasyonları (4h, PAPER)",
                     "c4s_candle_variations_strict": "C4S · Mum varyasyonları 4h (sıkı, PAPER)"}.get(name, name)
            out.append({"book_id": key, "name": name, "label": label, "state_dir": key, "summary_file": b.get("summary_file")})
        if len(out) == 1 and (self.state_dir / "strategy_paper" / "futures_ledger.json").exists():
            sp = self.get("strategy_paper") or {}
            out.append({"book_id": "strategy_paper", "name": str(sp.get("name") or "t2_trend_regime"), "label": "T2 · EMA200 trend", "state_dir": "strategy_paper"})
        # FORMASYON TRADER: yalniz GERCEKTEN varsa (ozet dosyasi yazilmis) listelenir — "calisiyormus gibi" gosterilmez.
        pt = self.get("pattern_trader") or {}
        if isinstance(pt, dict) and pt.get("key") == "pattern_trader" and "pattern_trader" not in seen:
            out.append({"book_id": "pattern_trader", "name": str(pt.get("name") or "pattern_v1"), "label": "Mum trader",
                        "state_dir": "pattern_trader", "summary_file": "pattern_trader.json"})
        return out

    def book_supports_market(self, book_id: str, market: str) -> bool:
        """Defter/piyasa birlesimi DESTEKLENIYOR mu? Ana bot hem spot hem futures tutar; kagit defterler
        (T2/M2/formasyon) yalniz USDⓈ-M perpetual'dir. Desteklenmeyen birlesimde panel BASKA HESABIN verisine
        sessizce DONMEZ, durumu acikca soyler (2026-09-17)."""
        return market != "spot" or book_id == "main"

    # ---- TERMINAL PANELI (2026-09-16): defter basina pozisyon/gecmis/ozkaynak — YETKILI defter dosyasindan
    def book_summary_file(self, book_id: str) -> dict | None:
        """Defterin kendi ozet dosyasi (strategy_paper*.json / pattern_trader.json). Ana bot icin None."""
        b = self.book(book_id)
        if b is None or b["book_id"] == "main":
            return None
        fn = str(b.get("summary_file") or ("%s.json" % b["book_id"]))
        if "/" in fn or "\\" in fn or ".." in fn:
            return None
        d = read_json(self.state_dir / fn, default=None)
        return d if isinstance(d, dict) else None

    def book_positions(self, book_id: str, *, market: str = "futures") -> list[dict]:
        """Defterin BUTUN acik pozisyonlari — yetkili pozisyon defterinden (aday/ret kaydi DEGIL).

        Tarama evreni disinda kalmis eski pozisyonlar da GORUNUR: defterde ne varsa o listelenir.
        `market="spot"` yalniz ana botta anlamlidir (kagit defterler USDM_PERP'tir; bkz. `book_supports_market`).

        BOS DEFTER != EKSIK DOSYA (2026-09-17): yetkili defter dosyasi VARSA ve `positions` MESRU olarak bossa
        liste bostur — eski ozetten pozisyon DIRILTILMEZ. Ozet projeksiyonuna yalniz dosya YOK/OKUNAMAZ iken
        dusulur.
        """
        if not self.book_supports_market(book_id, market):
            return []                                  # DESTEKLENMEYEN birlesim: baska hesabin verisine DONULMEZ
        if book_id == "main":
            return self.spot_positions() if market == "spot" else self.futures_positions()
        led = self.book_ledger(book_id)
        if isinstance(led, dict):
            pos = led.get("positions") or {}
        else:
            # Defter dosyasi YOK/OKUNAMAZ: defterin KENDI ozetindeki pozisyonlar (ayni yazar, ayni tick) kullanilir.
            # Bu bir ikinci muhasebe DEGILDIR: yalniz ayni kaydin projeksiyonu. Ikisi de yoksa liste bostur.
            pos = (self.book_summary_file(book_id) or {}).get("positions") or {}
        items = pos.items() if isinstance(pos, dict) else [(p.get("symbol"), p) for p in pos if isinstance(p, dict)]
        out = []
        for sym, p in items:
            if not isinstance(p, dict):
                continue
            q = dict(p)
            q["symbol"] = str(q.get("symbol") or sym)
            q.setdefault("entry_avg", q.get("entry"))
            q.setdefault("qty", q.get("units"))
            q.setdefault("isolated_margin", q.get("margin"))
            q["book_id"] = book_id
            out.append(q)
        return out

    def book_trades(self, book_id: str, limit: int = 500, *, market: str = "futures") -> list[dict]:
        """Defterin KAPANMIS islemleri (en yeni SONDA), en fazla `limit` satir. BOS GECMIS != EKSIK DOSYA:
        yetkili defter okunabiliyorsa bos gecmis BOS doner; `history_tail` projeksiyonuna yalniz dosya
        YOK/OKUNAMAZ iken dusulur. TOPLAM sayi/`realized` icin `book_history_totals` kullanilir — son N
        kapanistan tum gecmis toplami URETILMEZ."""
        return self._book_history(book_id, market=market)[-int(limit):]

    def _book_history(self, book_id: str, *, market: str = "futures") -> list[dict]:
        """Defterin TAM kapanmis islem gecmisi (kesilmemis, en yeni SONDA). Kaynak: yetkili defter, yoksa ozet
        projeksiyonu. ANA BOT icin gecmis PIYASAYA gore ayrilir (`trades()` futures + spot kayitlarini BIRLIKTE
        tasir ve yeniden ESKIYE dogru siralidir): spot kapanisi futures kaydi gibi etiketlenmez (2026-09-17)."""
        if not self.book_supports_market(book_id, market):
            return []
        if book_id == "main":
            want = "spot" if market == "spot" else "futures"
            rows = [t for t in self.trades() if market_of(t) == want]
            return list(reversed(rows))                # `trades()` en YENI basta doner; sozlesme: en yeni SONDA
        led = self.book_ledger(book_id)
        if isinstance(led, dict):
            return [dict(h, book_id=book_id) for h in (led.get("history") or []) if isinstance(h, dict)]
        return [dict(h, book_id=book_id, from_summary_tail=True)
                for h in ((self.book_summary_file(book_id) or {}).get("history_tail") or []) if isinstance(h, dict)]

    def book_history_totals(self, book_id: str, *, market: str = "futures") -> dict[str, Any]:
        """KAPANMIS islemlerin TAM toplami (gorunen son N satirdan DEGIL).

        `complete=False` ise yetkili defter okunamamistir ve kaynak yalnizca ozet kuyrugudur (`history_tail`):
        toplam bir ALT SINIRDIR, panel bunu boyle gostermelidir. HIC KAPANIS OLMAYAN gecerli bir defterde
        `realized` 0.0'dir — `None` (bilinmiyor) DEGIL: "olculdu ve sifir" ile "kaynak eksik" ayri seylerdir.
        """
        complete = book_id == "main" or isinstance(self.book_ledger(book_id), dict)
        rows = self._book_history(book_id, market=market)
        vals = [_f(t.get("net_pnl")) for t in rows]
        known_all = all(v is not None for v in vals)
        return {"closed": len(rows), "realized": round(sum(v for v in vals if v is not None), 6) if known_all else None,
                "wins": sum(1 for v in vals if v is not None and v > 0) if known_all else None,
                "losses": sum(1 for v in vals if v is not None and v <= 0) if known_all else None,
                "complete": complete, "source": "ledger" if complete else "summary_history_tail"}

    def book(self, book_id: str) -> dict | None:
        return next((b for b in self.books() if b["book_id"] == book_id), None)

    def book_ledger(self, book_id: str) -> dict | None:
        b = self.book(book_id)
        if b is None:
            return None
        if b["book_id"] == "main":
            return self.get("futures_ledger")
        return read_json(self.state_dir / b["state_dir"] / "futures_ledger.json", default=None)

    def book_position(self, book_id: str, symbol: str) -> dict | None:
        led = self.book_ledger(book_id) or {}
        pos = led.get("positions") or {}
        items = pos.items() if isinstance(pos, dict) else [(p.get("symbol"), p) for p in pos if isinstance(p, dict)]
        for sym, p in items:
            if isinstance(p, dict) and str(p.get("symbol") or sym) == symbol:
                q = dict(p)
                q.setdefault("entry_avg", q.get("entry"))
                q.setdefault("qty", q.get("units"))
                return q
        return None

    def book_trade(self, book_id: str, trade_id: str, *, market: str = "futures") -> dict | None:
        """Bir KAPANMIS islemi KENDI defterinde ve KENDI piyasasinda bulur (kimlik korunur: baska defterin ya da
        baska piyasanin kaydina dusmez)."""
        if not trade_id:
            return None
        for t in self._book_history(book_id, market=market):
            if str(t.get("id") or t.get("trade_id") or "") == str(trade_id):
                return t
        return None

    def book_history(self, book_id: str, symbol: str, limit: int = 100) -> list[dict]:
        led = self.book_ledger(book_id) or {}
        rows = [h for h in (led.get("history") or []) if isinstance(h, dict) and h.get("symbol") == symbol]
        return rows[-int(limit):]

    def book_entry_features(self, book_id: str, trade_id: str | None) -> dict | None:
        b = self.book(book_id)
        if b is None or not trade_id:
            return None
        p = (self.state_dir / "trade_memory.jsonl") if b["book_id"] == "main" else (self.state_dir / b["state_dir"] / "trade_memory.jsonl")
        if not p.exists():
            return None
        try:
            for ln in iter_text_lines(p):
                if trade_id not in ln:
                    continue
                try:
                    d = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict) and d.get("trade_id") == trade_id:
                    return d.get("features") or (d.get("entry") or {}).get("features") or None
        except OSError:
            return None
        return None

    def last_decision(self, symbol: str) -> dict | None:
        r = self.get("risk") or {}
        out = None
        for e in (r.get("last_decisions") or []):
            if isinstance(e, dict) and e.get("symbol") == symbol:
                out = e
        return out

    def futures_equity(self) -> float | None:
        led = self.get("futures_ledger") or {}
        for k in ("equity", "wallet_balance"):
            if led.get(k) is not None:
                try:
                    return float(led[k])
                except (TypeError, ValueError):
                    continue
        return None

    def spot_equity(self) -> float | None:
        pf = self.get("portfolio") or {}
        if not pf:
            return None
        try:
            cash = float(pf.get("cash", 0) or 0)
        except (TypeError, ValueError):
            cash = 0.0
        val = 0.0
        for p in (pf.get("positions") or {}).values() if isinstance(pf.get("positions"), dict) else []:
            try:
                val += float(p.get("units", 0)) * float(p.get("last_price") or p.get("entry_price") or 0)
            except (TypeError, ValueError):
                continue
        return round(cash + val, 4)

    def trades(self) -> list[dict]:
        out: list[dict] = []
        led = self.get("futures_ledger") or {}
        for h in led.get("history") or []:
            if isinstance(h, dict):
                q = dict(h); q.setdefault("market_type", "futures"); out.append(q)
        pf = self.get("portfolio") or {}
        for h in pf.get("history") or []:
            if isinstance(h, dict):
                q = dict(h); q.setdefault("market_type", "spot"); q.setdefault("id", q.get("trade_id") or f"spot-{q.get('symbol', '')}-{q.get('exit_time') or q.get('closed_at') or ''}"); out.append(q)
        out.sort(key=lambda t: str(t.get("closed_at") or t.get("exit_time") or ""), reverse=True)
        return out

    def trade(self, trade_id: str) -> dict | None:
        for t in self.trades():
            if str(t.get("id")) == trade_id:
                return t
        return None

    def orders(self) -> list[dict]:
        o = self.get("orders")
        if isinstance(o, list):
            return [x for x in o if isinstance(x, dict)]
        if isinstance(o, dict) and isinstance(o.get("orders"), list):
            return [x for x in o["orders"] if isinstance(x, dict)]
        led = self.get("futures_ledger") or {}
        ent = led.get("entries") or []
        return [e for e in ent if isinstance(e, dict)][-300:][::-1]

    def coin_heads(self) -> list[dict]:
        ch = self.get("coin_heads") or {}
        return [h for h in (ch.get("heads") or []) if isinstance(h, dict)]

    def coin_head(self, base: str) -> dict | None:
        b = base.upper()
        for h in self.coin_heads():
            if str(h.get("symbol", "")).upper().split("/")[0] == b:
                return h
        return None

    def brief(self, base: str) -> dict | None:
        ag = self.get("agents") or {}
        for b in ag.get("briefs") or []:
            if str(b.get("symbol", "")).upper().split("/")[0] == base.upper():
                return b
        return None

    def killswitch_state(self) -> str:
        ks = self.get("killswitch")
        if isinstance(ks, dict) and ks.get("state"):
            return str(ks["state"])
        r = self.get("risk") or {}
        return str((r.get("killswitch") or {}).get("state") or "ARMED")

    def mode(self) -> str:
        m = self.get("mode")
        return str(m.get("mode")) if isinstance(m, dict) and m.get("mode") else "PAPER"

    # ---- canlılık / tazelik
    def file_age(self, name: str) -> float | None:
        """State dosyasının disk yaşı (sn). Fiyat tazeliği bundan gelir — tarayıcı Binance'a GİTMEZ."""
        fn = {**STATE_FILES, **JSONL_FILES}.get(name)
        if not fn:
            return None
        try:
            return max(0.0, utc_now().timestamp() - os.stat(self.state_dir / fn).st_mtime)
        except OSError:
            return None

    def price_age_s(self) -> float | None:
        """Mark fiyatlarının yaşı = defterin son yazılma yaşı (worker'ın güvenli/önbellekli kaynağı).

        STRATEJİ TURU yaşıyla KARIŞTIRILMAZ: tur 4 saatte bir, tick/defter yazımı çok daha sık olur.
        """
        return self.file_age("futures_ledger")

    def heads_age_s(self) -> float | None:
        return _age((self.get("coin_heads") or {}).get("generated_at"))

    def risk_age_s(self) -> float | None:
        """`risk.json` anlık görüntüsünün yaşı (sn). Fiyat yaşından AYRI kavramdır.

        `risk.json` STRATEJİ TURUNDA yazılır (`engine_v3._persist_risk_state` → kök `generated_at`),
        fiyatlar ise her tickte tazelenir; ikisi dakikalarca ayrışabilir. Damga yoksa `None` döner
        ve panel «Veri yaşı bilinmiyor» yazar — taze GİBİ gösterilmez.
        """
        return _age((self.get("risk") or {}).get("generated_at"))

    def marks(self) -> dict[str, Any]:
        """Sembol → worker'ın kaydettiği son fiyat. Panel ASLA borsaya doğrudan bağlanmaz."""
        out: dict[str, Any] = {}
        for p in self.futures_positions():
            lp = p.get("last_price")
            if lp not in (None, "", 0, "0"):
                out[str(p.get("symbol") or "")] = lp
        return out

    def max_drawdown_pct(self) -> Any:
        """`risk.json` içindeki drawdown. Risk motoru bunu `exposure` ALTINA yazar
        (`RiskEngine.snapshot()` → `{"exposure": {"drawdown_pct": ...}}`); eski kod yalnız kök
        seviyeye baktığı için değer bulunamıyor ve panelde «Veri yok» görünüyordu."""
        r = self.get("risk") or {}
        for scope in (r.get("exposure"), r.get("state"), r):
            if not isinstance(scope, dict):
                continue
            for key in ("drawdown_pct", "max_drawdown_pct"):
                v = scope.get(key)
                if v is not None:
                    return v
        return None

    def last_run_age(self) -> float | None:
        ages = [a for a in (
            _age((self.get("coin_heads") or {}).get("generated_at")),
            _age((self.get("agents") or {}).get("generated_at")),
            _age((self.get("scan") or {}).get("generated_at")),
        ) if a is not None]
        return min(ages) if ages else None

    def snapshot_telemetry(self) -> dict[str, Any]:
        """FeatureSnapshotV3 uretim sayaclari (bkz. learn/telemetry.py). Dosya yoksa sifir sayaclar."""
        from ..learn.telemetry import COUNTER_NAMES
        d = self.get("snapshot_telemetry") or {}
        return {"counters": {k: int((d.get("counters") or {}).get(k, 0) or 0) for k in COUNTER_NAMES},
                "last_failure_code": str(d.get("last_failure_code") or ""),
                "last_failure_at": str(d.get("last_failure_at") or "")}

    def count_jsonl_lines(self, name: str) -> int:
        """Boş olmayan fiziksel satır sayısı — JSON AYRIŞTIRMAZ (ucuz akış sayımı)."""
        fn = JSONL_FILES.get(name)
        if not fn:
            return 0
        p = self.state_dir / fn
        if not p.exists():
            return 0
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                return sum(1 for ln in fh if ln.strip())
        except OSError:
            return 0

    def experience_index(self, dirname: str = "experience_index",
                         shadow_archive_dirname: str = "shadow_archive") -> dict[str, Any]:
        """Uzun vadeli deneyim indeksinin SALT OKUNUR durumu — shard/segment AÇMAZ.

        `retrieval_scope` DÜRÜSTTÜR: indeks yoksa/boşsa `HOT_ONLY`, bozuksa `DEGRADED`;
        yalnız gerçekten indekslenmiş satır varsa `HOT_PLUS_INDEXED_HISTORY`. Eksik/bozuk
        manifest 500 ÜRETMEZ.
        """
        base: dict[str, Any] = {"available": False, "indexed_experiences": 0,
                                "indexed_real": 0, "indexed_shadow": 0,
                                "processed_segments": 0, "corrupt_segments": 0,
                                "skipped_rows": 0, "oldest_label_ms": None,
                                "newest_label_ms": None, "index_lag_segments": 0,
                                "last_refresh_at": None, "last_rebuild_at": None,
                                "index_health": "ABSENT", "last_index_error": None,
                                "retrieval_scope": "HOT_ONLY",
                                "no_lookahead": "AS_OF_ENFORCED_FAIL_CLOSED",
                                "rebuildable_from_archive": True}
        mpath = self.state_dir / dirname / "manifest.json"
        if not mpath.exists():
            return base
        try:
            doc = read_json(mpath, default=None)
        except Exception:  # noqa: BLE001
            doc = None
        if not isinstance(doc, dict):
            base.update({"index_health": "DEGRADED", "retrieval_scope": "DEGRADED",
                         "last_index_error": "INDEX_MANIFEST_UNREADABLE"})
            return base

        def _i(v: Any) -> int:
            return int(v) if isinstance(v, (int, float)) else 0

        t = doc.get("totals") if isinstance(doc.get("totals"), dict) else {}
        processed = [s for s in (doc.get("processed") or []) if isinstance(s, dict)]
        corrupt = [s for s in (doc.get("corrupt_segments") or [])]
        health = str(doc.get("health") or "EMPTY")
        # Gecikme: arşivde olup indekste olmayan segment sayısı (iki manifest, segment açılmaz).
        lag = 0
        try:
            adoc = read_json(self.state_dir / shadow_archive_dirname / "manifest.json",
                             default=None)
            if isinstance(adoc, dict):
                n_arc = len([s for s in (adoc.get("segments") or []) if isinstance(s, dict)])
                lag = max(0, n_arc - len(processed))
        except Exception:  # noqa: BLE001
            lag = 0
        if lag > 0 and health == "OK":
            health = "STALE"
        rows = _i(t.get("rows"))
        # Aggregate senkronu: aggregates.json'daki uygulanmış segment kümesi, manifestteki
        # işlenmiş kümeyle birebir aynıysa tam-geçmiş toplam hafızası SAĞLIKLIDIR.
        agg_synced = False
        agg_outcomes = 0
        try:
            adoc2 = read_json(self.state_dir / dirname / "aggregates.json", default=None)
            if isinstance(adoc2, dict):
                applied = {str(x) for x in (adoc2.get("applied_segments") or [])}
                agg_synced = applied == {str(x.get("segment_id")) for x in processed}
                agg_outcomes = _i((adoc2.get("book") or {}).get("total_added"))
        except Exception:  # noqa: BLE001
            agg_synced = False
        if health in ("DEGRADED", "FAILED"):
            scope = "DEGRADED"
        elif rows > 0 and health == "OK" and lag == 0 and agg_synced:
            scope = "FULL_HISTORY_BOUNDED"
        elif rows > 0 and health in ("OK", "STALE"):
            scope = "HOT_PLUS_RECENT_INDEX"
        else:
            scope = "HOT_ONLY"
        base.update({"available": True, "indexed_experiences": rows,
                     "indexed_real": _i(t.get("real")), "indexed_shadow": _i(t.get("shadow")),
                     "processed_segments": len(processed), "corrupt_segments": len(corrupt),
                     "skipped_rows": sum(_i(s.get("n_skipped")) for s in processed),
                     "oldest_label_ms": t.get("oldest_label_ms"),
                     "newest_label_ms": t.get("newest_label_ms"),
                     "index_lag_segments": lag,
                     "last_refresh_at": doc.get("last_refresh_at"),
                     "last_rebuild_at": doc.get("last_rebuild_at"),
                     "index_health": health, "last_index_error": doc.get("last_error"),
                     "aggregate_synced": agg_synced, "aggregate_outcomes": agg_outcomes,
                     "retrieval_scope": scope})
        return base

    def decision_retention(self, dirname: str = "decision_archive") -> dict[str, Any]:
        """Aktif günlük + kayıpsız arşivin SALT OKUNUR saklama özeti.

        Manifest eksik/eski/bozuk olsa bile `available=False` ile döner; ASLA istisna sızdırmaz
        (dashboard 500 üretmemelidir). Segment dosyaları AÇILMAZ — maliyet O(1).
        """
        hot = self.count_jsonl_lines("decision_journal")
        base: dict[str, Any] = {"hot_records": hot, "archived_records": 0,
                                "lifetime_records": hot, "n_segments": 0,
                                "oldest_ts": None, "newest_ts": None,
                                "last_rotation_at": None, "archive_health": "ABSENT",
                                "last_archive_error": None,
                                "retention_policy": "UNKNOWN", "deleted_segments": 0,
                                "silent_deletion": False, "archive_available": False}
        # Retrieval kapsamı KARAR ARŞİVİNİN varlığından TÜRETİLEMEZ: canlı retrieval'ın kaynağı
        # deneyim indeksidir. Kapsam gerçek indeks durumundan okunur (dürüst raporlama).
        base["retrieval_scope"] = self.experience_index().get("retrieval_scope", "HOT_ONLY")
        mpath = self.state_dir / dirname / "manifest.json"
        if not mpath.exists():
            return base
        try:
            doc = read_json(mpath, default=None)
        except Exception:  # noqa: BLE001 — bozuk manifest 500 ÜRETMEZ
            doc = None
        if not isinstance(doc, dict):
            base["archive_health"] = "DEGRADED"
            base["last_archive_error"] = "MANIFEST_UNREADABLE"
            return base
        t = doc.get("totals") if isinstance(doc.get("totals"), dict) else {}

        def _i(v: Any) -> int:
            return int(v) if isinstance(v, (int, float)) else 0

        archived = _i(t.get("records"))
        base.update({"archived_records": archived, "lifetime_records": hot + archived,
                     "archived_decisions": _i(t.get("decisions")),
                     "archived_outcomes": _i(t.get("outcomes")),
                     "archive_bytes_compressed": _i(t.get("bytes_compressed")),
                     "n_segments": _i(t.get("segments")),
                     "oldest_ts": t.get("first_ts"), "newest_ts": t.get("last_ts"),
                     "last_rotation_at": doc.get("last_rotation_at"),
                     "archive_health": doc.get("health") or "EMPTY",
                     "last_archive_error": doc.get("last_error"),
                     "retention_policy": doc.get("retention_policy") or "UNKNOWN",
                     "deleted_segments": _i(doc.get("deleted_segments")),
                     "pending_trim": bool(doc.get("pending_trim")),
                     "archive_available": True})
        return base

    def coin_memory(self, base: str) -> dict[str, Any]:
        """Coin'e özel hiyerarşik bellek özeti — SALT OKUNUR, eksik veride 500 YOK.

        Gerçek kapanışlar (TradeMemory) + gölge sonuçlar (aktif defter) + tam-geçmiş
        toplamları (experience_index/aggregates.json L2/L3 hücreleri) + son karar etkisi.
        """
        b = str(base).upper().split("/")[0]
        sym = f"{b}/USDT"
        out: dict[str, Any] = {"symbol": sym, "available": False,
                               "real": {"n": 0}, "shadow": {"n": 0},
                               "aggregate": None, "by_direction": {},
                               "last_influence": None, "date_range": {},
                               "consistency": None}

        def _num(x: Any) -> float | None:
            try:
                v = float(x)
                return v if v == v else None
            except (TypeError, ValueError):
                return None

        # --- gerçek kapanışlar
        rs: list[float] = []
        maes: list[float] = []
        mfes: list[float] = []
        exits: dict[str, int] = {}
        first = last = None

        def _mem_row(row: dict) -> dict | None:
            # 512M panel (2026-09-28, öğrenme modu; dördüncü doğrulama turu): yalnız bu sembolün satırları ve aşağıda okunan
            # alanlar tutulur — 4000 satırlık kuyrukta tam ayrıştırılmış ~74 KB'lık giriş satırları BİRİKMEZ (sonuç aynı)
            if str(row.get("symbol") or "") != sym:
                return None
            keep = {k: row[k] for k in ("symbol", "kind", "recorded_at") if k in row}
            if "outcome" in row:
                o = row["outcome"]
                keep["outcome"] = ({k: o[k] for k in ("r_multiple", "mae_pct", "mfe_pct", "exit_reason") if k in o}
                                   if isinstance(o, dict) else o)
            return keep

        for row in self.tail_jsonl("trade_memory", 4000, needle=sym, project=_mem_row):
            if str(row.get("symbol") or "") != sym:
                continue
            if row.get("kind") == "exit":
                o = row.get("outcome") or {}
                r = _num(o.get("r_multiple"))
                if r is not None:
                    rs.append(r)
                if _num(o.get("mae_pct")) is not None:
                    maes.append(float(o["mae_pct"]))
                if _num(o.get("mfe_pct")) is not None:
                    mfes.append(float(o["mfe_pct"]))
                er = str(o.get("exit_reason") or "?")
                exits[er] = exits.get(er, 0) + 1
            ts = str(row.get("recorded_at") or "")
            if ts:
                first = ts if first is None or ts < first else first
                last = ts if last is None or ts > last else last
        out["real"] = {"n": len(rs),
                       "avg_r": round(sum(rs) / len(rs), 4) if rs else None,
                       "wins": sum(1 for r in rs if r > 0),
                       "losses": sum(1 for r in rs if r < 0),
                       "avg_mae_pct": round(sum(maes) / len(maes), 3) if maes else None,
                       "avg_mfe_pct": round(sum(mfes) / len(mfes), 3) if mfes else None,
                       "exit_reasons": exits, "weight": 1.0}
        # --- aktif gölge
        sh = self.get("shadow_book") or {}
        sh_rows = [t for t in (sh.get("trades") or [])
                   if isinstance(t, dict) and str(t.get("symbol")) == sym]
        sh_lab = [t for t in sh_rows if isinstance(t.get("outcome"), dict)]
        sh_rs = [_num((t.get("outcome") or {}).get("r_multiple")) for t in sh_lab]
        sh_rs = [r for r in sh_rs if r is not None]
        # NET (2026-09-29, maliyet sapması): `avg_r` brüt kalır (eski gölgeler yalnız brüt); net etiketli (cf_label_v2)
        # karşı-olgusalların ortalaması ayrıca — ikisi tek ortalamada KARIŞTIRILMAZ
        sh_net = [r for r in (_num((t.get("outcome") or {}).get("r_net")) for t in sh_lab) if r is not None]
        out["shadow"] = {"n": len(sh_rows), "labeled": len(sh_lab),
                         "avg_r": round(sum(sh_rs) / len(sh_rs), 4) if sh_rs else None,
                         "avg_r_net": round(sum(sh_net) / len(sh_net), 4) if sh_net else None, "n_net": len(sh_net),
                         "weight": "shadow_weight×fidelity (gerçekten DAİMA düşük)"}
        # --- tam-geçmiş toplamları (aggregates.json — L3 sembol, L2 sembol|yön|setup)
        try:
            adoc = read_json(self.state_dir / "experience_index" / "aggregates.json",
                             default=None)
            cells = ((adoc or {}).get("book") or {}).get("cells") or {}
            l3 = cells.get(f"3|{sym.replace('|', '_')}") or {}
            n = w = wr = 0.0
            months = sorted(l3)
            for st_ in l3.values():
                n += float(st_.get("n") or 0)
                w += float(st_.get("w") or 0)
                wr += float(st_.get("wr") or 0)
            if n:
                out["aggregate"] = {"n": int(n), "mean_r": round(wr / w, 4) if w else None,
                                    "months": len(months),
                                    "first_month": months[0] if months else None,
                                    "last_month": months[-1] if months else None}
            by_dir: dict[str, Any] = {}
            for key, mrows in cells.items():
                if not key.startswith("2|"):
                    continue
                parts = key.split("|")
                if len(parts) >= 4 and parts[1] == sym.replace("|", "_"):
                    dn, wn, wrn = 0.0, 0.0, 0.0
                    for st_ in mrows.values():
                        dn += float(st_.get("n") or 0)
                        wn += float(st_.get("w") or 0)
                        wrn += float(st_.get("wr") or 0)
                    d = by_dir.setdefault(parts[2], {"n": 0, "setups": {}})
                    d["n"] += int(dn)
                    d["setups"][parts[3]] = {"n": int(dn),
                                             "mean_r": round(wrn / wn, 4) if wn else None}
            out["by_direction"] = by_dir
        except Exception:  # noqa: BLE001
            pass
        # --- son karar etkisi + tarih aralığı + tutarlılık
        for row in reversed(self.tail_jsonl("decision_journal", 2000, needle=sym,
                                            project=lambda r: r if r.get("symbol") == sym else None)):
            if row.get("symbol") == sym and row.get("learning_influence"):
                out["last_influence"] = row["learning_influence"]
                out["last_decision_ts"] = row.get("decision_ts")
                out["last_why_tr"] = row.get("why_summary_tr")
                break
        out["date_range"] = {"first": first, "last": last}
        all_rs = rs + sh_rs
        if all_rs:
            pos = sum(1 for r in all_rs if r > 0)
            out["consistency"] = round(abs(2 * pos / len(all_rs) - 1), 4)
        out["available"] = bool(rs or sh_rows or out["aggregate"])
        return out

    def shared_experience(self) -> dict[str, Any] | None:
        """ORTAK DENEYİM kartı (2026-09-29) — O(1), salt okur: sayımlar + son toplama adımı (`status.json`) ve son rapor
        taraması + en çok gözlemli hücreler (`report_summary.json`). Katman hiç çalışmadıysa None (kart basılmaz)."""
        d = self.state_dir / XP_DIR
        st = _xp_json(d / XP_STATUS_FILE)
        if st is None:
            return None
        c = st.get("counters") if isinstance(st.get("counters"), dict) else {}
        store = st.get("store") if isinstance(st.get("store"), dict) else {}
        sm = _xp_json(d / XP_SUMMARY_FILE)
        if sm is not None and sm.get("schema") != XP_SUMMARY_SCHEMA:
            sm = None
        try:
            disk_mb = round(float(store.get("disk_bytes") or 0) / 1048576.0, 2)
        except (TypeError, ValueError):
            disk_mb = None
        cells = []
        for cell in ((sm or {}).get("top_cells") or [])[:XP_TOP_CELLS]:
            if isinstance(cell, dict):
                cells.append({"group": cell.get("group"), "book_name": cell.get("book_name"),
                              "setup_type": cell.get("setup_type"), "side": cell.get("side"),
                              "dims": dict(cell.get("dims") or {}), "real": _xp_col(cell.get("real")),
                              "cf": _xp_col(cell.get("cf"))})
        counts = (sm or {}).get("counts") if isinstance((sm or {}).get("counts"), dict) else {}
        return {"banner": XP_BANNER_TR, "state": st.get("state"), "last_step_at": st.get("last_step_at"),
                "steps": st.get("steps"), "step_ms_p50": st.get("step_ms_p50"), "step_ms_p95": st.get("step_ms_p95"),
                "drafts": st.get("drafts"), "rows_total": c.get("rows_total"),
                "rows_by_kind": dict(c.get("rows_by_kind") or {}), "rows_by_book": dict(c.get("rows_by_book") or {}),
                "snapshot_status_mix": dict(c.get("snapshot_status_mix") or {}), "errors_total": c.get("errors_total"),
                "breaker_tripped": bool((st.get("breaker") or {}).get("tripped")) if isinstance(st.get("breaker"), dict) else False,
                "disk_mb": disk_mb, "hot_lines": store.get("hot_lines"),
                "last_sweep_at": (sm or {}).get("generated_at"), "sweep_params": (sm or {}).get("params"),
                "sweep_counts": {k: counts.get(k) for k in ("real_closed_net", "real_open", "cf_net", "cf_net_a15",
                                                           "cf_gross_legacy", "cf_pending")} if counts else None,
                "top_cells": cells}

    def learning_research(self) -> dict[str, Any]:
        """PAPER araştırma politikası özeti — hangi aday aktif, neyi değiştirdi, sonucu ne.

        `auto_promotion_possible` her zaman False: bu katman CHAMPION/LIVE üretemez.
        """
        d = self.get("research_policy") or {}
        recs = d.get("records") or []
        return {"active_policy_id": d.get("active_policy_id"),
                "active_rationale": d.get("active_rationale"),
                "active_changed_params": d.get("active_changed_params") or [],
                "active_stats": d.get("active_stats"),
                "shadow_policy_id": d.get("shadow_policy_id"),
                "shadow_stats": d.get("shadow_stats"),
                "quarantined": d.get("quarantined") or [],
                "counts": d.get("counts") or {},
                "auto_promotion_possible": bool(d.get("auto_promotion_possible", False)),
                "retired": [{"policy_id": r.get("policy_id"), "reason": r.get("retired_reason")}
                            for r in recs if r.get("state") == "RETIRED" and r.get("retired_reason")][-5:],
                "gates": d.get("gates") or {}}

    def decision_funnel(self) -> dict[str, Any]:
        """Karar hunisi + kayan 24 saat. `trades_opened_24h` YALNIZ gözlem metriğidir, kapı değildir.

        `daily_trade_cap`/`per_run_trade_cap` her zaman None: sistemde sabit işlem sayısı kotası YOK.
        """
        d = self.get("decision_funnel") or {}
        return {"run": d.get("run") or {}, "rolling_24h": d.get("rolling_24h") or {},
                "trades_opened_24h": int(d.get("trades_opened_24h", 0) or 0),
                "hard_block_rate": d.get("hard_block_rate"), "no_trade_rate": d.get("no_trade_rate"),
                "opportunity_cost_count": int(d.get("opportunity_cost_count", 0) or 0),
                "opportunity_cost": d.get("opportunity_cost") or [],
                "daily_trade_cap": None, "per_run_trade_cap": None, "at": d.get("at")}

    def overview(self) -> dict[str, Any]:
        health = self.get("health") or {}
        llm = self.get("llm_budget") or {}
        heads = self.coin_heads()
        positions = self.futures_positions()
        # «Coin head'ler» tablosu AÇIK POZİSYON LİSTESİ DEĞİLDİR; fakat açık pozisyonların hepsi
        # ZORUNLU olarak yer alır. Eski `sorted(heads)[:10]` top-N kesimi açık pozisyonları
        # düşürüyordu (bkz. views.coin_head_scope). HTML ve API AYNI kaynaktan beslenir.
        from .views import coin_head_scope
        scope = coin_head_scope(heads, positions)
        return {
            "generated_at": utc_now().isoformat(timespec="seconds"),
            "equity_futures": self.futures_equity(),
            "equity_spot": self.spot_equity(),
            "open_positions": positions,
            "killswitch": self.killswitch_state(),
            "mode": self.mode(),
            "health": health.get("state") or "UNKNOWN",
            "health_summary": health.get("summary") or "",
            "heartbeat_age_s": self.heartbeat_age(),
            "last_run_age_s": self.last_run_age(),
            "llm_spent_usd_today": llm.get("spent_usd"),
            "snapshot_telemetry": self.snapshot_telemetry(),
            "learning_research": self.learning_research(),
            "decision_funnel": self.decision_funnel(),
            "chief": (self.get("coin_heads") or {}).get("chief") or (self.get("agents") or {}).get("chief") or {},
            "top_heads": scope["heads"],
            "coin_head_scope": scope,
            "open_positions_total": scope["open_positions_total"],
            "open_positions_shown": scope["open_positions_shown"],
            "missing_open_symbols": scope["missing_open_symbols"],
            "coverage_complete": scope["coverage_complete"],
            "price_age_s": self.price_age_s(),
            "heads_age_s": self.heads_age_s(),
        }

    def fee_schedule(self) -> Any:
        """Defterin ücret tarifesi — tahmini kapanış ücreti bundan hesaplanır."""
        return (self.get("futures_ledger") or {}).get("fees")

    def view_model(self, *, stale_price_s: int = 90, stale_run_s: int = 2400,
                   tz_label: str = "UTC") -> dict[str, Any]:
        """Panel + Telegram için TEK kanonik görünüm (bkz. `dashboard.views.build`)."""
        from .views import Freshness, build
        fresh = Freshness(price_age_s=self.price_age_s(), run_age_s=self.last_run_age(),
                          heads_age_s=self.heads_age_s(), heartbeat_age_s=self.heartbeat_age(),
                          stale_price_s=stale_price_s, stale_run_s=stale_run_s, tz_label=tz_label)
        return build(self.futures_positions(), self.trades(),
                     (self.get("coin_heads") or {}).get("chief") or (self.get("agents") or {}).get("chief") or {},
                     marks=self.marks(), fees=self.fee_schedule(),
                     today=utc_now().date().isoformat(), max_drawdown_pct=self.max_drawdown_pct(),
                     freshness=fresh,
                     futures_equity=self.futures_equity(), spot_equity=self.spot_equity(),
                     futures_ledger_doc=self.get("futures_ledger"),
                     spot_ledger_doc=self.get("spot_ledger"),
                     risk_state=self.get("risk"), as_of=utc_now().isoformat(timespec="seconds"),
                     # Risk anlık görüntüsü de strateji turunda yazılır → AYNI tazelik eşiği
                     # (`stale_run_s`) kullanılır; yeni/keyfî bir eşik UYDURULMAZ.
                     risk_age_s=self.risk_age_s(), risk_stale_s=stale_run_s)


def _evidence(self, base: str) -> dict | None:
    import json as _j
    b = base.upper().replace("/", "_")
    for name in (b, f"{b}_USDT"):
        p = Path(self.state_dir) / "evidence" / f"{name}.json"
        if p.exists():
            try:
                return _j.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                return None
    return None


StateReader.evidence = _evidence

__all__ = ["StateReader", "STATE_FILES", "JSONL_FILES"]
