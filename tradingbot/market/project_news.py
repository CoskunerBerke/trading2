"""PROJE DUYURULARI — her coinin KENDI deposundan yayimlanan surumler.

Neden bu kaynak: "proje duyurusu / planli ag olayi" icin dogrulanabilir ve anahtarsiz tek
kaynak, projenin kendi yayim akisidir. GitHub Releases API'si her surum icin GERCEK bir
`published_at`, kalici bir `html_url` ve etiket verir; ikincil bir aktarima ya da haber
sitesine guvenmek gerekmez. Bu yuzden uretilen kayitlar `CONFIRMED`tir.

On coinin onu da dogrulandi (2026-09-11): hepsinde gercek surum ve gercek yayim zamani var.

SINIRLAR — acikca:

* Bu kaynak **surum/yazilim duyurularini** kapsar. Borsa listeleme, yonetisim oylamasi,
  token ekonomisi degisikligi ya da basin bulteni KAPSAMAZ. "Haberler takip ediliyor"
  cumlesi bu kaynak icin kurulamaz; kurulabilecek cumle "proje surum duyurulari takip
  ediliyor"dur.
* Kimliksiz GitHub sinirlari saatte 60 istektir. On depo bir turda 10 istek eder; bu
  yuzden varsayilan periyot saatlerdir. `GITHUB_TOKEN` ortam degiskeni VARSA kullanilir
  (sinir 5000/saat), YOKSA kimliksiz devam edilir — zorunlu DEGILDIR.
* Yayim zamani API'den gelir ve ASLA alinma zamaniyla doldurulmaz; ikisi ayri alandir.
* Tarihsel arsiv yoktur: `market.news.for_backtest` bu kayitlari da gecmise TASIYAMAZ.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable

from .news import CONFIRMED, PROJECT, NewsItem

log = logging.getLogger(__name__)

API = "https://api.github.com/repos/{repo}/releases?per_page={n}"

#: Coin → projenin YAYIM yaptigi depo. Her biri 2026-09-11'de tek tek dogrulandi.
#: Bir coinin deposu yoksa buraya UYDURMA bir giris konmaz; coin kapsam disinda kalir.
PROJECT_REPOS: dict[str, str] = {
    "BTC/USDT": "bitcoin/bitcoin",
    "ETH/USDT": "ethereum/go-ethereum",
    "SOL/USDT": "anza-xyz/agave",
    "BNB/USDT": "bnb-chain/bsc",
    "XRP/USDT": "XRPLF/rippled",
    "LINK/USDT": "smartcontractkit/chainlink",
    "DOGE/USDT": "dogecoin/dogecoin",
    "AVAX/USDT": "ava-labs/avalanchego",
    "LTC/USDT": "litecoin-project/litecoin",
    "AAVE/USDT": "aave-dao/aave-v3-origin",
}


def _default_get(url: str, timeout: float = 20.0) -> Any:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "tradingbot"}
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:                                   # varsa kullanilir, YOKSA zorunlu degil
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def release_to_item(symbol: str, repo: str, rel: dict, *, ingested_at: str | None = None) -> NewsItem | None:
    """Bir surum satirini `NewsItem`e cevir. Yayim zamani yoksa kayit URETILMEZ.

    Taslak (`draft`) surumler atlanir: yayimlanmamis bir seye "duyuru" denemez.
    `ingested_at` KURULUM ANINDA verilir; nesne sonradan degistirilmez (aksi hâlde
    `event_id` alanlarla tutarsiz kalabilirdi).
    """
    if not isinstance(rel, dict) or rel.get("draft"):
        return None
    published = rel.get("published_at")
    if not published:
        return None                            # zamani bilinmeyen duyuru kaydedilmez
    tag = str(rel.get("tag_name") or "")
    name = str(rel.get("name") or "").strip()
    title = f"{symbol.split('/')[0]} {tag}" + (f" — {name}" if name and name != tag else "")
    return NewsItem(
        source=f"github:{repo}", title=title, category=PROJECT, status=CONFIRMED,
        url=str(rel.get("html_url") or ""), symbols=[symbol],
        published_at=published, ingested_at=ingested_at,
        body=str(rel.get("body") or "")[:2000],
        detail={"repo": repo, "tag": tag, "prerelease": bool(rel.get("prerelease")),
                "kind": "release"})


class ProjectReleases:
    """Coin basina surum duyurusu cekici. Hatalar SAYILIR, sessizce yutulmaz."""

    def __init__(self, *, get: Callable[[str], Any] | None = None, per_repo: int = 5,
                 repos: dict[str, str] | None = None) -> None:
        self.get = get or _default_get
        self.per_repo = int(per_repo)
        self.repos = dict(repos if repos is not None else PROJECT_REPOS)
        self.errors: dict[str, str] = {}
        self.requests = 0
        self.covered: list[str] = []
        self.uncovered: list[str] = []

    def fetch(self, symbols: Iterable[str], *, now_iso: str) -> list[NewsItem]:
        """Verilen semboller icin surum duyurulari. Deposu olmayan sembol `uncovered`dir."""
        out: list[NewsItem] = []
        self.errors, self.requests = {}, 0
        self.covered, self.uncovered = [], []
        for sym in symbols:
            repo = self.repos.get(str(sym))
            if not repo:
                self.uncovered.append(str(sym))
                continue
            self.requests += 1
            try:
                rows = self.get(API.format(repo=repo, n=self.per_repo)) or []
            except urllib.error.HTTPError as exc:
                self.errors[str(sym)] = f"HTTP {exc.code}"
                continue
            except Exception as exc:  # noqa: BLE001 — ag hatasi digerlerini durdurmaz
                self.errors[str(sym)] = f"{type(exc).__name__}: {exc}"[:160]
                continue
            self.covered.append(str(sym))
            for rel in rows:
                it = release_to_item(str(sym), repo, rel, ingested_at=now_iso)
                if it is not None:
                    out.append(it)
        return out

    def status(self) -> dict:
        """Kaynagin GERCEK kapsami — 'haberler takip ediliyor' demenin sinirlari."""
        return {"source": "github_releases", "kind": "project_release_announcements",
                "requires_api_key": False, "requests": self.requests,
                "covered_symbols": sorted(self.covered),
                "uncovered_symbols": sorted(self.uncovered),
                "errors": dict(self.errors),
                "ok": not self.errors,
                "not_covered_by_this_source": [
                    "borsa listeleme duyuruları", "yönetişim oylamaları",
                    "token ekonomisi değişiklikleri", "genel basın bültenleri"]}


#: MAKRO TAKVIM — UYGULANMADI ve burada bos bir entegrasyon SUNULMAZ.
#: Anahtarsiz ve guvenilir bir makro takvim ucu bulamadim: FRED ve ticari takvim
#: saglayicilari API anahtari ister, federalreserve.gov ise yalniz HTML yayimlar ve
#: kazima kirilgandir. Kalan bagimlilik budur: bir makro takvim API anahtari.
#: Anahtar saglandiginda `market.news` kayit sozlesmesi (provenans, uc durum, gecmise
#: sizinti yasagi) hazirdir; eksik olan yalnizca cekicidir.
MACRO_CALENDAR_STATUS = {
    "implemented": False,
    "reason": "anahtarsız ve güvenilir bir makro takvim ucu yok",
    "blocked_on": "makro takvim API anahtarı (ör. ücretli takvim sağlayıcısı ya da FRED)",
    "note": "sahte kaynak ya da boş çalışan entegrasyon eklenmedi",
}


__all__ = ["API", "MACRO_CALENDAR_STATUS", "PROJECT_REPOS", "ProjectReleases", "release_to_item"]
