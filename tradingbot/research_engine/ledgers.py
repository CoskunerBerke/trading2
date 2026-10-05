"""Ledger'ların HAM, salt-okunur okunması (§4.1) — `futures_ledger.json` + `state/spot_ledger.json`.

* Her dosya `open(path, "r")` ile açılır ve `json.load` ile okunur; motor ledger sınıflarını
  (`FuturesLedgerV2`/`SpotLedger`) yüklemez, hiçbir ledger nesnesi kurmaz, hiçbir şey kaydetmez.
* `schema_version` bilinen değerlerden biri değilse (bugün yalnız 2) ya da defter türüne özgü zorunlu alanlar yoksa
  defter o gece `SCHEMA_UNKNOWN` ile atlanır (fail-closed). Okunamayan/bozuk JSON `UNREADABLE`'dır.
* Defter listesi `scripts/bot_scorecard.find_books` mantığıyla bulunur (bilinen klasörler + `state/*/futures_ledger.json`)
  **ve** `state/spot_ledger.json` açıkça eklenir (`find_books` yalnız vadeli dosyayı tarar). Betik bir paket olmadığı
  için mantık burada AYNEN yinelenir; eşitliği bir özellik testi denetler.
* Ana bot iki alt deftere ayrılır (§4.2): `main_fut` (`state/futures_ledger.json`) ve `main_spot`
  (`state/spot_ledger.json`); raporlarda "Ana bot" altında birlikte ve ayrı satırlar olarak görünür.

Zaman ve sayı yardımcıları da buradadır: ledger zamanları ISO-8601 UTC (`+00:00`), para alanları Decimal'ın kayıpsız
string gösterimidir; motor toplamları Decimal ile yapar (1e-6 uzlaştırması kayan nokta hatasına bırakılmaz).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

UTC = timezone.utc
FUTURES_FILE = "futures_ledger.json"
SPOT_FILE = "spot_ledger.json"
#: Bilinen ledger şeması (accounting/models.py `SCHEMA_VERSION`). Yeni bir sürüm motor güncellenene kadar ATLANIR.
KNOWN_SCHEMA_VERSIONS = frozenset({2})

KIND_FUTURES, KIND_SPOT = "futures", "spot"
BOOK_MAIN_FUT, BOOK_MAIN_SPOT, BOOK_MAIN = "main_fut", "main_spot", "main"

LEDGER_OK, LEDGER_SCHEMA_UNKNOWN, LEDGER_UNREADABLE, LEDGER_MISSING = "OK", "SCHEMA_UNKNOWN", "UNREADABLE", "MISSING"

#: `bot_scorecard.BOOKS` klasör anahtarlarının aynısı ("" = ana bot, state kökü). Sıra raporlarda korunur.
KNOWN_BOOK_DIRS: tuple[str, ...] = ("", "strategy_paper", "strategy_paper_m2", "strategy_paper_box", "pattern_trader",
                                    "strategy_paper_trend4h", "strategy_paper_candle4h", "strategy_paper_candle4h_strict")

#: Görünen adlar. D4 etiketi belgeye göre "D4 Donchian 4h" (eski "Trend 4h …" etiketi düzeltildi, §7.7).
BOOK_NAMES: dict[str, str] = {
    BOOK_MAIN: "Ana bot", BOOK_MAIN_FUT: "Ana bot · vadeli", BOOK_MAIN_SPOT: "Ana bot · spot",
    "strategy_paper": "T2", "strategy_paper_m2": "M2 (TSMOM28)", "strategy_paper_box": "Box", "pattern_trader": "Formasyon",
    "strategy_paper_trend4h": "D4 Donchian 4h", "strategy_paper_candle4h": "C4 Mum varyasyonları (PAPER)",
    "strategy_paper_candle4h_strict": "C4S Mum varyasyonları 4h (sıkı, PAPER)",
}


# ============================================================================ zaman / sayı
def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_ts(x: Any) -> datetime | None:
    """ISO-8601 → UTC datetime. Saat dilimi yoksa UTC kabul edilir. Okunamazsa None."""
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(x).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def day_of(dt: datetime) -> str:
    return dt.astimezone(UTC).date().isoformat()


def parse_day(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def day_start(day: str | date) -> datetime:
    d = parse_day(day) if isinstance(day, str) else day
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def add_days(day: str, n: int) -> str:
    return (parse_day(day) + timedelta(days=n)).isoformat()


def dec(x: Any) -> Decimal:
    """Para/miktar alanı → Decimal. None/"" → 0. Okunamayan değer `InvalidOperation` fırlatır (sessiz 0 yok)."""
    if x is None or x == "":
        return Decimal(0)
    if isinstance(x, Decimal):
        return x
    if isinstance(x, float):
        return Decimal(repr(x))
    return Decimal(str(x))


def dec_or_none(x: Any) -> Decimal | None:
    if x is None or x == "":
        return None
    try:
        return dec(x)
    except (InvalidOperation, ValueError, TypeError):
        return None


def dstr(x: Decimal | None) -> str | None:
    """Decimal → kayıpsız string (JSON'a)."""
    return None if x is None else format(x, "f")


def symbol_key(symbol: Any) -> str:
    """Enstrüman anahtarı: 'ETH/USDT', 'ETHUSDT', 'ETH/USDT:USDT' → 'ETHUSDT' (vadeli ve spot aynı coin birleşir)."""
    s = str(symbol or "").upper().strip()
    if ":" in s:
        s = s.split(":", 1)[0]
    return s.replace("/", "").replace("_", "").replace("-", "")


GOLD_BASES = ("XAU", "PAXG")


def is_gold(inst: str) -> bool:
    return any(inst.startswith(b) for b in GOLD_BASES)


# ============================================================================ defter listesi
def book_id_for_dir(sub: str) -> str:
    return BOOK_MAIN_FUT if sub == "" else sub


def book_name(book: str) -> str:
    return BOOK_NAMES.get(book, book)


#: AYNA DEFTERLER (2026-10-06; `scripts/bot_scorecard.py` `MIRROR_BOOKS` ile AYNI anahtarlar — test): başka bir defterin
#: gerçek işlemlerinin kopyası (M2X = M2'nin kopyası, docs/M2_AGGRESSIVE_V1.md §4.2). Arşiv (`find_ledgers`, S1a) onları
#: da KAYDEDER; günlük hedefin toplamına, gruplarına, enstrümanlarına, en iyi enstrümanına ve hükümlerine GİRMEZ (aynı
#: işlemleri iki kez sayardı), ayrı bölümde yalnız bilgi olarak gösterilir. Motor `scripts/`ten bağımsız kalsın diye
#: liste burada tanımlıdır.
MIRROR_BOOKS: dict[str, str] = {"strategy_paper_m2x": "M2X agresif (M2 kopyası, PAPER)"}


def is_mirror(book: str) -> bool:
    return book in MIRROR_BOOKS


def book_group(book: str) -> str:
    """Rapor grubu: ana botun iki alt defteri "main" altında toplanır."""
    return BOOK_MAIN if book in (BOOK_MAIN_FUT, BOOK_MAIN_SPOT) else book


def find_ledgers(state: Path | str) -> dict[str, tuple[str, Path]]:
    """defter kimliği → (tür, yol). Vadeli: `find_books` ile aynı küme (bilinen klasörler + `*/futures_ledger.json`);
    spot: `state/spot_ledger.json` varsa `main_spot`. Yalnız VAR OLAN dosyalar döner."""
    st = Path(state)
    out: dict[str, tuple[str, Path]] = {}
    for sub in KNOWN_BOOK_DIRS:
        p = st / sub / FUTURES_FILE if sub else st / FUTURES_FILE
        if p.exists():
            out[book_id_for_dir(sub)] = (KIND_FUTURES, p)
    for p in sorted(st.glob(f"*/{FUTURES_FILE}")):
        out.setdefault(book_id_for_dir(p.parent.name), (KIND_FUTURES, p))
    sp = st / SPOT_FILE
    if sp.exists():
        out[BOOK_MAIN_SPOT] = (KIND_SPOT, sp)
    return out


# ============================================================================ okuma
@dataclass
class LedgerRead:
    book: str
    kind: str
    path: str
    status: str
    read_at: datetime
    sha256: str | None = None
    size: int | None = None
    schema_version: Any = None
    doc: dict | None = field(default=None, repr=False)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == LEDGER_OK

    def meta(self) -> dict[str, Any]:
        return {"book": self.book, "kind": self.kind, "path": self.path, "status": self.status, "read_at": iso(self.read_at),
                "sha256": self.sha256, "size": self.size, "schema_version": self.schema_version, "error": self.error}


def _validate(kind: str, d: Any) -> str | None:
    """Şema denetimi; sorun varsa açıklama, yoksa None."""
    if not isinstance(d, dict):
        return "kök bir JSON nesnesi değil"
    sv = d.get("schema_version")
    if isinstance(sv, bool) or not isinstance(sv, int) or sv not in KNOWN_SCHEMA_VERSIONS:
        return f"bilinmeyen schema_version={sv!r} (bilinen: {sorted(KNOWN_SCHEMA_VERSIONS)})"
    k = d.get("kind")
    if kind == KIND_FUTURES:
        if k not in (None, KIND_FUTURES):
            return f"kind={k!r} (beklenen futures)"
        need = {"wallet_balance": (str, int, float), "history": list, "entries": list, "positions": dict}
    else:
        if k != KIND_SPOT:
            return f"kind={k!r} (beklenen spot)"
        need = {"cash": (str, int, float), "lots": dict, "history": list, "entries": list}
    for key, typ in need.items():
        if not isinstance(d.get(key), typ):
            return f"zorunlu alan yok/tipi yanlış: {key}"
    return None


def read_ledger(book: str, kind: str, path: Path | str, *, clock: Callable[[], datetime] = utc_now) -> LedgerRead:
    """Tek ledger'ı salt-okunur oku. Dosya `"r"` kipinde açılır; hiçbir koşulda yazılmaz."""
    p = Path(path)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return LedgerRead(book, kind, str(p), LEDGER_MISSING, clock(), error="dosya yok")
    except OSError as exc:
        return LedgerRead(book, kind, str(p), LEDGER_UNREADABLE, clock(), error=f"{type(exc).__name__}: {exc}")
    read_at = clock()
    raw = text.encode("utf-8")
    sha, size = hashlib.sha256(raw).hexdigest(), len(raw)
    del raw                                      # bellek: dosya boyunda geçici kopya hemen bırakılır (gece birimi 512M)
    try:
        d = json.loads(text)
    except ValueError as exc:
        return LedgerRead(book, kind, str(p), LEDGER_UNREADABLE, read_at, sha, size, error=f"JSON çözülemedi: {exc}")
    del text
    why = _validate(kind, d)
    sv = d.get("schema_version") if isinstance(d, dict) else None
    if why:
        return LedgerRead(book, kind, str(p), LEDGER_SCHEMA_UNKNOWN, read_at, sha, size, sv, error=why)
    return LedgerRead(book, kind, str(p), LEDGER_OK, read_at, sha, size, sv, doc=d)


def read_all(state: Path | str, *, clock: Callable[[], datetime] = utc_now) -> dict[str, LedgerRead]:
    return {b: read_ledger(b, kind, p, clock=clock) for b, (kind, p) in find_ledgers(state).items()}


__all__ = ["BOOK_MAIN", "BOOK_MAIN_FUT", "BOOK_MAIN_SPOT", "BOOK_NAMES", "FUTURES_FILE", "GOLD_BASES", "KIND_FUTURES",
           "KIND_SPOT", "KNOWN_BOOK_DIRS", "KNOWN_SCHEMA_VERSIONS", "LEDGER_MISSING", "LEDGER_OK", "LEDGER_SCHEMA_UNKNOWN",
           "LEDGER_UNREADABLE", "LedgerRead", "MIRROR_BOOKS", "SPOT_FILE", "UTC", "add_days", "book_group", "book_id_for_dir", "book_name",
           "day_of", "day_start", "dec", "dec_or_none", "dstr", "find_ledgers", "is_gold", "is_mirror", "iso", "parse_day", "parse_ts",
           "read_all", "read_ledger", "symbol_key", "utc_now"]
