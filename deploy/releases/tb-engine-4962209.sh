#!/usr/bin/env bash
# trading2 — SÜREKLİ ÖĞRENME MOTORU P1a sürüm betiği (PAPER; gerçek para YOK). docs/SYSTEM_LEARNING_ENGINE_V1.md §2.2–§2.5,
# §9.3, §9.4, §10 P1a. KÜÇÜK ve AYRILMIŞ: yalnız motor adımları (tb-deploy-*.sh kopyalanmadı).
#
# NE YAPAR: /opt/tradingbot/engine-app AYRI klonunu (git worktree DEĞİL) hedef koda (TIP) sabitler ve önceden derler;
#   data/research'ü İLK başlatmadan ÖNCE tradingbot sahipliğiyle (0750) oluşturur; tradingbot-engine-night.{service,timer}
#   birimlerini KAPILI kurar; birimi bir kez ELLE çalıştırır (smoke); smoke geçerse zamanlayıcıyı (01:37 UTC) açar.
# NE YAPMAZ: worker'ı ve dashboard'ı DURDURMAZ / YENİDEN BAŞLATMAZ; /opt/tradingbot/app, config.yaml, state, defterler
#   DEĞİŞMEZ; ağlı birim yok (motor PrivateNetwork=yes). Motor yalnız data/research'e yazar. Betik yalnız kendi
#   klasörlerine (engine-app, birim dosyaları, deploy-logs/engine-*) yazar.
#
# Kullanım (VPS'te, sahip çalıştırır; dosya adı tb-engine-<hedefin ilk 7 hanesi>.sh):
#   sudo bash tb-engine-4962209.sh --dry-run    # yalnız denetim, hedef kod GEÇİCİ klonda (yalnız deploy-logs'a kayıt)
#   sudo bash tb-engine-4962209.sh              # dağıt: klon + dizin + kapılı birim kurulumu + elle smoke
#   sudo bash tb-engine-4962209.sh --check      # 14 gün her gün (yalnız deploy-logs/engine-<sha7>-samples.jsonl'a yazar)
#   sudo bash tb-engine-4962209.sh --ab-report  # 14. geceden sonra bir kez: AÇIK/KAPALI geceler aynı saat penceresinde
#   sudo bash tb-engine-4962209.sh --rollback   # zamanlayıcı + servis + engine-app kaldırılır; data/research KALIR
# Çıkış: 0 tamam · 1 durdu (ön denetimde: hiçbir şey değişmedi) · 2 smoke geçmedi ya da saati değil (zamanlayıcı KAPALI)
#   · 3 reload sonrası MemoryMax kayması (motor birimleri geri alındı) · 4 dağıtıldı ama bir değişmez KALDI.
#
# DAEMON-RELOAD GÜVENLİĞİ (§9.3): depodaki worker birimi MemoryMax=4G der, VPS'te 6G override vardır. daemon-reload (enable/
#   disable'ın örtük reload'u dahil) YALNIZ reload_gate'ten geçer: worker ve dashboard NeedDaemonReload=no ve worker 6G;
#   reload'dan SONRA 6G yeniden doğrulanır, değilse motor birimleri geri alınır ve betik durur. setup_vps_v3.sh ASLA
#   çalıştırılmaz; systemctl set-property / edit / revert kullanılmaz.
# SAAT: dağıtım, smoke ve kuru çalışma UTC 00:00–04:00 (gece birimi) ve 4h yayın pencerelerinde (hh:00–hh:35, hh 4'ün
#   katı; +5 dk pay) BAŞLAMAZ; başka sürümün yeniden başlatmasından (deploy-logs/*-restart-at.txt) 3 gün geçmeden DURUR.
# KAPATMA (anında, veri yerinde kalır): sudo systemctl disable --now tradingbot-engine-night.timer
#   (disable örtük reload yapar: önce  systemctl show tradingbot-worker -p NeedDaemonReload  → no olmalı; değilse yalnız
#   sudo systemctl stop tradingbot-engine-night.timer  yeterlidir). Tam geri alma: bu betiğin --rollback'i.
# OKUMA (belge ↔ kod): §10 "--check › GÜNLÜK HEDEF" içeriği engine-status --brief'ten gelir (son 7 gün, durum etiketi,
#   hüküm paydasıyla en iyi enstrüman, şans oranı, son bakış; k* yok). §2.9 Box kaçan bar / koruyucu izleyici gecikmesi /
#   veri reddi oranı worker'da zaman damgalı tutulmaz (birikimli sayaç + son değerler): --ab-report bunları --check
#   örnekleri arasındaki artıştan ve aradaki TEK A/B gecesine göre KABA gruplar (gece + gündüz); tur süreleri, 418/429,
#   PAPER_RESEARCH_ACTIVE ve tur hataları ise worker günlüğünden her gece 01:37–03:40 UTC penceresinde TAM ölçülür.
#   A/B KAPALI gecesi = S0 + S1s (yalnız ölçülmüş anlık görüntü; §7.1 W(D) için; night.py madde 2); A/B dönemi motor
#   KOD özetine bağlıdır (selfcheck madde 5). K6 --check'te otomatiktir. Ağır adımlar nice 19 + ionice idle (§2.9).
set -Eeuo pipefail

TIP="49622093a3d3bc334b454b4df2dcdb4e53eb5144"    # P1a kod commit'i (1. inceleme turu + ayna defter M2X günlük hedef dışı)
T7="${TIP:0:7}"
BRANCH_REF="refs/heads/claude/gifted-knuth-0ehpcs"
REPO_URL="${TB_ENGINE_REPO_URL:-https://github.com/CoskunerBerke/trading2.git}"   # içerik TAM SHA'ya bağlıdır
SVC_SHA256="0db7a26a2a2c955ef3d9cdb83834f816c0556752233a8f312b3ffbbd7ca89b2e"   # deploy/tradingbot-engine-night.service @TIP
TMR_SHA256="59fa82a34182f1e8c50377d6633191881a2250e640098cf8c369a5cff46b6ed6"   # deploy/tradingbot-engine-night.timer @TIP
INV_TESTS=33                                     # bağımsız koşucunun alt kümesi (kabul 1–8, 13, 14)

BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="$BASE/app"; ENG="$BASE/engine-app"; VENV="$BASE/venv"; DATA="$BASE/data"; STATE="$DATA/state"; RES="$DATA/research"
SD="${TRADINGBOT_SYSTEMD_DIR:-/etc/systemd/system}"
SVC_USER="${TRADINGBOT_USER:-tradingbot}"; SVC_GROUP="${TRADINGBOT_GROUP:-$SVC_USER}"
UNIT="tradingbot-engine-night"; SVC="$UNIT.service"; TMR="$UNIT.timer"; DROPIN_DIR="$SD/$SVC.d"
WORKER="tradingbot-worker.service"; DASH="tradingbot-dashboard.service"; DROPIN="$DROPIN_DIR/50-tb-engine.conf"
MEM_EXPECT=6442450944; ENGINE_MEM=536870912     # worker MemoryMax 6G (VPS override) · gece birimi 512M (= ENGINE_EXPECTED_…)
LOGDIR="$BASE/deploy-logs"; BASELINE="$LOGDIR/engine-$T7-deploy.json"; SAMPLES="$LOGDIR/engine-$T7-samples.jsonl"
MODE="${1:-deploy}"; SMOKE_TIMEOUT=1800
TMP=""; WHY=""; CHANGED=(); INV_N=0; INV_FAIL=()

say()  { printf '\n== %s\n' "$*"; };          ok()  { printf '   OK  %s\n' "$*"; }
warn() { printf '   UYARI  %s\n' "$*"; };       die() { printf '\nDUR: %s\n' "$1" >&2; exit "${2:-1}"; }
as_svc() { sudo -u "$SVC_USER" "$@"; }
# svc DİZİN KOMUT…: servis kullanıcısı, BOŞ ortam (servis ortamı aktarılmaz), verilen dizinde
svc() { local d="$1"; shift; as_svc env -i -C "$d" PATH=/usr/local/bin:/usr/bin:/bin HOME="$BASE" LANG=C.UTF-8 TZ=UTC \
          PYTHONDONTWRITEBYTECODE=1 "$@"; }
gitx() { local d="$1"; shift; svc "$d" GIT_TERMINAL_PROMPT=0 git "$@"; }
LOW=(nice -n 19); if command -v ionice >/dev/null 2>&1; then LOW+=(ionice -c3 -t); fi   # ağır adımlar: CPU/IO en düşük
sc_show() { systemctl show "$1" -p "$2" --value 2>/dev/null || true; }
cleanup() { if [[ -n "$TMP" && "$TMP" == /tmp/tb-engine-* && -d "$TMP" ]]; then rm -rf -- "$TMP"; fi; }
on_err() { printf '\nDUR: beklenmeyen hata (satır %s). Bu çalıştırmada değişen: %s\n' "$1" "${CHANGED[*]:-hiçbir şey}" >&2
           if (( ${#CHANGED[@]} )); then printf '   Geri alma: sudo bash %s --rollback (data/research kalır)\n' "$0" >&2; fi
           exit 1; }
trap cleanup EXIT
trap 'on_err $LINENO' ERR
# inv AD 0|1 AÇIKLAMA — adlandırılmış değişmez. gate: aynı, ama dağıtımda ilk KALDI'da durur (hiçbir şey değişmeden).
inv() {
  INV_N=$((INV_N + 1))
  if [[ "$2" == 0 ]]; then printf '   [tamam] #%02d %-24s %s\n' "$INV_N" "$1" "$3"
  else printf '   [KALDI] #%02d %-24s %s\n' "$INV_N" "$1" "$3"; INV_FAIL+=("$1"); fi
}
gate() { inv "$@"; if [[ "$2" != 0 && "$MODE" == deploy ]]; then die "$1: $3. HİÇBİR ŞEYE DOKUNULMADI"; fi; }
[[ "$TIP" =~ ^[0-9a-f]{40}$ ]] || die "TIP tam bir commit SHA'sı değil"
[[ "$MODE" =~ ^(deploy|--dry-run|--check|--ab-report|--rollback)$ ]] \
  || die "bilinmeyen seçenek: $MODE (--dry-run | --check | --ab-report | --rollback | seçeneksiz = dağıt)"
if [[ "$(id -u)" != 0 && -z "${TB_ENGINE_ALLOW_NON_ROOT:-}" ]]; then die "root olarak çalıştırın: sudo bash $0 $*"; fi

# time_ok [EN_AZ_DK] — 0: dağıtım/smoke için uygun saat (UTC); değilse 1 ve WHY. Gece birimi penceresi 00:00–04:00;
# 4h yayın pencereleri hh:00–hh:35 (+5 dk pay); EN_AZ_DK: bir sonraki 4h penceresine en az bu kadar dakika kalmalı.
time_ok() {
  local hm h m left
  hm="$(date -u +%H:%M)"; h=$((10#${hm%%:*})); m=$((10#${hm##*:}))
  if (( h < 4 )); then WHY="UTC $hm — gece birimi penceresi (00:00–04:00 UTC)"; return 1; fi
  if (( h % 4 == 0 && m < 40 )); then WHY="UTC $hm — 4h yayın penceresi (hh:00–hh:35, +5 dk pay)"; return 1; fi
  left=$(( (4 - h % 4) * 60 - m ))
  if (( left < ${1:-0} )); then WHY="UTC $hm — sonraki 4h penceresine $left dk (en az ${1} dk gerekir)"; return 1; fi
  WHY="UTC $hm"; return 0
}

# reload_gate — daemon-reload'un (ve enable/disable örtük reload'unun) TEK yolu (§9.3). 0: güvenli.
reload_gate() {
  local u v mm; WHY=""
  for u in "$WORKER" "$DASH"; do
    v="$(sc_show "$u" NeedDaemonReload)"
    if [[ "$v" != no ]]; then WHY="$u NeedDaemonReload=${v:-?} (reload birim kaymasını uygular; 4G↔6G)"; return 1; fi
  done
  mm="$(sc_show "$WORKER" MemoryMax)"
  if [[ "$mm" != "$MEM_EXPECT" ]]; then WHY="worker MemoryMax=${mm:-?} (beklenen $MEM_EXPECT = 6G)"; return 1; fi
  return 0
}

# Gömülü Python araçları (yalnız standart kütüphane; root olarak `python -I`, depo kodu İÇE ALINMAZ).
read -r -d '' PYTOOL <<'PY' || true
import ast, hashlib, json, math, os, pwd, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone
UTC = timezone.utc
STAGES = ("S0", "S1s", "S1a", "S3", "S7", "S7b")
ACCEPT = [1, 2, 3, 4, 5, 6, 7, 8, 13, 14]
def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
def ep(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=UTC)).timestamp()
def iv(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None
def f(x, nd=1):
    return "—" if x is None else ("%.*f" % (nd, x)).replace(".", ",")
def pct(v, q):
    v = sorted(v)
    return v[max(0, math.ceil(q * len(v)) - 1)] if v else None
def runs(res):
    d = os.path.join(res, "runs")
    try:
        names = sorted(n for n in os.listdir(d) if re.match(r"^\d{8}T\d{6}Z(-\d+)?$", n))
    except OSError:
        return []
    return [r for r in (rj(os.path.join(d, n, "run_status.json")) for n in names) if isinstance(r, dict)]
def epoch(res, tip):
    """Hedef SHA'nın A/B dönem satırı (dönem = motor kod özeti; aynı kodla yeniden sabitlenen SHA eski dönemdedir)."""
    try:
        with open(os.path.join(res, "runs", "engine_epochs.jsonl"), encoding="utf-8") as fh:
            rows = [json.loads(ln) for ln in fh if ln.strip()]
    except (OSError, ValueError):
        return None
    return next((r for r in rows if r.get("engine_sha") == tip), None)
def release_day(res, tip):
    r = epoch(res, tip) or {}
    return r.get("epoch_day") or r.get("first_seen_day")
def mine(res, tip):
    """Bu sürümün (aynı motor kodunun) çalıştırmaları."""
    code = (epoch(res, tip) or {}).get("code_hash")
    return [r for r in runs(res) if (r.get("shas") or {}).get("engine") == tip
            or (code and (r.get("shas") or {}).get("engine_code") == code)]
def jscan(since, until=None, unit="tradingbot-worker.service"):
    """Worker günlüğü (short-unix; akış halinde okunur): tur süreleri + olay sayıları; okunamazsa None. Günlükte her kayıt
    hem METİN hem JSON basılabilir: ikisi ayrı sayılır, büyüğü alınır."""
    cmd = ["journalctl", "-u", unit, "--since", "@%d" % since, "-o", "short-unix", "--no-pager", "-q"]
    if until is not None:
        cmd[5:5] = ["--until", "@%d" % until]
    try:
        pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True,
                              errors="replace")
    except OSError:
        return None
    tours, cnt = [], {}
    for ln in pr.stdout:
        p = ln.rstrip("\n").split(" ", 2)
        if len(p) < 3 or ": " not in p[2]:
            continue
        msg = p[2].split(": ", 1)[1]
        m = re.search(r"ÖĞRENME: .* ([0-9]+(?:\.[0-9]+)?)s$", msg)
        if m:
            tours.append(float(m.group(1)))
        k = "j" if msg.startswith('{"ts"') else "t"
        for name, rx in (("rest418429", r"(^| )(429|418) https?://"), ("research_active", r"PAPER_RESEARCH_ACTIVE"),
                         ("tour_error", r"İzleme turu hatası")):
            if re.search(rx, msg):
                cnt[(name, k)] = cnt.get((name, k), 0) + 1
    out = {n: max(cnt.get((n, "t"), 0), cnt.get((n, "j"), 0)) for n in ("rest418429", "research_active", "tour_error")}
    out["tours"] = tours
    return out if pr.wait() == 0 else None
def c_paper(state):
    p = os.path.join(state, "mode.json")
    if not os.path.exists(p):
        return print("state/mode.json yok → worker varsayılanı PAPER") or 0
    d = rj(p)
    if not isinstance(d, dict):
        return print("state/mode.json OKUNAMADI") or 1
    print("mode=%s live_order_path_enabled=%s" % (d.get("mode"), d.get("live_order_path_enabled")))
    return 0 if d.get("mode") == "PAPER" and d.get("live_order_path_enabled") is not True else 1
def c_runner(path, n):
    d = rj(path) or {}
    print("%s geçti · %s kaldı · %s atlandı · kabul %s" % (d.get("passed"), d.get("failed"), d.get("skipped"),
                                                          d.get("acceptance_passed")))
    return 0 if (d.get("failed"), d.get("skipped"), d.get("passed"), d.get("acceptance_passed")) == (0, 0, int(n), ACCEPT) else 1
def _imports(node, top_only):
    for ch in ast.iter_child_nodes(node):
        if top_only and isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(ch, ast.Import):
            yield from (a.name for a in ch.names)
        elif isinstance(ch, ast.ImportFrom):
            mod = "." * ch.level + (ch.module or "")
            yield mod
            yield from (mod + "." + a.name for a in ch.names)
        yield from _imports(ch, top_only)
def c_ast(tree):
    bad = []
    red = os.path.join(tree, "tradingbot", "research_engine")
    for fn in sorted(os.listdir(red)):
        if fn.endswith(".py"):
            with open(os.path.join(red, fn), encoding="utf-8") as fh:
                t = ast.parse(fh.read())
            bad += ["%s: %s" % (fn, n) for n in _imports(t, False)
                    if n.split(".")[0] == "sqlite3" or "config_v3" in n or n.rsplit(".", 1)[-1] in ("load_config", "load_v3")]
    for fn in ("cli.py", "cli_v3.py"):
        with open(os.path.join(tree, "tradingbot", fn), encoding="utf-8") as fh:
            t = ast.parse(fh.read())
        bad += ["%s (üst düzey): %s" % (fn, n) for n in _imports(t, True) if "research_engine" in n]
    print("yasak import yok" if not bad else "YASAK: " + "; ".join(bad[:5]))
    return 1 if bad else 0
def c_lastrun(res):
    st = rj(os.path.join(res, "summary", "run_status.json")) or {}
    print(st.get("run_id") or "")
    return 0
def c_smoke(res, tip, pre, t0, user):
    st = rj(os.path.join(res, "summary", "run_status.json")) or {}
    rid, sc, bad = st.get("run_id"), st.get("selfcheck") or {}, []
    print("   smoke: %s · sonuç %s (çıkış %s) · plan %s · bayraklar %s" % (
        rid, st.get("result"), st.get("exit_code"), " ".join(st.get("plan") or []), ", ".join(st.get("flags") or []) or "—"))
    for k, v in sorted(((sc.get("isolation") or {}).get("checks") or {}).items()):
        print("     öz-denetim %-26s %s %s" % (k, v.get("status"), v.get("errno") or ""))
    bad += ["smoke'un yeni run_status.json'u yok"] if (not rid or rid == pre or (ep(st.get("started_at")) or 0) < float(t0) - 5) else []
    bad += ["öz-denetim %s" % sc.get("status")] if sc.get("status") != "OK" else []
    # Sürüm günü tam plan (SUCCESS). Aynı kodun A/B penceresinde YENİDEN dağıtımda KAPALI gün kuralı smoke'ta da
    # uygulanır (S0 + S1s [+ S1a]): sonuç AB_OFF* kabul, ama planlanan HER aşama OK ve plana göre dosyalar şart.
    day, plan, ab = str(st.get("started_at"))[:10], st.get("plan") or [], (sc.get("ab") or {}).get("status")
    okres = st.get("result") == "SUCCESS" or (st.get("result") == ab and ab in ("AB_OFF", "AB_OFF_ZORUNLU_ARŞİV"))
    if not okres or st.get("exit_code") != 0:
        bad.append("sonuç %s" % st.get("result"))
    for s in plan:
        if ((st.get("stages") or {}).get(s) or {}).get("status") != "OK":
            bad.append("aşama %s: %s" % (s, ((st.get("stages") or {}).get(s) or {}).get("status")))
    bad += ["engine SHA %s ≠ hedef" % (st.get("shas") or {}).get("engine")] if (st.get("shas") or {}).get("engine") != tip else []
    want = {"S0": ["summary/run_status.json", "runs/%s/run_status.json" % rid, "runs/engine_epochs.jsonl"],
            "S1s": ["snapshots/%s.json.gz" % day], "S1a": ["snapshots/%s.json.gz" % day], "S3": ["summary/daily_target.json"],
            "S7": ["summary/digest_tr.md", "summary/engine_summary.json"],
            "S7b": ["backup/research-small-%s.tar.gz" % day, "backup/research-small-%s.tar.gz.sha256" % day]}
    need = [n for s in STAGES if s in plan or s == "S0" for n in want[s]]
    bad += ["beklenen dosya yok: " + n for n in need if not os.path.isfile(os.path.join(res, n))]
    bad += ["runs/engine_epochs.jsonl hedef SHA'yı içermiyor"] if release_day(res, tip) is None else []
    uid, foreign = pwd.getpwnam(user).pw_uid, []
    for dp, dn, fn in os.walk(res):
        foreign += [os.path.join(dp, x) for x in dn + fn if os.lstat(os.path.join(dp, x)).st_uid != uid]
    if foreign:
        bad.append("%d dosya/klasör %s'a ait değil (ör. %s)" % (len(foreign), user, foreign[0]))
    print("".join("     KALDI: %s\n" % b for b in bad), end="")
    return 1 if bad else 0
def c_sample(state, samples, nr, pid, memmax):
    bt = rj(os.path.join(state, "box_timer.json")) or {}
    pm = rj(os.path.join(state, "protective_monitor.json")) or {}
    tours = drej = dvm = 0
    for n in (sorted(os.listdir(state)) if os.path.isdir(state) else []):
        p = os.path.join(state, n)
        if not n.endswith(".json") or not os.path.isfile(p) or os.path.getsize(p) > 20_000_000:
            continue
        d = rj(p)
        if isinstance(d, dict) and isinstance(d.get("counters"), dict) and isinstance(d.get("rejections"), dict):
            tours += iv(d["counters"].get("tours")) or 0
            drej += iv(d["counters"].get("data_rejected")) or 0
            dvm += iv(d["rejections"].get("DATA_VERDICT_MISSING")) or 0
    s = {"t": int(time.time()), "box_missed": iv(bt.get("missed_bars")), "box_evals": iv(bt.get("evaluations")),
         "box_lag_p50": bt.get("lag_p50_s"), "box_lag_max": bt.get("lag_max_s"), "pm_runs": iv(pm.get("runs")),
         "pm_p50": pm.get("duration_p50_s"), "pm_max": pm.get("duration_max_s"),
         "pm_last_at": (pm.get("last_run") or {}).get("at"), "tours": tours, "data_rejected": drej, "dvm": dvm,
         "nrestarts": iv(nr), "worker_pid": iv(pid), "memmax": iv(memmax)}
    try:
        with open(samples, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(s, sort_keys=True) + "\n")
    except OSError as exc:
        print("   ölçüm örneği yazılamadı: %s" % exc)
    return s
def c_baseline(path, tip, app_sha, nr, pid, memmax, rid, state, samples):
    b = {"schema": "tb_engine_deploy_v1", "tip": tip, "app_sha": app_sha, "deployed_at": int(time.time()),
         "worker": {"nrestarts": iv(nr), "pid": iv(pid), "memmax": iv(memmax)}, "smoke_run_id": rid}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(b, indent=1) + "\n")
    c_sample(state, samples, nr, pid, memmax)
    return 0
def mark(flag):
    return "[ölçülemedi]" if flag is None else ("[DİKKAT]" if flag else "[tamam]")
def c_check(res, state, tip, baseline, samples, memmax, nr, pid):
    b = rj(baseline) or {}
    allr = mine(res, tip)
    rs = allr[-15:]
    print("-- son çalıştırmalar (bu sürüm; en çok 15)")
    for r in rs:
        res_ = r.get("resources") or {}
        print("   %-18s %-22s plan %-17s A/B %-15s tepe bellek %s" % (
            r.get("run_id"), r.get("result"), " ".join(r.get("plan") or []),
            ((r.get("selfcheck") or {}).get("ab") or {}).get("status"),
            ("%s MB (%s × MemoryMax)" % (f((res_.get("memory_peak_bytes") or 0) / 1048576, 0), f(res_.get("memory_peak_ratio"), 2)))
            if res_.get("memory_peak_bytes") else "—"))
    print("   (bu sürümün çalıştırması yok)") if not rs else None
    print("-- P1a VPS kabul ölçütleri (§10; [DİKKAT] = bana iletin)")
    sm = next((r for r in rs if r.get("run_id") == b.get("smoke_run_id")), None)
    print("   %-13s K1 elle smoke: %s" % (mark(None if sm is None else sm.get("result") != "SUCCESS"),
                                         "%s %s" % (b.get("smoke_run_id"), sm.get("result")) if sm else "kaydı yok (runs/ budandı ya da dağıtım yok)"))
    first = next((r for r in allr if ((r.get("stages") or {}).get("S1a") or {}).get("status") == "OK"), None)
    if first:
        bk = ((first["stages"]["S1a"].get("result") or {}).get("books") or {})
        miss = ["%s %s/%s" % (k, v.get("new_closes"), (v.get("held") or {}).get("history")) for k, v in bk.items()
                if v.get("new_closes") != (v.get("held") or {}).get("history")]
        print("   %-13s K2 ilk arşiv (%s): %d defter; arşivlenen = ledger'ın tuttuğu history%s" % (
            mark(bool(miss)), first.get("run_id"), len(bk), (" — FARK: " + ", ".join(miss[:6])) if miss else ""))
    else:
        print("   %-13s K2 ilk arşiv: S1a'sı OK olan çalıştırma yok" % mark(None))
    inc = sum(1 for r in rs if ((((r.get("stages") or {}).get("S1a") or {}).get("result") or {}).get("inconsistent")))
    iso_ = sum(1 for r in rs if r.get("result") == "ISOLATION_BROKEN")
    skew = sum(1 for r in rs if "SKEW" in (r.get("flags") or []) or r.get("result") == "SKEW")
    fail = sum(1 for r in rs if r.get("result") in ("FAILED", "NOT_PAPER", "DISK_REFUSE"))
    print("   %-13s K3 INCONSISTENT %d · ISOLATION_BROKEN %d · SKEW %d · FAILED/NOT_PAPER/DISK %d" % (
        mark(bool(inc or iso_ or skew or fail) if rs else None), inc, iso_, skew, fail))
    pk = [x for x in ((r.get("resources") or {}).get("memory_peak_ratio") for r in rs) if isinstance(x, (int, float))]
    print("   %-13s K5 motor memory.peak en çok %s × MemoryMax (ölçüt ≤ 0,8)" % (mark(max(pk) > 0.8 if pk else None),
                                                                              f(max(pk) if pk else None, 2)))
    bdir = os.path.join(res, "backup")
    arcs = sorted(x for x in (os.listdir(bdir) if os.path.isdir(bdir) else []) if re.match(r"^research-small-.*\.tar\.gz$", x))
    vb = None
    if arcs:
        try:
            want = open(os.path.join(bdir, arcs[-1] + ".sha256"), encoding="utf-8").read().split()[0]
            h = hashlib.sha256()
            with open(os.path.join(bdir, arcs[-1]), "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            vb = h.hexdigest() == want
        except (OSError, IndexError):
            vb = False
    n7b = sum(1 for r in rs if ((r.get("stages") or {}).get("S7b") or {}).get("status") == "OK")
    print("   %-13s K7 araştırma yedeği: son %s sha256 %s · S7b OK çalıştırma %d" % (
        mark(None if vb is None else not vb), arcs[-1] if arcs else "—", {None: "—", True: "doğrulandı", False: "TUTMADI"}[vb], n7b))
    s1 = lambda r: ((r.get("stages") or {}).get("S1a") or {}).get("result") or ((r.get("stages") or {}).get("S1s") or {}).get("result")
    last = next((r for r in reversed(rs) if s1(r)), None)
    lr = s1(last) if last else {}
    fl = set(lr.get("flags") or [])
    miss = [k for k, v in (lr.get("books") or {}).items() if v.get("status") != "OK"]
    age = (lr.get("ledger_freshness") or {}).get("age_s")
    print("   %-13s defter okuma: %s · en yeni updated_at %s sa önce · %s" % (
        mark(bool(miss or fl & {"LEDGER_STALE", "NO_LEDGERS", "LEDGER_MISSING"}) if lr else None),
        ", ".join(sorted(fl & {"LEDGER_STALE", "NO_LEDGERS", "LEDGER_MISSING"})) or "bayrak yok",
        f(age / 3600.0 if age is not None else None, 1), ("sorunlu: " + ", ".join(miss)) if miss else "hepsi OK"))
    rot = []
    for k, v in ((lr or {}).get("books") or {}).items():
        rot += [(d, "%s/%s" % (k, kk)) for kk, d in (v.get("rotation_days") or {}).items() if isinstance(d, (int, float))]
    rmin = min(rot) if rot else (None, "—")
    print("   %-13s K8 rotasyon payı en az %s gün (%s; ölçüt ≥ 3)" % (mark(rmin[0] < 3 if rot else None), f(rmin[0], 1), rmin[1]))
    js = jscan(max(b.get("deployed_at") or 0, int(time.time()) - 86400)) or {}
    print("   %-13s 418/429 (worker günlüğü, son 24 sa) %s · PAPER_RESEARCH_ACTIVE %s (raporlanır) · tur hatası %s" % (
        mark(js["rest418429"] > 0 if js else None), js.get("rest418429", "—"), js.get("research_active", "—"),
        js.get("tour_error", "—")))
    w = b.get("worker") or {}
    print("   %-13s worker: NRestarts dağıtımda %s → şimdi %s · MemoryMax %s (6G = 6442450944) · PID %s → %s" % (
        mark((iv(memmax) != 6442450944) or (iv(nr) != w.get("nrestarts") if w else None)), w.get("nrestarts"), nr, memmax,
        w.get("pid"), pid))
    c_sample(state, samples, nr, pid, memmax)
    return 0
def c_k6(eng, sc):
    """VPS kabul 6: aynı tanımlı görünümler (kayıt: closed_at günü; cüzdan: ts günü) ARŞİVİN kapsadığı son gün − 2'ye
    kadar eşit olmalı (geç fonlama o zamana dek yerleşir); gerçekleşmemiş sütunu karşılaştırılmaz (farklı an)."""
    e, s_ = rj(eng) or {}, (rj(sc) or {}).get("daily_target") or {}
    upto = e.get("archived_through")
    if not e.get("books") or not s_.get("books") or not upto:
        print("   %-13s K6 scorecard --daily = engine-status --daily: karşılaştırılamadı" % mark(None))
        return 0
    cut = (datetime.strptime(upto, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    days = [d for d in e.get("days") or [] if d <= cut and d in (s_.get("days") or [])]
    bad = ["%s: betikte yok" % b for b in e["books"] if b not in s_["books"] or "error" in s_["books"][b]]
    bad += ["%s: motorda yok" % b for b, x in s_["books"].items() if b not in e["books"] and "error" not in x]
    for b, eb in e["books"].items():
        sd = (s_["books"].get(b) or {}).get("days") or {}
        for d in days:
            er, sr = eb["days"][d]["rec"], (sd.get(d) or {}).get("rec") or {}
            bad += ["%s %s kayıt.%s %s≠%s" % (b, d, k, er.get(k), sr.get(k)) for k in ("n", "net", "fees", "funding", "slippage")
                    if abs((er.get(k) or 0) - (sr.get(k) or 0)) > 1e-6]
            ew, sw = eb["days"][d]["wal_ts"], (sd.get(d) or {}).get("wal_ts") or {}
            if ew.get("complete") and sw.get("complete") and abs((ew.get("net") or 0) - (sw.get("net") or 0)) > 1e-6:
                bad.append("%s %s cüzdan %s≠%s" % (b, d, ew.get("net"), sw.get("net")))
    print("   %-13s K6 scorecard --daily = engine-status --daily: %d defter × %d gün (≤ %s; arşiv %s'e kadar)%s" % (
        mark(bool(bad) if days else None), len(e["books"]), len(days), cut, upto, (" — FARK: " + "; ".join(bad[:4])) if bad else ""))
    return 0
def c_ab(res, tip, baseline, samples, memmax, nr, emem):
    b = rj(baseline) or {}
    rel = release_day(res, tip)
    print("-- A/B geceleri (§2.9; gece = 01:37–03:40 UTC, AÇIK ve KAPALI aynı pencere)")
    if rel is None:
        return print("   sürüm günü kaydı yok (runs/engine_epochs.jsonl) — A/B kurulamaz") or 1
    byday = {}
    for r in mine(res, tip):
        t = ep(r.get("started_at"))
        if t is not None:
            d = datetime.fromtimestamp(t, UTC)
            if (d.hour, d.minute) >= (1, 30) and (d.hour, d.minute) < (3, 40):
                byday.setdefault(d.strftime("%Y-%m-%d"), r)
    g = {k: {"tours": [], "rest": 0, "ra": 0, "err": 0, "n": 0, "peak": []} for k in ("ON", "OFF")}
    nights, gone = [], 0
    for i in range(1, 15):
        d0 = datetime.strptime(rel, "%Y-%m-%d").replace(tzinfo=UTC) + timedelta(days=i)
        day, s, e = d0.strftime("%Y-%m-%d"), d0.timestamp() + 97 * 60, d0.timestamp() + 220 * 60
        if time.time() <= e:
            break
        r = byday.get(day)
        ab = ((r or {}).get("selfcheck") or {}).get("ab") or {}
        st = ab.get("status") or ("AB_ON" if d0.timetuple().tm_yday % 2 == 0 else "AB_OFF")
        js = jscan(s, e)
        plan = tuple((r or {}).get("plan") or [])   # AÇIK = tam plan; KAPALI = S0 + S1s (SKEW/kilit/yalıtım dışarıda)
        key = {("AB_ON", STAGES[:1] + STAGES[2:]): "ON", ("AB_OFF", ("S0", "S1s")): "OFF"}.get((st, plan)) if r is not None else None
        nights.append((day, st, (r or {}).get("result") or ("çalıştırma kaydı yok" if r is None else "?"), js, key))
        if key and js is not None:
            gg = g[key]
            gg["n"], gg["tours"], gg["rest"] = gg["n"] + 1, gg["tours"] + js["tours"], gg["rest"] + js["rest418429"]
            gg["ra"], gg["err"] = gg["ra"] + js["research_active"], gg["err"] + js["tour_error"]
            pk = ((r or {}).get("resources") or {}).get("memory_peak_ratio")
            if isinstance(pk, (int, float)):
                gg["peak"].append(pk)
        elif key:
            gone += 1
    print("   sürüm günü %s · pencere %s … %s · %d/14 gece geçti%s" % (
        rel, (datetime.strptime(rel, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"),
        (datetime.strptime(rel, "%Y-%m-%d") + timedelta(days=14)).strftime("%Y-%m-%d"), len(nights),
        "" if len(nights) >= 14 else " — ARA GÖRÜNÜM (14 gece dolmadı)"))
    for day, st, rr, js, key in nights:
        print("   %s %-22s %-24s %s" % (day, st, rr, "günlük okunamadı" if js is None else "tur %d (p95 %s sn) · 418/429 %d · araştırma %d · tur hatası %d" % (
            len(js["tours"]), f(pct(js["tours"], 0.95), 0), js["rest418429"], js["research_active"], js["tour_error"])))
    on, off = g["ON"], g["OFF"]
    p95on, p95off = pct(on["tours"], 0.95), pct(off["tours"], 0.95)
    print("-- karşılaştırma (AÇIK %d gece · KAPALI %d gece; AB_OFF_ZORUNLU_ARŞİV hariç%s)" % (
        on["n"], off["n"], (" · günlüğü okunamayan %d gece" % gone) if gone else ""))
    res4 = [("tur p50/p95 (sn)", None if p95on is None or p95off is None else p95on > 1.05 * p95off,
             "AÇIK %s/%s (%d tur) · KAPALI %s/%s (%d tur) · ölçüt AÇIK p95 ≤ KAPALI p95 × 1,05" % (
                 f(pct(on["tours"], .5), 0), f(p95on, 0), len(on["tours"]), f(pct(off["tours"], .5), 0), f(p95off, 0),
                 len(off["tours"])))]
    res4.append(("418/429", on["rest"] > 0 if on["n"] else None,
                 "AÇIK %d · KAPALI %d (ölçüt: AÇIK gecelerde 0)" % (on["rest"], off["rest"])))
    res4.append(("PAPER_RESEARCH_ACTIVE", "bilgi" if (on["n"] and off["n"]) else None,
                 "AÇIK %d · KAPALI %d (raporlanır; fark beklenmez)" % (on["ra"], off["ra"])))
    res4.append(("tur hatası", None if not (on["n"] and off["n"]) else (on["err"] > off["err"]),
                 "AÇIK %d · KAPALI %d" % (on["err"], off["err"])))
    pk = max(on["peak"]) if on["peak"] else None
    res4.append(("motor memory.peak", None if pk is None else pk > 0.8,
                 "AÇIK gecelerde en çok %s × MemoryMax (ölçüt ≤ 0,8; MemoryMax %s bayt)" % (f(pk, 2), emem)))
    w = b.get("worker") or {}
    res4.append(("worker NRestarts", iv(nr) != w.get("nrestarts") if w else None,
                 "dağıtımda %s → şimdi %s (değişmemeli)" % (w.get("nrestarts"), nr)))
    res4.append(("worker MemoryMax", iv(memmax) != 6442450944, "%s (6G = 6442450944 olmalı)" % memmax))
    sm = []
    try:
        with open(samples, encoding="utf-8") as fh:
            sm = [json.loads(x) for x in fh if x.strip()]
    except (OSError, ValueError):
        pass
    kinds = {day: key for day, _st, _rr, _js, key in nights}
    agg = {"ON": {"box": [], "drej": [], "pm": []}, "OFF": {"box": [], "drej": [], "pm": []}}
    for a, z in zip(sm, sm[1:]):
        inside = [dd for dd in kinds if a["t"] < datetime.strptime(dd, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() + 97 * 60
                  and datetime.strptime(dd, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() + 220 * 60 <= z["t"]]
        if len(inside) != 1 or kinds[inside[0]] is None or z.get("worker_pid") != a.get("worker_pid"):
            continue
        k, hrs = kinds[inside[0]], (z["t"] - a["t"]) / 3600.0
        if None not in (a.get("box_missed"), z.get("box_missed")) and hrs > 0:
            agg[k]["box"].append((z["box_missed"] - a["box_missed"]) / hrs)
        if (z.get("tours") or 0) > (a.get("tours") or 0):
            agg[k]["drej"].append((z["data_rejected"] - a["data_rejected"]) / (z["tours"] - a["tours"]))
        if z.get("pm_p50") is not None:
            agg[k]["pm"].append(z["pm_p50"])
    for name, key, nd, rule in (("Box kaçan bar/saat", "box", 3, "AÇIK ≤ KAPALI"),
                                ("veri reddi/tur", "drej", 3, "AÇIK ≤ KAPALI × 1,05"),
                                ("koruyucu izleyici süre p50 (sn)", "pm", 2, "AÇIK ≤ KAPALI × 1,05")):
        a_, z_ = agg["ON"][key], agg["OFF"][key]
        ma, mz = pct(a_, .5), pct(z_, .5)
        flag = None if ma is None or mz is None else (ma > mz if key == "box" else ma > 1.05 * mz + 1e-9)
        res4.append((name, flag, "KABA (iki --check örneği arası, tek A/B gecesi + gündüz): AÇIK medyan %s (%d aralık) · "
                                 "KAPALI medyan %s (%d aralık) · ölçüt %s" % (f(ma, nd), len(a_), f(mz, nd), len(z_), rule)))
    for name, flag, txt in res4:
        print("   %-13s %-26s %s" % ({None: "[ölçülemedi]", True: "[KALDI]", False: "[GEÇTİ]", "bilgi": "[bilgi]"}[flag],
                                     name, txt))
    if any(x[1] is True for x in res4):
        print("   >>> en az bir ölçüt KALDI: bu çıktıyı bana iletin (kapatma: sudo systemctl disable --now tradingbot-engine-night.timer)")
    return 0
CMDS = {"paper": c_paper, "runner": c_runner, "ast": c_ast, "lastrun": c_lastrun, "smoke": c_smoke, "check": c_check,
        "ab": c_ab, "baseline": c_baseline, "k6": c_k6}
r = CMDS[sys.argv[1]](*sys.argv[2:])
sys.exit(r if isinstance(r, int) else 0)
PY
rpy() { env -i PATH="$PATH" LANG=C.UTF-8 TZ=UTC "$VENV/bin/python" -I -c "$PYTOOL" "$@"; }

# ------------------------------------------------------------------ klon yardımcıları (git servis kullanıcısıyla)
fetch_tip() {   # $1: klon. TIP yalnız EKLENİR (çalışma ağacı değişmez); içerik commit kimliğine bağlıdır
  local d="$1"
  if ! gitx "$d" cat-file -e "$TIP^{commit}" 2>/dev/null; then
    gitx "$d" fetch -q "$REPO_URL" "$TIP" 2>/dev/null || gitx "$d" fetch -q "$REPO_URL" "$BRANCH_REF" 2>/dev/null || true
  fi
  gitx "$d" cat-file -e "$TIP^{commit}" 2>/dev/null \
    || die "hedef commit $T7 getirilemedi ($REPO_URL): GitHub erişimini ve commit'in yayımlandığını denetleyin"
}
make_clone() {  # $1: VAR OLAN boş klasör (servis kullanıcısına ait). Yerel app deposundan klon + TIP + checkout
  gitx "$1" clone -q --no-checkout "$APP" .
  fetch_tip "$1"
  gitx "$1" -c advice.detachedHead=false checkout -q --detach "$TIP"
}
unit_contract() {   # $1: ağaç. sha sabitlemesinin üstüne ikinci savunma: §2.2–§2.5 anahtar satırları
  local s="$1/deploy/$SVC" t="$1/deploy/$TMR"
  grep -qx 'OnCalendar=\*-\*-\* 01:37:00 UTC' "$t" && grep -qx 'Persistent=false' "$t" \
    && grep -qx 'PrivateNetwork=yes' "$s" && grep -qx 'PrivateTmp=yes' "$s" && grep -qx 'ProtectSystem=strict' "$s" \
    && grep -qx 'ReadWritePaths=/opt/tradingbot/data/research' "$s" && ! grep -q '^EnvironmentFile' "$s" \
    && grep -qx 'WorkingDirectory=/opt/tradingbot/engine-app' "$s" && grep -qx 'MemoryMax=512M' "$s" \
    && grep -qx "Environment=ENGINE_EXPECTED_MEMORY_MAX=$ENGINE_MEM" "$s" && grep -qx 'User=tradingbot' "$s" \
    && grep -qx 'ExecStart=/opt/tradingbot/venv/bin/python -s -m tradingbot engine-night' "$s"
}
dropin_text() {     # uyarı birimi VARSA OnFailure; nproc < 4 ise CPUQuota=60% (§2.5; birim dosyasının yorumu)
  local t=""
  if systemctl cat tradingbot-alert@.service >/dev/null 2>&1; then t+=$'[Unit]\nOnFailure=tradingbot-alert@%n.service\n'; fi
  if (( $(nproc) < 4 )); then t+=$'[Service]\nCPUQuota=60%\n'; fi
  if [[ -n "$t" ]]; then printf '# tb-engine-%s: §2.5 (uyarı birimi / nproc < 4)\n%s' "$T7" "$t"; fi
}
undo_units() {      # motor birimlerini kaldır (zamanlayıcı reload'suz kapatılır); reload yalnız kapıdan
  systemctl stop "$TMR" >/dev/null 2>&1 || true
  systemctl disable --no-reload "$TMR" >/dev/null 2>&1 || true
  if [[ "$(systemctl is-active "$SVC" 2>/dev/null || true)" =~ ^(active|activating)$ ]]; then systemctl stop "$SVC" || true; fi
  rm -f -- "$SD/$SVC" "$SD/$TMR" "$DROPIN"
  if [[ -d "$DROPIN_DIR" ]]; then rmdir -- "$DROPIN_DIR" 2>/dev/null || true; fi
  if reload_gate; then systemctl daemon-reload; ok "daemon-reload (kapıdan geçti)"
  else warn "daemon-reload ATLANDI: $WHY — birim dosyaları silindi, zamanlayıcı durdu ve kapalı; motor çalışmaz"; fi
  systemctl reset-failed "$SVC" >/dev/null 2>&1 || true
}
worker_now() { printf '%s %s' "$(sc_show "$WORKER" MainPID)" "$(sc_show "$WORKER" NRestarts)"; }

# ================================================================== --check (salt-okunur; yalnız örnek eklenir)
if [[ "$MODE" == --check ]]; then
  say "GECE ÖĞRENME MOTORU — tb-engine-$T7 --check (salt-okunur; yalnız $SAMPLES'a ölçüm örneği eklenir)"
  [[ -d "$ENG/.git" ]] || die "motor kurulu değil ($ENG yok). Dağıtım: sudo bash $0"
  eng_sha="$(gitx "$ENG" rev-parse HEAD)"; app_sha="$(gitx "$APP" rev-parse HEAD)"
  if gitx "$ENG" merge-base --is-ancestor "$app_sha" "$eng_sha" 2>/dev/null; then rel="app ⊑ engine-app (SKEW yok)"
  else rel="SKEW — app sürümü engine-app'te yok: engine-app yeniden sabitlenmeli (o geceler yalnız S0/S1a/S7)"; fi
  echo "   iki SHA: app ${app_sha:0:7} · engine-app ${eng_sha:0:7} (bu betiğin hedefi $T7) → $rel"
  echo "   zamanlayıcı: $(systemctl is-enabled "$TMR" 2>/dev/null || true) / $(systemctl is-active "$TMR" 2>/dev/null || true)" \
       "· sonraki: $(sc_show "$TMR" NextElapseUSecRealtime) · son servis sonucu: $(sc_show "$SVC" Result)" \
       "(çıkış $(sc_show "$SVC" ExecMainStatus); 226 = NAMESPACE)"
  echo "   worker: MemoryMax $(sc_show "$WORKER" MemoryMax) · NeedDaemonReload $(sc_show "$WORKER" NeedDaemonReload)" \
       "· NRestarts $(sc_show "$WORKER" NRestarts) — betik worker'a dokunmaz"
  say "engine-status --brief (≤ 60 satır; GECE ÖĞRENME MOTORU › GÜNLÜK HEDEF)"
  svc "$ENG" TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" "$VENV/bin/python" -s -m tradingbot engine-status --brief \
    || echo "   (engine-status hata verdi)"
  say "kabul ölçütleri"
  rpy check "$RES" "$STATE" "$TIP" "$BASELINE" "$SAMPLES" "$(sc_show "$WORKER" MemoryMax)" "$(sc_show "$WORKER" NRestarts)" \
    "$(sc_show "$WORKER" MainPID)" || echo "   (ölçüt raporu hata verdi)"
  TMP="$(mktemp -d /tmp/tb-engine-k6.XXXXXX)"; chown "$SVC_USER:$SVC_GROUP" "$TMP"   # K6 (salt-okunur; geçici, silinir)
  svc "$ENG" TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" "${LOW[@]}" "$VENV/bin/python" -s -m tradingbot \
    engine-status --daily --json --days 10 > "$TMP/e.json" 2>/dev/null || true
  svc "$ENG" "${LOW[@]}" "$VENV/bin/python" -s scripts/bot_scorecard.py --state "$STATE" --daily --days 10 --out "$TMP/s.json" \
    >/dev/null 2>&1 || true
  rpy k6 "$TMP/e.json" "$TMP/s.json" || echo "   (K6 hata verdi)"
  echo "   K4: 14. geceden sonra  sudo bash $0 --ab-report · tablo: engine-status --daily ve bot_scorecard.py --daily"
  echo "   kapatma: sudo systemctl disable --now $TMR (önce NeedDaemonReload=no); tam geri alma: sudo bash $0 --rollback"
  exit 0
fi
if [[ "$MODE" == --ab-report ]]; then
  say "GECE ÖĞRENME MOTORU — tb-engine-$T7 --ab-report (salt-okunur)"
  rpy ab "$RES" "$TIP" "$BASELINE" "$SAMPLES" "$(sc_show "$WORKER" MemoryMax)" "$(sc_show "$WORKER" NRestarts)" "$ENGINE_MEM"
  exit 0
fi

# ================================================================== dağıtım / kuru çalışma / geri alma: tek örnek + kayıt
[[ -d "$LOGDIR" ]] || install -d -m 0750 "$LOGDIR"
exec 9>"$LOGDIR/.tb-engine.lock"
flock -n 9 || die "başka bir tb-engine betiği çalışıyor ($LOGDIR/.tb-engine.lock)"
LOG="$LOGDIR/engine-$T7-${MODE#--}-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1
echo "tb-engine-$T7 $MODE · $(date -u '+%F %T') UTC · kayıt: $LOG"
read -r W_PID0 W_NR0 <<<"$(worker_now)"

if [[ "$MODE" == --rollback ]]; then
  say "GERİ ALMA — zamanlayıcı + servis kaldırılır, engine-app silinir; data/research KALIR; worker'a DOKUNULMAZ"
  if [[ -f "$SD/$SVC" || -f "$SD/$TMR" ]]; then
    keep="$LOGDIR/engine-$T7-rollback-units-$(date -u +%Y%m%dT%H%M%SZ)"; install -d -m 0750 "$keep"
    cp -p -- "$SD/$SVC" "$SD/$TMR" "$keep/" 2>/dev/null || true; ok "birim dosyalarının kopyası: $keep"
  fi
  undo_units
  if [[ -e "$ENG" || -L "$ENG" ]]; then
    [[ "$ENG" == "$BASE/engine-app" && -d "$ENG/.git" && ! -L "$ENG" ]] || die "$ENG beklenen klon değil; elle bakın"
    rm -rf -- "$ENG"; ok "engine-app klonu silindi ($ENG)"
  fi
  [[ ! -e "$SD/$SVC" && ! -e "$SD/$TMR" ]] || die "birim dosyaları hâlâ duruyor"
  echo "   zamanlayıcı: $(systemctl is-active "$TMR" 2>/dev/null || true) · data/research: $(du -sh "$RES" 2>/dev/null | cut -f1 || echo yok) (DOKUNULMADI)"
  read -r W_PID1 W_NR1 <<<"$(worker_now)"
  if [[ "$W_PID1 $W_NR1" == "$W_PID0 $W_NR0" ]]; then ok "worker dokunulmadı (PID $W_PID1, NRestarts $W_NR1)"
  else warn "worker PID/NRestarts değişti ($W_PID0/$W_NR0 → $W_PID1/$W_NR1) — betik worker'a dokunmaz; nedeni günlükte"; fi
  echo "GERİ ALINDI. Yeniden kurmak için: sudo bash $0"
  exit 0
fi

# ------------------------------------------------------------------ ön denetimler (dağıtımda ilk KALDI → hiçbir şey değişmez)
say "1/9 sunucu (salt-okunur)"
ENG_NOW=""; if [[ -d "$ENG/.git" ]]; then ENG_NOW="$(gitx "$ENG" rev-parse HEAD 2>/dev/null || echo ?)"; fi
if [[ "$MODE" == deploy && "$ENG_NOW" == "$TIP" && -f "$SD/$SVC" && "$(systemctl is-enabled "$TMR" 2>/dev/null || true)" == enabled ]] \
   && [[ "$(sha256sum < "$SD/$SVC" | cut -d' ' -f1)" == "$SVC_SHA256" ]]; then
  echo "   zaten dağıtılmış ($T7; zamanlayıcı açık). Durum: sudo bash $0 --check"; exit 0
fi
SLOTS="Uygun aralıklar (UTC): 04:40–07:40, 08:40–11:40, 12:40–15:40, 16:40–19:40, 20:40–23:40"
if time_ok 20; then ok "saat uygun ($WHY)"
elif [[ "$MODE" == deploy ]]; then die "şimdi dağıtılmaz: $WHY. $SLOTS. HİÇBİR ŞEYE DOKUNULMADI"
elif time_ok 5; then warn "dağıtım şu an BAŞLAMAZ: $WHY (kuru çalışma sürer, düşük öncelikle)"
else die "kuru çalışma da şimdi yapılmaz: $WHY (derleme ve koşucu worker turlarıyla çekişmesin; §2.9). $SLOTS. HİÇBİR ŞEYE DOKUNULMADI"; fi
id -u "$SVC_USER" >/dev/null 2>&1 && [[ -x "$VENV/bin/python" ]] && rc=0 || rc=1
gate "servis-kullanıcısı" "$rc" "$SVC_USER var, $VENV/bin/python çalıştırılabilir"
out="$(rpy paper "$STATE" 2>&1)" && rc=0 || rc=1
gate "PAPER" "$rc" "$out"
for u in "$WORKER" "$DASH"; do
  v="$(sc_show "$u" NeedDaemonReload)"; [[ "$v" == no ]] && rc=0 || rc=1
  gate "${u%%.service}-NDR=no" "$rc" "$u NeedDaemonReload=${v:-?} (yes ise DURUN, bana iletin)"
done
mm="$(sc_show "$WORKER" MemoryMax)"; [[ "$mm" == "$MEM_EXPECT" ]] && rc=0 || rc=1
gate "worker-MemoryMax=6G" "$rc" "MemoryMax=${mm:-?} (beklenen $MEM_EXPECT)"
st="$(systemctl is-active "$SVC" 2>/dev/null || true)"; [[ ! "$st" =~ ^(active|activating)$ ]] && rc=0 || rc=1
gate "motor-boşta" "$rc" "$SVC ${st:-?} (gece çalıştırması sürerken dağıtılmaz)"
free_b="$(df -B1 --output=avail "$BASE" | tail -n 1 | tr -d ' ')"; res_b="$(du -sb "$RES" 2>/dev/null | cut -f1 || echo 0)"
(( free_b >= 10000000000 && ${res_b:-0} <= 20000000000 )) && rc=0 || rc=1
gate "disk" "$rc" "boş $(numfmt --to=si "$free_b") (≥ 10G) · data/research $(numfmt --to=si "${res_b:-0}") (≤ 20G)"
last=0; lastf=""
for fm in "$LOGDIR"/*-restart-at.txt; do
  [[ -f "$fm" ]] || continue
  e="$(sed -n 1p "$fm" 2>/dev/null || true)"
  if [[ "$e" =~ ^[0-9]+$ ]] && (( e > last )); then last="$e"; lastf="$fm"; fi
done
age=$(( $(date +%s) - last )); (( age >= 3 * 86400 )) && rc=0 || rc=1   # sahip kararı 2026-10-06 "3 temiz gün sonra" (önceden 7)
if [[ -n "$lastf" ]]; then out="son sürüm yeniden başlatması $(basename "$lastf") $((age / 86400)) gün önce (≥ 3; §2.9; ilk iki --check temiz olmalı)"
  (( rc == 0 )) || out+="; en erken $(date -u -d "@$((last + 3 * 86400))" '+%F %H:%M') UTC"
else out="deploy-logs'ta sürüm kaydı yok"; fi   # o sürümün ilk iki --check'i temiz mi: sahip + inceleyici yargısı
gate "3g-pencere-dışı" "$rc" "$out"
ram_kb="$(awk '/^MemTotal:/{print $2}' /proc/meminfo)"; dm="$(sc_show "$DASH" MemoryMax)"; [[ "$dm" =~ ^[0-9]+$ ]] || dm=536870912
need=$(( MEM_EXPECT + dm + ENGINE_MEM + 1073741824 ))
if (( ram_kb * 1024 >= need )); then ok "bellek bütçesi: worker 6G + panel $(numfmt --to=iec "$dm") + motor 512M + 1G ≤ RAM $(numfmt --to=iec $((ram_kb * 1024)))"
else warn "bellek bütçesi aşılıyor (RAM $(numfmt --to=iec $((ram_kb * 1024)))): OOMScoreAdjust=1000 motoru önce öldürür; P0 kararı gerekir"; fi
tst="$(systemctl is-enabled "$TMR" 2>/dev/null || true)"; rst="yok"; if [[ -d "$RES" ]]; then rst="$(stat -c '%U %a' "$RES")"; fi
echo "   mevcut kurulum: engine-app ${ENG_NOW:-yok} · birim dosyası $([[ -f "$SD/$SVC" ]] && echo var || echo yok)" \
     "· zamanlayıcı ${tst:-yok} · data/research $rst"
APP_SHA="$(gitx "$APP" rev-parse HEAD 2>/dev/null)" || die "çalışan app SHA'sı okunamadı ($APP, $SVC_USER ile git)"
CFG_SHA0="$(sha256sum < "$APP/config.yaml" 2>/dev/null | cut -d' ' -f1)"

say "2/9 hedef kod $T7 GEÇİCİ klonda (çalışan hiçbir şey değişmez)"
TMP="$(mktemp -d /tmp/tb-engine-"$T7".XXXXXX)"; install -d -o "$SVC_USER" -g "$SVC_GROUP" -m 0750 "$TMP/engine-app" "$TMP/data"
chown "$SVC_USER:$SVC_GROUP" "$TMP"
make_clone "$TMP/engine-app"; W="$TMP/engine-app"
[[ "$(gitx "$W" rev-parse HEAD)" == "$TIP" ]] && rc=0 || rc=1
inv "kaynak-TIP" "$rc" "geçici klon HEAD = $T7 ($REPO_URL)"
gitx "$W" merge-base --is-ancestor "$APP_SHA" "$TIP" && rc=0 || rc=1
gate "app-SHA-ata" "$rc" "çalışan app ${APP_SHA:0:7} hedefin atası (değilse her gece SKEW)"
[[ "$(sha256sum < "$W/deploy/$SVC" | cut -d' ' -f1)" == "$SVC_SHA256" && "$(sha256sum < "$W/deploy/$TMR" | cut -d' ' -f1)" == "$TMR_SHA256" ]] && rc=0 || rc=1
inv "birim-sha256" "$rc" "service ${SVC_SHA256:0:12}… · timer ${TMR_SHA256:0:12}…"
unit_contract "$W" && rc=0 || rc=1
inv "birim-sözleşmesi" "$rc" "01:37 UTC, PrivateNetwork, PrivateTmp, ReadWritePaths=data/research, 512M = ENGINE_EXPECTED_MEMORY_MAX, -s -m"
install -d -m 0755 "$TMP/units"
cp -- "$W/deploy/$TMR" "$TMP/units/$TMR"       # verify yorumlayıcının VARLIĞINI da denetler (sahte kökte: $VENV)
sed "s#^ExecStart=/opt/tradingbot/venv/bin/python #ExecStart=\"$VENV/bin/python\" #" "$W/deploy/$SVC" > "$TMP/units/$SVC"
if command -v systemd-analyze >/dev/null 2>&1; then
  out="$(systemd-analyze verify "$TMP/units/$SVC" "$TMP/units/$TMR" 2>&1)" && rc=0 || rc=1
  out="$(grep "$UNIT" <<<"$out" | head -n 3 || true)"
else rc=1; out="systemd-analyze yok"; fi
inv "systemd-analyze-verify" "$rc" "${out:-temiz}"
out="$(svc "$W" "${LOW[@]}" "$VENV/bin/python" -s -m compileall -q tradingbot scripts 2>&1 | tail -n 3)" && rc=0 || rc=1
inv "compileall" "$rc" "${out:-tradingbot/ + scripts/ önceden derlendi}"

say "3/9 bağımsız değişmezler (pytest YOK; ayrı süreç, servis ortamı aktarılmadan, ağ kapalı)"
svc "$W" "${LOW[@]}" "$VENV/bin/python" -s tests/standalone/run_engine_invariants.py --tree "$W" --forbid-pytest --json "$TMP/inv.json" \
  | sed 's/^/   /' || true
out="$(rpy runner "$TMP/inv.json" "$INV_TESTS" 2>&1)" && rc=0 || rc=1
inv "bağımsız-koşucu" "$rc" "$out (beklenen $INV_TESTS/$INV_TESTS)"
out="$(rpy ast "$W" 2>&1)" && rc=0 || rc=1
if o2="$(svc "$W" "${LOW[@]}" "$VENV/bin/python" -s -c 'import resource as r, sys, tradingbot.cli
print("cli import RSS %d MB" % (r.getrusage(r.RUSAGE_SELF).ru_maxrss // 1024))
sys.exit(any(m.startswith("tradingbot.research_engine") for m in sys.modules))' 2>&1)"; then out="$out; $o2"
else rc=1; out="$out; tradingbot.cli import'u motoru yükledi ya da düştü: ${o2:0:200}"; fi
inv "yalıtım-AST" "$rc" "$out (config_v3/load_config/sqlite3 yok; cli tembel)"
out="$(svc "$W" TRADINGBOT_DATA="$TMP/data" "$VENV/bin/python" -s -m tradingbot engine-status --brief 2>&1)" && rc=0 || rc=1
n_out="$(wc -l <<<"$out")"; leaked="$(find "$TMP/data" -mindepth 1 | head -n 1)"
[[ "$rc" == 0 && "$n_out" -le 60 && -z "$leaked" ]] && rc=0 || rc=1
inv "engine-status-kuru" "$rc" "boş veri kökünde çıkış 0, $n_out satır (≤ 60), config yüklenmedi, hiçbir şey yazılmadı"

say "4/9 betik"
if command -v shellcheck >/dev/null 2>&1; then
  if shellcheck -S warning "$0" >/dev/null 2>&1; then ok "shellcheck temiz"; else warn "shellcheck bulgusu (shellcheck -S warning $0)"; fi
else echo "   shellcheck yok (VPS'te kurulu değil; depoda sandbox testi denetler)"; fi
if (( ${#INV_FAIL[@]} )); then
  [[ "$MODE" == deploy ]] && die "${#INV_FAIL[@]} değişmez KALDI: ${INV_FAIL[*]}. HİÇBİR ŞEYE DOKUNULMADI"
  echo; echo "KURU ÇALIŞMA: $((INV_N - ${#INV_FAIL[@]}))/$INV_N değişmez geçti — KALAN: ${INV_FAIL[*]}. HİÇBİR ŞEY DEĞİŞMEDİ."; exit 1
fi
if [[ "$MODE" == --dry-run ]]; then
  echo; echo "KURU ÇALIŞMA: $INV_N/$INV_N değişmez geçti. HİÇBİR ŞEY DEĞİŞMEDİ. Dağıtım: sudo bash $0  (UTC uygun aralıkta)"; exit 0
fi

# ================================================================== DAĞITIM (yalnız motor; worker çalışmaya devam eder)
say "5/9 engine-app ayrı klonu $T7'e sabitlenir + compileall"
if [[ -d "$ENG/.git" ]]; then
  [[ "$(stat -c %U "$ENG")" == "$SVC_USER" ]] || die "$ENG $SVC_USER'a ait değil"
  [[ -z "$(gitx "$ENG" status --porcelain)" ]] || die "$ENG içinde yerel değişiklik var"
  fetch_tip "$ENG"; gitx "$ENG" -c advice.detachedHead=false checkout -q --detach "$TIP"
else
  [[ ! -e "$ENG" ]] || die "$ENG var ama git klonu değil"
  install -d -o "$SVC_USER" -g "$SVC_GROUP" -m 0750 "$ENG"; make_clone "$ENG"
fi
CHANGED+=("engine-app=$T7")
svc "$ENG" "${LOW[@]}" "$VENV/bin/python" -s -m compileall -q tradingbot scripts >/dev/null
[[ "$(gitx "$ENG" rev-parse HEAD)" == "$TIP" && -z "$(gitx "$ENG" status --porcelain)" ]] && rc=0 || rc=1
inv "engine-app-sabit" "$rc" "$ENG HEAD = $T7, temiz, önceden derlendi"
(( rc == 0 )) || die "engine-app sabitlenemedi"

say "6/9 data/research (İLK başlatmadan ÖNCE; yoksa 226/NAMESPACE)"
install -d -o "$SVC_USER" -g "$SVC_GROUP" -m 0750 "$RES" "$RES/locks" "$RES/backup"
CHANGED+=("data/research")
foreign="$(find "$RES" ! -user "$SVC_USER" -print -quit)"
[[ "$(stat -c '%U %a' "$RES")" == "$SVC_USER 750" && -z "$foreign" ]] && rc=0 || rc=1
inv "araştırma-kökü" "$rc" "$RES ($SVC_USER, 0750; locks/ backup/)${foreign:+ — YABANCI SAHİPLİ: $foreign}"
(( rc == 0 )) || die "data/research sahipliği uygun değil: sudo chown -R $SVC_USER:$SVC_GROUP $RES (sonra yeniden çalıştırın)"

say "7/9 birimler (install -m0644) + KAPILI daemon-reload"
for u in "$SVC" "$TMR"; do
  if [[ -f "$SD/$u" ]] && ! cmp -s "$ENG/deploy/$u" "$SD/$u"; then cp -p -- "$SD/$u" "$LOGDIR/engine-$T7-prev-$u"; fi
  install -m 0644 "$ENG/deploy/$u" "$SD/$u"
done
CHANGED+=("birim dosyaları")
dt="$(dropin_text)"
if [[ -n "$dt" ]]; then
  install -d -m 0755 "$DROPIN_DIR"; printf '%s' "$dt" > "$TMP/dropin.conf"; install -m 0644 "$TMP/dropin.conf" "$DROPIN"
  ok "drop-in $DROPIN:"; sed 's/^/        /' "$DROPIN"
else rm -f -- "$DROPIN"; ok "drop-in yok (uyarı birimi yok, nproc ≥ 4)"; fi
reload_gate && rc=0 || rc=1
inv "reload-kapısı" "$rc" "${WHY:-worker+panel NeedDaemonReload=no, worker 6G}"
if (( rc )); then undo_units; die "daemon-reload güvenli değil: $WHY. Motor birimleri geri alındı. Bana iletin"; fi
systemctl daemon-reload
mm="$(sc_show "$WORKER" MemoryMax)"; [[ "$mm" == "$MEM_EXPECT" ]] && rc=0 || rc=1
inv "MemoryMax-reload-sonrası" "$rc" "worker MemoryMax=${mm:-?}"
if (( rc )); then
  undo_units
  die "reload'dan sonra worker MemoryMax=$mm (6G değil). Motor birimleri GERİ ALINDI. Worker'ı yeniden başlatmadan 6G'ye döndürmek için:
  sudo systemctl set-property $WORKER MemoryMax=6G   — ve bana iletin" 3
fi
[[ "$(sc_show "$SVC" MemoryMax)" == "$ENGINE_MEM" && "$(sc_show "$SVC" NeedDaemonReload)" == no && "$(sc_show "$TMR" LoadState)" == loaded ]] && rc=0 || rc=1
inv "birim-yüklendi" "$rc" "$SVC MemoryMax=$(sc_show "$SVC" MemoryMax) · $TMR $(sc_show "$TMR" LoadState)"
(( rc == 0 )) || die "motor birimi beklendiği gibi yüklenmedi (zamanlayıcı açılmadı); sudo bash $0 --rollback"

say "8/9 ELLE SMOKE: systemctl start $SVC (geçmezse zamanlayıcı AÇILMAZ)"
time_ok 5 || die "smoke şimdi çalıştırılmaz: $WHY. Birimler kurulu, zamanlayıcı KAPALI; uygun saatte yeniden çalıştırın: sudo bash $0" 2
PRE_RID="$(rpy lastrun "$RES")"; T0="$(date +%s)"
src=0; timeout "$SMOKE_TIMEOUT" systemctl start "$SVC" || src=$?
if (( src == 124 )); then systemctl stop "$SVC" || true; fi
ems="$(sc_show "$SVC" ExecMainStatus)"; sres="$(sc_show "$SVC" Result)"
echo "   smoke (start) çıkışı $src · Result=$sres · ExecMainStatus=$ems"
out=0; rpy smoke "$RES" "$TIP" "$PRE_RID" "$T0" "$SVC_USER" || out=1
[[ "$src" == 0 && "$sres" == success && "$ems" == 0 && "$out" == 0 ]] && rc=0 || rc=1
inv "smoke" "$rc" "çıkış 0, öz-denetim OK, SUCCESS, beklenen dosyalar, 226/NAMESPACE yok, sahiplik $SVC_USER"
if (( rc )); then
  [[ "$ems" == 226 ]] && echo "   226/NAMESPACE: ReadWritePaths/ReadOnlyPaths bağlanamadı (data/research ya da data yok?)"
  journalctl -u "$SVC" -n 40 --no-pager 2>/dev/null | sed 's/^/   | /' || true
  if [[ "$(systemctl is-enabled "$TMR" 2>/dev/null || true)" == enabled ]]; then
    systemctl stop "$TMR" || true; systemctl disable --no-reload "$TMR" || true; echo "   önceki zamanlayıcı KAPATILDI"
  fi
  die "smoke GEÇMEDİ → zamanlayıcı ETKİNLEŞTİRİLMEDİ (birimler kurulu, data/research kaldı). Çıktıyı bana iletin;
  geri almak için: sudo bash $0 --rollback" 2
fi

say "9/9 zamanlayıcı (enable --now; örtük reload KAPIDAN)"
reload_gate || { undo_units; die "enable öncesi reload kapısı: $WHY. Motor birimleri geri alındı" 3; }
systemctl enable --now "$TMR"
CHANGED+=("zamanlayıcı")
mm="$(sc_show "$WORKER" MemoryMax)"
if [[ "$mm" != "$MEM_EXPECT" ]]; then
  undo_units; die "enable'dan sonra worker MemoryMax=$mm. Motor birimleri GERİ ALINDI; 6G için: sudo systemctl set-property $WORKER MemoryMax=6G — bana iletin" 3
fi
[[ "$(systemctl is-enabled "$TMR" 2>/dev/null || true)" == enabled && "$(systemctl is-active "$TMR" 2>/dev/null || true)" == active ]] && rc=0 || rc=1
inv "zamanlayıcı" "$rc" "enabled + active · sonraki $(sc_show "$TMR" NextElapseUSecRealtime) · worker MemoryMax 6G"
read -r W_PID1 W_NR1 <<<"$(worker_now)"
[[ "$W_PID1 $W_NR1" == "$W_PID0 $W_NR0" && "$(gitx "$APP" rev-parse HEAD)" == "$APP_SHA" \
   && "$(sha256sum < "$APP/config.yaml" 2>/dev/null | cut -d' ' -f1)" == "$CFG_SHA0" ]] && rc=0 || rc=1
inv "worker-dokunulmadı" "$rc" "worker PID $W_PID0→$W_PID1, NRestarts $W_NR0→$W_NR1; app HEAD ve config.yaml baytları aynı"
rpy baseline "$BASELINE" "$TIP" "$APP_SHA" "$W_NR1" "$W_PID1" "$(sc_show "$WORKER" MemoryMax)" "$(rpy lastrun "$RES")" "$STATE" "$SAMPLES" \
  || warn "taban kaydı yazılamadı ($BASELINE)"
echo
if (( ${#INV_FAIL[@]} )); then
  echo "DAĞITILDI ama $((${#INV_FAIL[@]})) değişmez KALDI: ${INV_FAIL[*]} — bu çıktıyı bana iletin."; exit 4
fi
echo "DAĞITILDI: $INV_N/$INV_N değişmez geçti. Motor $T7 her gece 01:37 UTC'de çalışır (ağsız, kayıt-yalnız)."
echo "Sonraki: 14 gün her gün  sudo bash $0 --check  · 14. geceden sonra  sudo bash $0 --ab-report"
echo "Kapatma: sudo systemctl disable --now $TMR (önce NeedDaemonReload=no) · tam geri alma: sudo bash $0 --rollback"
