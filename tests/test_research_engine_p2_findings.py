# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2b — §5.10 akıl sağlığı: Box stop genişliği bulgusunun config dönemine bölünerek yeniden
üretilmesi (docs/SYSTEM_LEARNING_ENGINE_V1.md §5.10; P2 kabul 11).

**Dürüst sınır:** gerçek VPS verisi (Box'ın 2026-10-03 öncesi işlemleri) testte YOKTUR. Bu test bulgunun EN YAKIN
sentetik yeniden üretimidir: eski dönemde (E0, `min_stop_pct` 0,32) gerçek bulgunun sayılarıyla 72 işlem stop < %0,5 ve
176 işlem stop %0,5–1; iki kovanın brüt avantajı AYNI tanımlıdır (işlem yönüne aynı küçük kayma), fark yalnız stop
genişliğinden doğar. Motorun gerçek boru hattı (S1a arşiv → S1b günlük → S2 atıf) koşulur ve `attribution/_build.json`'un
`sanity_5_10_box_stop_width` bloğu (dönem başına kova: n, ortalama net R, kayıp kodları) okunur. Beklenen: dar kovanın
ortalaması negatif, %0,5–1 kovası ondan belirgin iyi ve pozitif; dar kovanın kayıplarında `TIGHT_STOP_COST_MULTIPLIER` /
`STOP_TOO_TIGHT` baskın. Gerçek sayılar (−0,56R / +0,17R) VPS'te aynı blokta görülür (`--check`, P2 bölümü).
Bulgu uygulanmış bir sahip kararıdır (`min_stop_pct: 0.5`, 2026-10-03): motor aday üretmez, yeni dönemi izler.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import FakeHost, FakeVps, run_engine_night  # noqa: E402
from research_engine_p2_fixtures import config_text  # noqa: E402
from research_engine_p2b_fixtures import BOX_TIGHT_N, BOX_WIDE_N, box_epoch_world  # noqa: E402

from tradingbot.research_engine import analysis as A  # noqa: E402
from tradingbot.research_engine import selfcheck as SC  # noqa: E402

UTC = timezone.utc


def test_box_stop_width_finding_is_reproduced_per_config_epoch(tmp_path):
    v = FakeVps(tmp_path / "w")
    box_epoch_world(v)
    host = FakeHost(tmp_path / "host", config_text=config_text())
    SC.register_epoch(v.paths, "b" * 40, datetime(2020, 1, 1, tzinfo=UTC), "t", code_hash=SC.engine_code_hash())
    st = run_engine_night(v, host, datetime(2026, 9, 29, 1, 40, tzinfo=UTC))
    assert st["result"] == "SUCCESS", {k: (s.get("status"), s.get("error")) for k, s in st["stages"].items()}
    b = json.loads((v.paths.attribution / "_build.json").read_text(encoding="utf-8"))
    rep = b["sanity_5_10_box_stop_width"]
    assert set(rep) == {"E0"}, "hepsi eski dönemde (2026-09-30 öncesi); dönem sınırı aşılmaz"
    tight, wide = rep["E0"]["<0.5"], rep["E0"]["0.5-1"]
    assert (tight["n"], wide["n"]) == (BOX_TIGHT_N, BOX_WIDE_N)
    assert tight["mean_r"] < 0 < wide["mean_r"] and wide["mean_r"] - tight["mean_r"] > 0.2, (tight, wide)
    top = list(tight["codes"])[:3]
    assert {"TIGHT_STOP_COST_MULTIPLIER", "STOP_TOO_TIGHT"} & set(top), tight["codes"]
    # kod payı: dar kovada maliyet çarpanı kodu, geniş kovadakinden belirgin sık
    share = {k: rep["E0"][k]["codes"].get("TIGHT_STOP_COST_MULTIPLIER", 0) / rep["E0"][k]["n"] for k in ("<0.5", "0.5-1")}
    assert share["<0.5"] > 2 * share["0.5-1"], share
    # aynı tablo atıf satırlarından yeniden hesaplanır (blok deterministik)
    assert A.sanity_box_stop_width(A.iter_attribution(v.paths)) == rep
