#!/usr/bin/env python3
"""`library/config_epochs.json` üreticisi (P3a; mühür CONFIG_EPOCHS_V1; docs/ENGINE_BRAIN_V1.md §8.1, ana belge §6.2).

Git geçmişinden her belgelenmiş worker sürümünün (`tradingbot/research_engine/epochs.py` `DEPLOYS`) `config.yaml`'ını okur,
belgelenmiş kayıtlarla `epochs.build`'e verir ve sonucu yazar. Belirlenimcidir: aynı depo → aynı baytlar. Salt-okunur git
komutları kullanır (`rev-parse`, `show`, `log`, `merge-base --is-ancestor`); ağ yok.

Denetimler (biri tutmazsa çıkış 2, hiçbir şey yazılmaz):
* mühürden (`registry.T_SEAL_MS`) önce depoya eklenmiş HER worker sürüm betiği (`deploy/releases/tb-deploy-*.sh`)
  `DEPLOYS`'ta belgelenmiş olmalı (belgelenmemiş sürüm = hata). Mühürden sonra eklenen betikler V1'in kapsamı DIŞINDADIR:
  sonraki config dönemlerini gece birimi açar (ana belge §6.2 "yeni sha = yeni dönem", P3g); V1 dosyası değişmez;
* dağıtılmış sürümler zaman sırasıyla birbirinin atası olmalı;
* pencere sınırının dayandığı commit'in (`lo_commit`/`hi_commit`) işleyen zamanı sınıra eşit olmalı.

    python scripts/gen_config_epochs.py            # dosyayı yeniden üret
    python scripts/gen_config_epochs.py --check    # depodaki dosya üretilenle aynı mı (değilse çıkış 1)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tradingbot.research_engine import epochs as EP  # noqa: E402

DEFAULT_OUT = ROOT / "tradingbot" / "research_engine" / "library" / "config_epochs.json"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def gather(repo: Path) -> tuple[dict[str, str], dict[str, str]]:
    texts, shas = {}, {}
    for rel in EP.releases_needed():
        shas[rel] = git(repo, "rev-parse", "--verify", f"{rel}^{{commit}}").strip()
        texts[rel] = git(repo, "show", f"{shas[rel]}:config.yaml")
    return texts, shas


def seal_ms(repo: Path) -> int:
    """`registry.T_SEAL_MS` (modül import edilmeden: registry bu dosyayı yükler); 0 = henüz mühürlenmedi."""
    text = (repo / "tradingbot" / "research_engine" / "registry.py").read_text(encoding="utf-8")
    m = re.search(r"^T_SEAL_MS = (\d+)$", text, re.M)
    return int(m.group(1)) if m else 0


def added_at(repo: Path, rel_path: str) -> int | None:
    """Dosyayı depoya ekleyen ilk commit'in işleyen zamanı (s); izlenmiyorsa None."""
    out = git(repo, "log", "--diff-filter=A", "--format=%ct", "--", rel_path).split()
    return int(out[-1]) if out else None


def verify(repo: Path, shas: dict[str, str]) -> list[str]:
    errs: list[str] = []
    documented = {d["release"] for d in EP.DEPLOYS}
    t_seal = seal_ms(repo)
    for f in sorted((repo / "deploy" / "releases").glob("tb-deploy-*.sh")):
        rel = f.stem[len("tb-deploy-"):]
        if rel in documented:
            continue
        at = added_at(repo, f.relative_to(repo).as_posix())
        if t_seal and (at is None or at * 1000 > t_seal):
            continue                                   # mühürden sonra: V1'in kapsamı dışında (gece yeni dönem açar)
        errs.append(f"belgelenmemiş worker sürümü: {f.name} (epochs.DEPLOYS'a dağıtım kaydı eklenmeli)")
    seq = [d["release"] for d in EP.DEPLOYS if d["status"] in ("BASE", "DEPLOYED")]
    for a, b in zip(seq, seq[1:]):
        r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", shas[a], shas[b]], capture_output=True)
        if r.returncode != 0:
            errs.append(f"{a} → {b}: ata değil")
    for rel, bound, commit in EP.bound_commits():
        t = git(repo, "log", "-1", "--format=%cI", commit).strip()
        if datetime.fromisoformat(t).astimezone(timezone.utc) != datetime.fromisoformat(bound).astimezone(timezone.utc):
            errs.append(f"{rel}: sınır {bound} ≠ {commit} commit zamanı {t}")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--check", action="store_true", help="yazma; depodaki dosya üretilenle aynı değilse çıkış 1")
    a = ap.parse_args(argv)
    repo = Path(a.repo)
    texts, shas = gather(repo)
    errs = verify(repo, shas)
    if errs:
        print("\n".join(errs), file=sys.stderr)
        return 2
    text = EP.render(EP.build(texts, shas))
    out = Path(a.out)
    if a.check:
        cur = out.read_text(encoding="utf-8") if out.exists() else ""
        if cur != text:
            print(f"FARK: {out} üretilenle aynı değil", file=sys.stderr)
            return 1
        print(f"AYNI: {out}")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"yazıldı: {out} ({len(text.encode('utf-8'))} bayt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
