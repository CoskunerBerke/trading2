"""FeatureSnapshotV3 — PAYLAŞILAN, sürümlü, nedensel-zaman güvenli karar anı görüntüsü.

Neden: replay hafızası pratikte yalnız `expected_r`/`p_win` yazıyordu; LearnerV2'nin beklediği MA/
volatilite/hacim/funding/mikroyapı/ajan alanları boş (0) kalıyordu → model "hangi koşulda neden zarar
edildiğini" öğrenemiyordu. Bu modül **tek** builder'dır: hem `historical replay` hem canlı PAPER aynı
fonksiyonu çağırır, aynı girdide **aynı vektörü ve aynı hash'i** üretir.

Sözleşme:
* Yalnız karar anında (`decision_ts`) erişilebilir veri kullanılır. Kullanılan her barın KAPANIŞ zamanı
  `decision_ts`'yi geçemez; geçerse `LeakageError` (fail-closed).
* Gelecek barları değiştirmek geçmiş snapshot'ın hash'ini DEĞİŞTİRMEZ (mutasyon testiyle sabit).
* Eksik alan sessizce 0 sayılmaz: her opsiyonel alan için `miss_<ad>` göstergesi ve `availability` özeti
  üretilir; sayısal vektörde eksikler nötr imputasyonla doldurulur ama gösterge 1 kalır.
* Sonuç (PnL/exit) hiçbir giriş alanına giremez — outcome ayrı `attach_outcome` ile eklenir.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

SNAPSHOT_VERSION = 3
SCHEMA_ID = "feature_snapshot_v3"


class LeakageError(ValueError):
    """Nedensellik ihlali: karar anından sonraki veri kullanılmış (fail-closed)."""


# --------------------------------------------------------------------------- alan sözlüğü
# (ad, zorunlu mu) — zorunlu alanlar coverage gate'inde aranır.
TREND_FIELDS = [("close", True), ("ret_1", True), ("ret_4", True), ("ret_12", False), ("ret_24", False),
                ("ma25", True), ("ma99", True), ("px_vs_ma25_pct", True), ("px_vs_ma99_pct", True),
                ("ma25_slope_pct", True), ("ma99_slope_pct", False), ("ma25_ma99_ratio", True),
                ("ma_cross_dir", True), ("ma_cross_age", False), ("adx", False), ("trend_strength", False)]
MOMENTUM_FIELDS = [("rsi_fast", True), ("rsi_slow", False), ("roc_4", True), ("roc_12", False),
                   ("macd", False), ("macd_signal", False), ("macd_hist", False),
                   ("momentum_dir", True), ("momentum_strength", False)]
VOL_FIELDS = [("atr", True), ("atr_pct", True), ("realized_vol_short", True), ("realized_vol_mid", False),
              ("bb_width", False), ("bb_squeeze", False), ("vol_regime_code", True),
              ("range_pct", True), ("high_low_ratio", False)]
VOLUME_FIELDS = [("volume", True), ("volume_sma_ratio", True), ("volume_z", True),
                 ("obv_slope", False), ("volume_regime_code", False)]
MICRO_FIELDS = [("funding_rate", False), ("funding_z", False), ("oi_change_pct", False), ("basis_pct", False),
                ("spread_pct", False), ("depth_ratio", False), ("est_slippage_pct", False),
                ("data_freshness_s", False), ("liquidity_ok", False)]
MARKET_FIELDS = [("btc_ret_short", False), ("btc_ret_mid", False), ("btc_regime_code", False),
                 ("corr_btc", False), ("beta_btc", False), ("breadth", False), ("risk_on", False),
                 ("cluster_exposure", False), ("portfolio_dir", False), ("portfolio_notional", False),
                 ("portfolio_risk_pct", False)]
# GERCEK kaynak: `tradingbot/coinhead/factors.py::FACTOR_GROUPS`. `CoinHeadDecision.factor_scores`
# tam olarak bu adlarla gelir. Onceden burada legacy ajan adlari (market/candles/levels/analog/edge)
# vardi; hicbiri factor_group olarak uretilmedigi icin ajan alanlari kalici bos kaliyordu.
# `test_agent_names_match_real_factor_groups` bu esitligi kalici olarak korur.
AGENT_NAMES = ("trend", "momentum", "volatility", "volume_flow", "structure_levels", "liquidity",
               "derivatives", "correlation", "historical_edge", "catalyst", "risk")
DECISION_FIELDS = ([(f"agent_bias_{a}", False) for a in AGENT_NAMES] +
                   [(f"agent_conf_{a}", False) for a in AGENT_NAMES] +
                   [("consensus_score", True), ("consensus_conf", True), ("n_dissent", True), ("n_vetoes", True),
                    ("head_confidence", False), ("risk_allowed", True),
                    ("pattern_n", False), ("pattern_p_win", False), ("pattern_expectancy_r", False),
                    ("pattern_pf", False), ("pattern_ci_low", False), ("pattern_distance", False),
                    ("pattern_fallback_level", False)])
PLAN_FIELDS = [("setup_code", True), ("expected_r", True), ("p_win_prior", True), ("expected_cost_pct", False),
               ("entry", True), ("stop_dist_pct", True), ("tp1_dist_pct", False), ("tp2_dist_pct", False),
               ("rr", True), ("leverage", True), ("notional", True), ("margin", False),
               ("open_risk_pct", False), ("drawdown_pct", False), ("pnl_today_r", False), ("pnl_week_r", False),
               ("long_exposure", False), ("short_exposure", False)]
SIDE_FIELDS = [("is_long", True), ("is_short", True), ("is_futures", True)]

ALL_FIELDS: list[tuple[str, bool]] = (TREND_FIELDS + MOMENTUM_FIELDS + VOL_FIELDS + VOLUME_FIELDS +
                                      MICRO_FIELDS + MARKET_FIELDS + DECISION_FIELDS + PLAN_FIELDS + SIDE_FIELDS)
FIELD_NAMES: list[str] = [n for n, _ in ALL_FIELDS]
REQUIRED_FIELDS: list[str] = [n for n, req in ALL_FIELDS if req]
MISS_PREFIX = "miss_"

# --------------------------------------------------------------------------- prediction / audit ayrimi
# `prediction_features_v3`: p_win modelinin HEM egitim HEM cikarim yolunda kullandigi alanlar.
# Kabul kosullari (ucu birden):
#   1) `LearnerV2.predict` cagrisindan ONCE mevcut (nedensel),
#   2) replay ve canli PAPER yollarinda AYNI anlamda uretilebilir,
#   3) prediction ciktisina bagli DEGIL (dairesel sizinti yok).
# Asagidaki alanlar bu kosullari saglamadigi icin modele GIRMEZ; yalniz attribution/policy/rapor icin
# saklanir. Gerekceler tek tek yazilidir - bu liste sessizce buyutulmemelidir.
AUDIT_ONLY_FIELDS: dict[str, str] = {
    # (1) olcek bagimli ham seviyeler: sembol/donem arasi karsilastirilamaz, normalize turevleri modelde
    "close": "ham fiyat seviyesi - px_vs_ma*/ret_* normalize turevleri kullanilir",
    "ma25": "ham seviye - px_vs_ma25_pct/ma25_slope_pct kullanilir",
    "ma99": "ham seviye - px_vs_ma99_pct/ma99_slope_pct kullanilir",
    "atr": "ham seviye - atr_pct kullanilir",
    "volume": "ham hacim - volume_z/volume_sma_ratio kullanilir",
    "macd": "ham fiyat olceginde - momentum_dir/roc_* kullanilir",
    "macd_signal": "builder tarafindan uretilmiyor (sema-only)",
    "macd_hist": "builder tarafindan uretilmiyor (sema-only)",
    "entry": "ham fiyat seviyesi - stop_dist_pct/tp*_dist_pct kullanilir",
    "notional": "hesap buyuklugune bagli - replay/canli arasinda kiyaslanamaz",
    "margin": "hesap buyuklugune bagli - replay/canli arasinda kiyaslanamaz",
    # (2) DAIRESEL: prediction ciktisinin kendisi
    "p_win_prior": "DAIRESEL - canli yolda `d.p_win` model ciktisiyla ezilir; girdi olamaz",
    # (3) prediction anindan SONRA olusur
    "risk_allowed": "risk degerlendirmesi p_win'den SONRA calisir",
    # (4) hicbir yol tarafindan doldurulmuyor: sabit-eksik gurultu olarak modele sokulmaz
    "btc_regime_code": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "breadth": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "risk_on": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "cluster_exposure": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "portfolio_dir": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "portfolio_notional": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "portfolio_risk_pct": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "open_risk_pct": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "drawdown_pct": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "pnl_today_r": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "pnl_week_r": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "long_exposure": "hicbir cagri yeri doldurmuyor - sabit eksik",
    "short_exposure": "hicbir cagri yeri doldurmuyor - sabit eksik",
}
PREDICTION_FIELDS: list[tuple[str, bool]] = [(n, r) for n, r in ALL_FIELDS if n not in AUDIT_ONLY_FIELDS]
PREDICTION_FIELD_NAMES: list[str] = [n for n, _ in PREDICTION_FIELDS]

PREDICTION_SCHEMA_ID = "prediction_features_v3"
# Eksik alan sozlesmesi: sayisal vektorde notr 0.0 ile doldurulur ve `miss_<ad>` gostergesi 1.0 olur.
# Model bu gostergeyi GORUR; "eksik" ile "gercek 0" ayrimi boylece egitime tasinir.
IMPUTATION_CONTRACT = "missing -> 0.0 (neutral) + miss_<field> = 1.0"


def prediction_feature_names() -> list[str]:
    """p_win modelinin girdi sirasi (KESIN, deterministik): alanlar + opsiyonel alanlarin miss_ gostergeleri."""
    return PREDICTION_FIELD_NAMES + [MISS_PREFIX + n for n, req in PREDICTION_FIELDS if not req]


def prediction_schema_hash() -> str:
    """Sema parmak izi: ad sirasi + surum + imputasyon sozlesmesi. Serve tarafinda model artifact'iyle
    karsilastirilir; uyusmazsa model KULLANILMAZ (fail-closed, baseline/prior'a donulur)."""
    payload = {"schema_id": PREDICTION_SCHEMA_ID, "feature_version": SNAPSHOT_VERSION,
               "names": prediction_feature_names(), "imputation": IMPUTATION_CONTRACT}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

VOL_REGIMES = ("LOW_VOL", "NORMAL", "HIGH_VOL", "EXTREME")
BTC_REGIMES = ("RISK_OFF", "NEUTRAL", "RISK_ON")
SETUP_CODES = ("-", "pullback", "kırılım", "breakout", "mean_revert", "trend_follow", "range")


def snapshot_feature_names(*, include_missing: bool = True) -> list[str]:
    """Model girdisinin KESİN sırası (deterministik)."""
    names = list(FIELD_NAMES)
    if include_missing:
        names += [MISS_PREFIX + n for n, req in ALL_FIELDS if not req]
    return names


# --------------------------------------------------------------------------- yardımcılar
def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != math.inf else None


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return 100.0 * (a - b) / abs(b)


def _code(value: Any, table: tuple[str, ...]) -> float | None:
    if value is None:
        return None
    s = str(value).upper() if table is not VOL_REGIMES else str(value).upper()
    for i, t in enumerate(table):
        if s == t.upper():
            return float(i)
    return None


@dataclass
class FeatureSnapshotV3:
    """Değişmez karar anı görüntüsü. `values` sayısal alanlar, `missing` eksik alan adları."""
    feature_version: int
    schema_id: str
    source: str
    symbol: str
    market_type: str
    timeframe: str
    side: str
    decision_ts: str
    last_bar_ts: str
    run_id: str = ""
    seed: int | None = None
    config_hash: str = ""
    strategy_version: str = ""
    model_version: str = ""
    pattern_version: str = ""
    values: dict[str, float] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def availability(self) -> dict[str, float]:
        total = len(FIELD_NAMES)
        return {"present": total - len(self.missing), "total": total,
                "ratio": round((total - len(self.missing)) / total, 4) if total else 0.0}

    def vector(self) -> dict[str, float]:
        """TAM vektor (prediction + audit): alanlar + eksiklik gostergeleri. Rapor/attribution icindir;
        p_win modeli bunu DEGIL `prediction_vector()`'u kullanir."""
        out = {n: float(self.values.get(n, 0.0)) for n in FIELD_NAMES}
        for n, req in ALL_FIELDS:
            if not req:
                out[MISS_PREFIX + n] = 1.0 if n in self.missing else 0.0
        return out

    def prediction_vector(self) -> dict[str, float]:
        """p_win modelinin girdisi - egitim ve cikarim yollari BU fonksiyonu cagirir (train/serve paritesi).
        Audit-only ve dairesel alanlar (bkz. `AUDIT_ONLY_FIELDS`) disarida kalir."""
        out = {n: float(self.values.get(n, 0.0)) for n in PREDICTION_FIELD_NAMES}
        for n, req in PREDICTION_FIELDS:
            if not req:
                out[MISS_PREFIX + n] = 1.0 if n in self.missing else 0.0
        return out

    @property
    def prediction_availability(self) -> dict[str, float]:
        total = len(PREDICTION_FIELD_NAMES)
        miss = sum(1 for n in PREDICTION_FIELD_NAMES if n in self.missing)
        return {"present": total - miss, "total": total,
                "ratio": round((total - miss) / total, 4) if total else 0.0}

    def payload(self) -> dict:
        """Hash'e giren kanonik gövde (telemetri/zaman damgası dışı alanlar hariç tutulmaz: karar anı sabittir)."""
        return {"schema_id": self.schema_id, "feature_version": self.feature_version, "symbol": self.symbol,
                "market_type": self.market_type, "timeframe": self.timeframe, "side": self.side,
                "decision_ts": self.decision_ts, "last_bar_ts": self.last_bar_ts,
                "values": {k: round(v, 10) for k, v in sorted(self.values.items())},
                "missing": sorted(self.missing)}

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(self.payload(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def to_dict(self) -> dict:
        return {"feature_version": self.feature_version, "schema_id": self.schema_id, "source": self.source,
                "symbol": self.symbol, "market_type": self.market_type, "timeframe": self.timeframe,
                "side": self.side, "decision_ts": self.decision_ts, "last_bar_ts": self.last_bar_ts,
                "run_id": self.run_id, "seed": self.seed, "config_hash": self.config_hash,
                "strategy_version": self.strategy_version, "model_version": self.model_version,
                "pattern_version": self.pattern_version, "values": self.values, "missing": self.missing,
                "availability": self.availability, "snapshot_hash": self.hash()}

    @classmethod
    def from_dict(cls, d: dict) -> "FeatureSnapshotV3":
        return cls(feature_version=int(d.get("feature_version", SNAPSHOT_VERSION)),
                   schema_id=str(d.get("schema_id", SCHEMA_ID)), source=str(d.get("source", "")),
                   symbol=str(d.get("symbol", "")), market_type=str(d.get("market_type", "")),
                   timeframe=str(d.get("timeframe", "")), side=str(d.get("side", "")),
                   decision_ts=str(d.get("decision_ts", "")), last_bar_ts=str(d.get("last_bar_ts", "")),
                   run_id=str(d.get("run_id", "")), seed=d.get("seed"), config_hash=str(d.get("config_hash", "")),
                   strategy_version=str(d.get("strategy_version", "")), model_version=str(d.get("model_version", "")),
                   pattern_version=str(d.get("pattern_version", "")),
                   values={k: float(v) for k, v in (d.get("values") or {}).items()},
                   missing=list(d.get("missing") or []))


# --------------------------------------------------------------------------- builder
def _series(frame: Any, col: str) -> list[float]:
    try:
        return [float(x) for x in frame[col].tolist()]
    except Exception:  # noqa: BLE001 — kolon yoksa/çevrilemezse alan "eksik" sayılır
        return []


def build_snapshot(*, symbol: str, market_type: str, timeframe: str, side: str, decision_ts_ms: int,
                   bars: Any, source: str = "LIVE_PAPER", btc_bars: Any = None, funding: dict | None = None,
                   micro: dict | None = None, decision: dict | None = None, plan: dict | None = None,
                   portfolio: dict | None = None, pattern: dict | None = None, agents: dict | None = None,
                   run_id: str = "", seed: int | None = None, config_hash: str = "",
                   strategy_version: str = "", model_version: str = "", pattern_version: str = "",
                   strict: bool = True) -> FeatureSnapshotV3:
    """`bars`: karar anına kadar KAPANMIŞ barlar (DataFrame; timestamp/open/high/low/close/volume).
    Bütün türetimler yalnız bu barlardan yapılır → gelecek barlar sonucu etkilemez."""
    if bars is None or len(bars) == 0:
        raise LeakageError("snapshot için bar yok")
    ts = [int(x) for x in bars["timestamp"].tolist()]
    close = _series(bars, "close")
    high, low, vol = _series(bars, "high"), _series(bars, "low"), _series(bars, "volume")
    last_ts = ts[-1]
    if strict:
        # Nedensellik: kullanılan hiçbir barın AÇILIŞ zamanı karar anını geçemez.
        bad = [t for t in ts if t > decision_ts_ms]
        if bad:
            raise LeakageError(f"karar anından sonraki {len(bad)} bar kullanıldı (decision_ts={decision_ts_ms}, "
                               f"ilk ihlal={min(bad)})")
    v: dict[str, float] = {}
    miss: list[str] = []

    def put(name: str, value: float | None) -> None:
        if value is None:
            miss.append(name)
        else:
            v[name] = float(value)

    def ret(n: int) -> float | None:
        return _pct(close[-1], close[-1 - n]) if len(close) > n else None

    def sma(vals: list[float], n: int) -> float | None:
        return sum(vals[-n:]) / n if len(vals) >= n else None

    # --- trend / fiyat
    put("close", close[-1] if close else None)
    for n, key in ((1, "ret_1"), (4, "ret_4"), (12, "ret_12"), (24, "ret_24")):
        put(key, ret(n))
    ma25, ma99 = sma(close, 25), sma(close, 99)
    put("ma25", ma25)
    put("ma99", ma99)
    put("px_vs_ma25_pct", _pct(close[-1], ma25) if ma25 is not None else None)
    put("px_vs_ma99_pct", _pct(close[-1], ma99) if ma99 is not None else None)
    prev25 = sum(close[-26:-1]) / 25 if len(close) >= 26 else None
    prev99 = sum(close[-100:-1]) / 99 if len(close) >= 100 else None
    put("ma25_slope_pct", _pct(ma25, prev25) if (ma25 is not None and prev25 is not None) else None)
    put("ma99_slope_pct", _pct(ma99, prev99) if (ma99 is not None and prev99 is not None) else None)
    put("ma25_ma99_ratio", (ma25 / ma99) if (ma25 is not None and ma99 not in (None, 0)) else None)
    cross_dir = None
    cross_age = None
    if ma25 is not None and ma99 is not None:
        cross_dir = 1.0 if ma25 >= ma99 else -1.0
        age = 0
        for i in range(len(close) - 1, 98, -1):
            a = sum(close[i - 24:i + 1]) / 25
            b = sum(close[i - 98:i + 1]) / 99
            if (1.0 if a >= b else -1.0) != cross_dir:
                break
            age += 1
            if age >= 200:
                break
        cross_age = float(age)
    put("ma_cross_dir", cross_dir)
    put("ma_cross_age", cross_age)
    put("adx", _f((decision or {}).get("adx")))
    put("trend_strength", _f((decision or {}).get("trend_strength")))

    # --- momentum
    def rsi(vals: list[float], n: int) -> float | None:
        if len(vals) <= n:
            return None
        gains = losses = 0.0
        for i in range(len(vals) - n, len(vals)):
            ch = vals[i] - vals[i - 1]
            gains += max(0.0, ch)
            losses += max(0.0, -ch)
        if losses == 0:
            return 100.0
        rs = (gains / n) / (losses / n)
        return 100.0 - 100.0 / (1.0 + rs)

    put("rsi_fast", rsi(close, 7))
    put("rsi_slow", rsi(close, 14))
    put("roc_4", ret(4))
    put("roc_12", ret(12))
    ema_f = ema_s = None
    if len(close) >= 26:
        def ema(vals: list[float], n: int) -> float:
            k = 2.0 / (n + 1)
            e = vals[0]
            for x in vals[1:]:
                e = x * k + e * (1 - k)
            return e
        ema_f, ema_s = ema(close, 12), ema(close, 26)
    macd = (ema_f - ema_s) if (ema_f is not None and ema_s is not None) else None
    put("macd", macd)
    put("macd_signal", None)
    put("macd_hist", None)
    r1 = ret(1)
    put("momentum_dir", (1.0 if r1 > 0 else (-1.0 if r1 < 0 else 0.0)) if r1 is not None else None)
    put("momentum_strength", abs(r1) if r1 is not None else None)

    # --- volatilite
    trs = [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
           for i in range(1, len(close))] if len(close) > 1 and high and low else []
    atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else None
    put("atr", atr)
    atr_pct = (100.0 * atr / close[-1]) if (atr is not None and close[-1]) else None
    put("atr_pct", atr_pct)

    def realized(n: int) -> float | None:
        if len(close) <= n:
            return None
        rets = [(close[i] / close[i - 1] - 1.0) for i in range(len(close) - n, len(close)) if close[i - 1]]
        if len(rets) < 2:
            return None
        m = sum(rets) / len(rets)
        return 100.0 * math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1))

    put("realized_vol_short", realized(12))
    put("realized_vol_mid", realized(48))
    sd = None
    if len(close) >= 20:
        m20 = sum(close[-20:]) / 20
        sd = math.sqrt(sum((x - m20) ** 2 for x in close[-20:]) / 20)
        put("bb_width", (400.0 * sd / m20) if m20 else None)
        put("bb_squeeze", 1.0 if (sd and m20 and (400.0 * sd / m20) < 4.0) else 0.0)
    else:
        put("bb_width", None)
        put("bb_squeeze", None)
    reg = None
    if atr_pct is not None:
        reg = 0.0 if atr_pct < 1.0 else (1.0 if atr_pct < 3.0 else (2.0 if atr_pct < 6.0 else 3.0))
    put("vol_regime_code", reg)
    put("range_pct", (100.0 * (high[-1] - low[-1]) / close[-1]) if (high and low and close[-1]) else None)
    put("high_low_ratio", (high[-1] / low[-1]) if (high and low and low[-1]) else None)

    # --- hacim
    put("volume", vol[-1] if vol else None)
    vsma = sma(vol, 20) if vol else None
    put("volume_sma_ratio", (vol[-1] / vsma) if (vol and vsma) else None)
    if vol and len(vol) >= 20:
        m = sum(vol[-20:]) / 20
        s = math.sqrt(sum((x - m) ** 2 for x in vol[-20:]) / 20)
        put("volume_z", ((vol[-1] - m) / s) if s else 0.0)
    else:
        put("volume_z", None)
    obv = None
    if vol and len(close) >= 11:
        acc = 0.0
        for i in range(len(close) - 10, len(close)):
            acc += vol[i] if close[i] > close[i - 1] else (-vol[i] if close[i] < close[i - 1] else 0.0)
        obv = acc / (sum(vol[-10:]) or 1.0)
    put("obv_slope", obv)
    vr = v.get("volume_sma_ratio")
    put("volume_regime_code", (0.0 if vr < 0.7 else (1.0 if vr < 1.5 else 2.0)) if vr is not None else None)

    # --- futures / mikroyapı (opsiyonel; yoksa açıkça eksik)
    fu = funding or {}
    mi = micro or {}
    put("funding_rate", _f(fu.get("rate")))
    put("funding_z", _f(fu.get("z")))
    put("oi_change_pct", _f(mi.get("oi_change_pct")))
    put("basis_pct", _f(mi.get("basis_pct")))
    put("spread_pct", _f(mi.get("spread_pct")))
    put("depth_ratio", _f(mi.get("depth_ratio")))
    put("est_slippage_pct", _f(mi.get("est_slippage_pct")))
    put("data_freshness_s", _f(mi.get("data_freshness_s")))
    liq = mi.get("liquidity_ok")
    put("liquidity_ok", None if liq is None else (1.0 if liq else 0.0))

    # --- piyasa bağlamı
    btc_close = _series(btc_bars, "close") if btc_bars is not None and len(btc_bars) else []
    if btc_bars is not None and len(btc_bars) and strict:
        bts = [int(x) for x in btc_bars["timestamp"].tolist()]
        if any(t > decision_ts_ms for t in bts):
            raise LeakageError("BTC bağlam barları karar anını aşıyor")
    put("btc_ret_short", _pct(btc_close[-1], btc_close[-5]) if len(btc_close) > 5 else None)
    put("btc_ret_mid", _pct(btc_close[-1], btc_close[-25]) if len(btc_close) > 25 else None)
    pf = portfolio or {}
    put("btc_regime_code", _code(pf.get("btc_regime"), BTC_REGIMES))
    corr = beta = None
    if len(btc_close) > 30 and len(close) > 30:
        a = [(close[i] / close[i - 1] - 1.0) for i in range(len(close) - 30, len(close)) if close[i - 1]]
        b = [(btc_close[i] / btc_close[i - 1] - 1.0) for i in range(len(btc_close) - 30, len(btc_close)) if btc_close[i - 1]]
        n = min(len(a), len(b))
        if n > 5:
            a, b = a[-n:], b[-n:]
            ma_, mb = sum(a) / n, sum(b) / n
            cov = sum((x - ma_) * (y - mb) for x, y in zip(a, b)) / n
            va = sum((x - ma_) ** 2 for x in a) / n
            vb = sum((y - mb) ** 2 for y in b) / n
            corr = cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None
            beta = cov / vb if vb > 0 else None
    put("corr_btc", corr)
    put("beta_btc", beta)
    put("breadth", _f(pf.get("breadth")))
    ro = pf.get("risk_on")
    put("risk_on", None if ro is None else (1.0 if ro else 0.0))
    put("cluster_exposure", _f(pf.get("cluster_exposure")))
    put("portfolio_dir", _f(pf.get("direction")))
    put("portfolio_notional", _f(pf.get("notional")))
    put("portfolio_risk_pct", _f(pf.get("open_risk_pct")))

    # --- ajan / karar bağlamı
    ag = agents or {}
    for a in AGENT_NAMES:
        rec = ag.get(a) or {}
        put(f"agent_bias_{a}", _f(rec.get("bias")))
        put(f"agent_conf_{a}", _f(rec.get("confidence")))
    d = decision or {}
    put("consensus_score", _f(d.get("consensus_score")))
    put("consensus_conf", _f(d.get("consensus_conf")))
    put("n_dissent", _f(d.get("n_dissent")))
    put("n_vetoes", _f(d.get("n_vetoes")))
    put("head_confidence", _f(d.get("head_confidence")))
    ra = d.get("risk_allowed")
    put("risk_allowed", None if ra is None else (1.0 if ra else 0.0))
    pt = pattern or {}
    put("pattern_n", _f(pt.get("n")))
    put("pattern_p_win", _f(pt.get("p_win")))
    put("pattern_expectancy_r", _f(pt.get("expectancy_r")))
    put("pattern_pf", _f(pt.get("profit_factor")))
    put("pattern_ci_low", _f(pt.get("ci_low")))
    put("pattern_distance", _f(pt.get("distance")))
    put("pattern_fallback_level", _f(pt.get("fallback_level")))

    # --- plan / risk
    pl = plan or {}
    put("setup_code", _code(pl.get("setup_type"), SETUP_CODES))
    put("expected_r", _f(pl.get("expected_r")))
    put("p_win_prior", _f(pl.get("p_win")))
    put("expected_cost_pct", _f(pl.get("expected_cost_pct")))
    entry = _f(pl.get("entry"))
    put("entry", entry)
    stop = _f(pl.get("stop"))
    put("stop_dist_pct", abs(_pct(stop, entry)) if (stop is not None and entry) else None)
    tps = pl.get("targets") or []
    put("tp1_dist_pct", abs(_pct(_f(tps[0]), entry)) if (len(tps) > 0 and entry) else None)
    put("tp2_dist_pct", abs(_pct(_f(tps[1]), entry)) if (len(tps) > 1 and entry) else None)
    put("rr", _f(pl.get("rr")))
    put("leverage", _f(pl.get("leverage")))
    put("notional", _f(pl.get("notional")))
    put("margin", _f(pl.get("margin")))
    put("open_risk_pct", _f(pf.get("open_risk_pct")))
    put("drawdown_pct", _f(pf.get("drawdown_pct")))
    put("pnl_today_r", _f(pf.get("pnl_today_r")))
    put("pnl_week_r", _f(pf.get("pnl_week_r")))
    put("long_exposure", _f(pf.get("long_exposure")))
    put("short_exposure", _f(pf.get("short_exposure")))

    # --- taraf
    s = str(side or "").upper()
    put("is_long", 1.0 if s == "LONG" else 0.0)
    put("is_short", 1.0 if s == "SHORT" else 0.0)
    put("is_futures", 1.0 if "PERP" in str(market_type).upper() or "FUT" in str(market_type).upper() else 0.0)

    from ..core import iso
    import datetime as _dt
    return FeatureSnapshotV3(
        feature_version=SNAPSHOT_VERSION, schema_id=SCHEMA_ID, source=source, symbol=symbol,
        market_type=market_type, timeframe=timeframe, side=s,
        decision_ts=iso(_dt.datetime.fromtimestamp(decision_ts_ms / 1000, tz=_dt.timezone.utc)),
        last_bar_ts=iso(_dt.datetime.fromtimestamp(last_ts / 1000, tz=_dt.timezone.utc)),
        run_id=run_id, seed=seed, config_hash=config_hash, strategy_version=strategy_version,
        model_version=model_version, pattern_version=pattern_version, values=v, missing=miss)


def prediction_vector_from_row(row: dict) -> dict[str, float] | None:
    """Kayitli hafiza satirindan p_win egitim vektoru. v3 snapshot yoksa None (satir egitime GIRMEZ).

    Egitim yolu bunu, canli cikarim yolu `FeatureSnapshotV3.prediction_vector()`'u cagirir; ikisi de
    ayni alan listesini ve ayni imputasyon sozlesmesini kullanir (train/serve paritesi).
    """
    snap = (row or {}).get("snapshot")
    if not isinstance(snap, dict) or not isinstance(snap.get("values"), dict):
        return None
    if int(snap.get("feature_version") or 0) != SNAPSHOT_VERSION:
        return None
    if str(snap.get("schema_id") or "") != SCHEMA_ID:
        return None
    return FeatureSnapshotV3.from_dict(snap).prediction_vector()


# --------------------------------------------------------------------------- PAYLASILAN esleme yardimcilari
# Replay ve canli PAPER bu iki fonksiyonu AYNEN cagirir. Esleme mantigi tek yerde durur ki iki yol
# birbirinden sessizce ayrilmasin (eski hata: iki cagri yeri de var olmayan `agent_reports` okuyordu).
_LEVEL_CODE = {"same_coin": 0.0, "cluster": 1.0, "universe": 2.0}


def agents_from_factor_scores(factor_scores: Any) -> dict[str, dict]:
    """`CoinHeadDecision.factor_scores` -> {grup: {bias, confidence}}. Nesne ya da dict kabul eder.

    Kaynak sozlesmesi (tradingbot/coinhead/schema.py::FactorGroupScore): `group`, `score` (-1..1),
    `confidence` (0..1). Grup adlari `FACTOR_GROUPS` ile birebir; bkz. `AGENT_NAMES`.
    """
    out: dict[str, dict] = {}
    for fs in (factor_scores or []):
        rec = fs if isinstance(fs, dict) else getattr(fs, "__dict__", {})
        name = str(rec.get("group") or "")
        if not name:
            continue
        out[name] = {"bias": rec.get("score"), "confidence": rec.get("confidence")}
    return out


def pattern_fields_from_evidence(evidence: Any, side: str) -> dict:
    """Karar akisinda ZATEN hesaplanmis pattern kanitindan snapshot alanlari (ikinci sorgu YOK).

    `evidence` sekli: {"LONG": query_result, "SHORT": query_result} (bkz. patterns/engine.py::query).
    Anahtarlar kaynaktan dogrulanmistir: n, stats.p_win_posterior, stats.mean_net_r,
    stats.profit_factor, stats.expectancy_ci[0], neighbors[0].distance, levels.
    """
    if not isinstance(evidence, dict):
        return {}
    res = evidence.get(str(side or "").upper())
    if not isinstance(res, dict):
        return {}
    st = res.get("stats") or {}
    ci = st.get("expectancy_ci") or []
    neigh = res.get("neighbors") or []
    levels = res.get("levels") or {}
    used = [_LEVEL_CODE[k] for k in levels if k in _LEVEL_CODE]
    return {"n": res.get("n"),
            "p_win": st.get("p_win_posterior"),
            "expectancy_r": st.get("mean_net_r"),
            "profit_factor": st.get("profit_factor"),
            "ci_low": (ci[0] if len(ci) > 0 else None),
            "distance": (neigh[0].get("distance") if neigh and isinstance(neigh[0], dict) else None),
            "fallback_level": (max(used) if used else None)}


def attach_outcome(snapshot_row: dict, outcome: dict) -> dict:
    """Kapanış sonucunu snapshot'a EKLER (giriş alanlarına asla karıştırmaz)."""
    return {**snapshot_row, "outcome": dict(outcome or {})}


__all__ = ["ALL_FIELDS", "AGENT_NAMES", "AUDIT_ONLY_FIELDS", "FIELD_NAMES", "FeatureSnapshotV3",
           "IMPUTATION_CONTRACT", "LeakageError", "MISS_PREFIX", "PREDICTION_FIELDS",
           "PREDICTION_FIELD_NAMES", "PREDICTION_SCHEMA_ID", "REQUIRED_FIELDS", "SCHEMA_ID",
           "SNAPSHOT_VERSION", "agents_from_factor_scores", "attach_outcome", "build_snapshot",
           "pattern_fields_from_evidence", "prediction_feature_names", "prediction_schema_hash",
           "prediction_vector_from_row",
           "snapshot_feature_names"]
