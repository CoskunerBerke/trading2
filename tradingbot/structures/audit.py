# -*- coding: utf-8 -*-
"""İLERİ YÜRÜYÜŞ BÜTÜNLÜK DENETİMİ — ortak yapı analizinin (structures_v1) geriye boyamadığının ölçümü.

Her kapanmış barda analiz, o ana kadarki barlarla (kayan pencere) YENİDEN hesaplanır ve kayıtlar kimlikleriyle izlenir.
İHLAL (sıfır beklenir):
  ANCHOR_AFTER_LAST_CLOSED_BAR / PIVOT_CONFIRMED_AFTER_DECISION_TIME / EVENT_AFTER_DECISION_TIME:* — gelecekten bilgi;
  ANCHOR_REWRITTEN — aynı kimlikte var olan dayanak değişti ya da silindi (gelişen yapıya EKLEME serbest);
  LEVELS_CHANGED_AFTER_EVENT — teyit/bozulma/süre olayından SONRA tetik/geçersizlik/stop/hedef/teyit anı değişti;
  EVENT_TIME_CHANGED:* — bir kez yazılan olay zamanı değişti;
  STATUS_WENT_BACK / TERMINAL_STATUS_CHANGED — durum geri döndü ya da terminal durum değişti;
  IDENTITY_SWITCH_AT_EVENT — oluşan kayıt kendi tetiği kesildiği barda kayboldu ve aynı ad/tarafta YENİ kimlikli
    teyitli kayıt doğdu (o barda teyit olan yeni bir pivot yoksa).
BİLGİ (meşru gelişme; sayılır): FORMING_LEVEL_REVISIONS, ANCHORS_APPENDED_WHILE_DEVELOPING,
  FORMING_INTERPRETATION_REPLACED, REDEFINED_BY_NEW_PIVOT_AT_TRIGGER_BAR, LATE_BREAK_EVENTS_AFTER_EXPIRY,
  FORMING_WITHDRAWN (oluşan kayıt terminal durum olmadan analizden çıktı).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .analysis import analyze

RANK = {"FORMING": 0, "CONFIRMED": 1, "BROKEN": 2, "EXPIRED": 2}


def walk_forward_audit(rows: list[dict[str, Any]], *, market: str, symbol: str, timeframe: str, step_ms: int,
                       n_eval: int, window: int = 400, max_examples: int = 8) -> dict[str, Any]:
    v: Counter = Counter()
    info: Counter = Counter()
    examples: list[dict[str, Any]] = []
    names_seen: Counter = Counter()
    names_conf: Counter = Counter()
    rejects: Counter = Counter()
    first_anc: dict = {}
    frozen: dict = {}
    rev_prev: dict = {}
    ev_seen: dict = {}
    last_status: dict = {}
    late_seen: set = set()
    prev_ids: set = set()
    prev_name: dict = {}
    n_an = 0

    def ex(d: dict[str, Any]) -> None:
        if len(examples) < max_examples:
            examples.append(dict(d, symbol=symbol, tf=timeframe))
    for i in range(max(0, len(rows) - n_eval), len(rows)):
        win = rows[max(0, i - window + 1): i + 1]
        as_of = int(rows[i]["timestamp"]) + int(step_ms)
        an = analyze(market=market, symbol=symbol, timeframe=timeframe, bars=win, as_of_ms=as_of)
        n_an += 1
        for rj in an.get("rejects") or []:
            rejects[str(rj.get("reason") or rj.get("code") or "?")] += 1
        recs = an.get("records") or []
        new_this_step = {r["pattern_id"] for r in recs if r["pattern_id"] not in first_anc}
        ids = set()
        last_ts = int(win[-1]["timestamp"])
        for r in recs:
            pid = r["pattern_id"]
            ids.add(pid)
            st = r.get("status")
            anc = [(int(a["ts"]), round(float(a["price"]), 10), a.get("role")) for a in (r.get("anchors") or [])]
            if any(a[0] > last_ts for a in anc):
                v["ANCHOR_AFTER_LAST_CLOSED_BAR"] += 1
            if any((a.get("confirmed_at_ms") or 0) > as_of for a in (r.get("anchors") or [])):
                v["PIVOT_CONFIRMED_AFTER_DECISION_TIME"] += 1
            for key in ("confirmed_at_ms", "broken_at_ms", "expired_at_ms"):
                if r.get(key) and int(r[key]) > as_of:
                    v["EVENT_AFTER_DECISION_TIME:" + key] += 1
            if pid not in first_anc:
                first_anc[pid] = anc
                names_seen[r.get("name")] += 1
            else:
                old = first_anc[pid]
                if len(anc) < len(old) or anc[:len(old)] != old:
                    v["ANCHOR_REWRITTEN"] += 1
                    ex({"kind": "ANCHOR_REWRITTEN", "pattern_id": pid, "name": r.get("name"), "first": old[-3:], "now": anc[-3:]})
                elif len(anc) > len(old):
                    info["ANCHORS_APPENDED_WHILE_DEVELOPING"] += 1
                    first_anc[pid] = anc
            snap = ((r.get("trigger") or {}).get("level"), (r.get("invalidation") or {}).get("level"), r.get("stop"),
                    tuple(r.get("targets") or []), r.get("confirmed_at_ms"))
            for key in ("confirmed_at_ms", "broken_at_ms", "expired_at_ms"):
                val = r.get(key) if (key != "expired_at_ms" or st == "EXPIRED") else None
                if val is None:
                    continue
                prev = ev_seen.get((pid, key))
                if prev is not None and prev != val:
                    v["EVENT_TIME_CHANGED:" + key] += 1
                    ex({"kind": "EVENT_TIME_CHANGED:" + key, "pattern_id": pid, "name": r.get("name"), "first": prev, "now": val})
                ev_seen[(pid, key)] = val
            if pid in frozen:
                if frozen[pid] != snap:
                    v["LEVELS_CHANGED_AFTER_EVENT"] += 1
                    ex({"kind": "LEVELS_CHANGED_AFTER_EVENT", "pattern_id": pid, "name": r.get("name"),
                        "frozen": repr(frozen[pid])[:160], "now": repr(snap)[:160]})
            elif st != "FORMING":
                frozen[pid] = snap
            elif pid in rev_prev and rev_prev[pid] != snap[:4]:
                info["FORMING_LEVEL_REVISIONS"] += 1
            rev_prev[pid] = snap[:4]
            if pid in last_status and RANK.get(st, 0) < RANK.get(last_status[pid], 0):
                v["STATUS_WENT_BACK"] += 1
                ex({"kind": "STATUS_WENT_BACK", "pattern_id": pid, "name": r.get("name"), "from": last_status[pid], "to": st})
            if pid in last_status and last_status[pid] in ("BROKEN", "EXPIRED") and st != last_status[pid]:
                v["TERMINAL_STATUS_CHANGED"] += 1
                ex({"kind": "TERMINAL_STATUS_CHANGED", "pattern_id": pid, "name": r.get("name"), "from": last_status[pid], "to": st})
            if r.get("broken_after_expiry") and pid not in late_seen:
                late_seen.add(pid)
                info["LATE_BREAK_EVENTS_AFTER_EXPIRY"] += 1
            if st == "CONFIRMED" and last_status.get(pid) != "CONFIRMED":
                names_conf[r.get("name")] += 1
            last_status[pid] = st
        gone_forming = [g for g in prev_ids - ids if last_status.get(g) == "FORMING"]
        info["FORMING_WITHDRAWN"] += len(gone_forming)
        born_conf = {(r.get("name"), r.get("side")) for r in recs if r.get("status") == "CONFIRMED" and r["pattern_id"] in new_this_step}
        born_form = {(r.get("name"), r.get("side")) for r in recs if r.get("status") == "FORMING" and r["pattern_id"] in new_this_step}
        redefined = {(r.get("name"), r.get("side")) for r in recs if r["pattern_id"] in new_this_step and any(
            int(a_.get("confirmed_at_ms") or 0) == as_of for a_ in (r.get("anchors") or []))}
        c_last = win[-1]["close"]
        for g in gone_forming:
            nm, sd, trig = prev_name.get(g, (None, None, None))
            crossed = trig is not None and ((c_last > trig) if sd == "LONG" else (c_last < trig))
            if crossed and (nm, sd) in born_conf and (nm, sd) in redefined:
                info["REDEFINED_BY_NEW_PIVOT_AT_TRIGGER_BAR"] += 1
            elif crossed and (nm, sd) in born_conf:
                v["IDENTITY_SWITCH_AT_EVENT"] += 1
                ex({"kind": "IDENTITY_SWITCH_AT_EVENT", "pattern_id": g, "name_side": [nm, sd]})
            elif (nm, sd) in born_form:
                info["FORMING_INTERPRETATION_REPLACED"] += 1
        prev_name = {r["pattern_id"]: (r.get("name"), r.get("side"), (r.get("trigger") or {}).get("level")) for r in recs}
        prev_ids = ids
    return {"analyses": n_an, "violations": dict(v), "info": dict(info), "examples": examples,
            "distinct_records": sum(names_seen.values()), "records_by_name": dict(names_seen.most_common()),
            "confirmations_by_name": dict(names_conf.most_common()), "rejects": dict(rejects)}


__all__ = ["walk_forward_audit"]
