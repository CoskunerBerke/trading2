#!/usr/bin/env bash
# trading2 — VPS dağıtımı (PAPER; gerçek para YOK). Hedef: TIP değişkeni (c0b8c94, dal claude/gifted-knuth-0ehpcs).
#
# BU SÜRÜM: ÖĞRENME MODU (L1) — YALNIZ PAPER.
#   Amaç: bütün botlar (ana bot, T2, M2, Box, D4, C4, Formasyon) mümkün olan en çok işleme girsin ve ortak öğrenme
#   katmanı R deneyimi toplasın. Yalnız ÖLÇÜMÜ BOZMAYAN sınırlar gevşer:
#     * toplam açık risk %6 → 100 (defter başına ayrı öğrenme risk profili; PROFILES sabitleri DEĞİŞMEZ);
#     * adet/marj darboğazı yerine defter başına eşzamanlı slot K (Σ marj ≤ %95 özkaynak, likidasyon ≥ 2 × stop);
#     * Box en küçük stop %2,22 → %0,32; ana botta rejim/mum/yapı kapılarının GİRİŞ kararı gölgede (kayıt yazılır);
#     * ekonomi kapısının reddettiği adaylar "keşif" işlemi olarak açılır (etiketli);
#     * açılamayan geçerli sinyaller "olsaydı" (karşı-olgusal) kaydı olarak etiketlenir — P&L'e GİRMEZ.
#   Veri kimliği, R geometrisi, çift sayım korumaları, 60 dk giriş pencereleri, sembol başına tek pozisyon, borsa
#   filtreleri ve kill switch AYNEN kalır. Yalnız mode=PAPER + gateway=paper + testnet kapalı + risk profili
#   PAPER_RESEARCH iken açılır (aksi ConfigError, worker başlamaz). Çalışma anında mod kapısı düşerse "ÖĞRENME MODU
#   ASKIDA" olur ve bütün botlar bugünkü (taban) kurallarla sürer.
#   BOYUT: işlem başına hedef risk %2 → %0,5 → işlem başına USDT yaklaşık 4 KAT küçülür. R (kâr/zarar ÷ ilk stop riski)
#   ölçekten bağımsızdır ve DEĞİŞMEZ: karşılaştırmalar R ile yapılır; öğrenme dönemindeki USDT eskisiyle kıyaslanmaz.
#   Öğrenmenin ilk aktif anı state/learning_mode.json'da tutulur; panel ve karne öncesi/sonrası ayrı gösterir.
#
# KAPATMAK (eski koda dönmeden): /opt/tradingbot/app/config.yaml içinde `learning_mode:` altında `enabled: false`, sonra
#   sudo systemctl restart tradingbot-worker tradingbot-dashboard
#   Açık pozisyonlar kendi stop/hedef/kaldıracıyla kapanır (zorla kapanış yok); toplam açık risk %6'nın altına inene
#   kadar TOTAL_OPEN_RISK yeni girişleri durdurabilir — beklenen davranış. (config.yaml düzenlemesi bir sonraki
#   dağıtımda "yerel değişiklik" olarak görünür; o zaman bana haber verin.)
#
# Kod kaynağı: herkese açık GitHub deposu (bu sürüm için bundle YOK; betiğin yanında bundle varsa durur)
# (commit TAM SHA ile istenir ve doğrulanır — içerik commit kimliğine bağlıdır). VPS'te:
#   sudo bash tb-deploy-c0b8c94.sh --dry-run   # kontroller + yeni kodun AYRI kopyada sınanması (preflight, değişmezler);
#                                              # çalışan kod/state/servis DEĞİŞMEZ
#   sudo bash tb-deploy-c0b8c94.sh             # dağıt (bu terminalde)
#   sudo bash tb-deploy-c0b8c94.sh --detach    # dağıt, SSH'tan BAĞIMSIZ ayrı systemd görevi olarak; çıktıyı izler.
#                                              # Pencere/PC kapansa da sürer. Ctrl+C yalnız İZLEMEYİ bırakır.
#   sudo bash tb-deploy-c0b8c94.sh --check     # dağıtım sonrası durum + geri alma tetikleri (state'e dokunmaz; yalnız
#                                              # deploy-logs'a Box ölçüm örneği ekler; tekrar tekrar koşulabilir)
#
# Sıra: kaynak → HEAD + temiz ağaç → hedef commit'in getirilmesi → ileri sarma + çalışan sürüm (PREREQ'lerden biri) →
#       disk, bellek sınırı, ortam (TRADINGBOT_LEARNING_MODE yok), state/mode.json PAPER → yeni kod AYRI kopyada:
#       preflight + config değişmezleri (servis ortamıyla) → [dry-run burada biter] → doğrulanmış yedek → worker'ın
#       turlar arası beklemesi → ortamın son denetimi → hızlı ileri sarma (ff-only) → yeniden başlatma → 60 sn kararlılık.
#       Yedek alınamazsa git'e ve servislere DOKUNULMAZ. Preflight ya da değişmezler düşerse çalışan kod hiç değişmez.
#       Servis kararlı kalkmazsa geri alınır: worker durur → Formasyon öğrenme planları temizlenir (yeni kod yerindeyken)
#       → eski commit → worker başlar. Yedeklere ve defterlere hiçbir adımda dokunulmaz.
set -Eeuo pipefail

TIP="c0b8c941be50b4f421174b575820f9395472f956"
BUNDLE_SHA256=""                          # bu sürüm için bundle yok (GitHub kaynağı)
BUNDLE_REF="refs/heads/claude/gifted-knuth-0ehpcs"
# VPS'te çalışıyor olabilecek sürümler (en eski önce): a8fe2a5 (2026-09-27 dağıtımı) ya da 7ad8832 (betiği hazırdı,
# çalıştırılmamış olabilir). Çalışan HEAD bunlardan birinin kendisi ya da soyundan olmalı (ve hedefin atası).
PREREQ=("a8fe2a5b329891578ff9a7788a3f38950473b430" "7ad8832e96945d472deacf1ba694521cbf2b96c1")
OOM_FIX="d8b0c8c"                         # bellek (OOM) onarımı; bundan eskiye dönmek OOM'u geri getirir
REPO_URL="https://github.com/CoskunerBerke/trading2.git"   # herkese açık depo (bundle yoksa)

BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="$BASE/app"; VENV="$BASE/venv"; DATA="$BASE/data"; STATE="$DATA/state"
SVC_USER="${TRADINGBOT_USER:-tradingbot}"
WORKER="tradingbot-worker.service"; DASH="tradingbot-dashboard.service"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_SRC="$HERE/tb-${TIP:0:7}.bundle"
LOGDIR="$BASE/deploy-logs"
T7="${TIP:0:7}"
MARK_RESTART="$LOGDIR/$T7-restart-at.txt"      # yeniden başlatma anı (epoch + yerel saat) — --check pencereleri
BOX_SAMPLES="$LOGDIR/$T7-box-samples.txt"      # --check Box ölçüm örnekleri (saatlik kaçan bar oranı için)
MEM_EXPECT=6442450944                          # worker MemoryMax beklenen 6G (bayt)
MEM_OLD=4294967296                             # depodaki eski unit değeri 4G (bayt)
MODE="${1:-deploy}"

say() { printf '\n== %s\n' "$*"; }
ok()  { printf '   OK  %s\n' "$*"; }
die() { printf '\nDUR: %s\n' "$*" >&2; exit 1; }
as_svc() { sudo -u "$SVC_USER" "$@"; }
gitc() { as_svc git -C "$APP" "$@"; }
shorts() { local s="" p; for p in "$@"; do s+="${s:+ / }${p:0:7}"; done; printf '%s' "$s"; }

# Servisin ortamı: unit'in Environment= satırları + EnvironmentFile. Değerler KOMUT SATIRINA KONMAZ: sudo tam komut
# satırını sistem log'una yazar (2026-09-25 VPS'te görüldü; o gün değerler boştu). Geçici dosyaya (0600, servis
# kullanıcısı) yazılır, alt süreç oradan okur; çıkışta silinir.
ENV_ARGS=()
ENVFILE=""
load_env() {
  local e line
  for e in $(systemctl show "$WORKER" -p Environment --value 2>/dev/null); do ENV_ARGS+=("$e"); done
  if [[ -r "$BASE/env" ]]; then
    local k v
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
      k="${line%%=*}"; v="${line#*=}"
      if [[ ${#v} -ge 2 && ( ( "${v:0:1}" == '"' && "${v: -1}" == '"' ) || ( "${v:0:1}" == "'" && "${v: -1}" == "'" ) ) ]]; then
        v="${v:1:${#v}-2}"                # systemd gibi: dış tırnaklar değerin parçası değil
      fi
      ENV_ARGS+=("$k=$v")
    done < "$BASE/env"
  fi
  ENV_ARGS+=("TRADINGBOT_BASE=$BASE" "ALLOW_LIVE_TRADING=false" "MPLCONFIGDIR=$BASE/.cache-deploy-mpl")
  ENVFILE="$(mktemp /tmp/tb-deploy-env.XXXXXX)"
  chmod 600 "$ENVFILE"; printf '%s\n' "${ENV_ARGS[@]}" > "$ENVFILE"; chown "$SVC_USER" "$ENVFILE"
}
trap 'rm -f "$ENVFILE"' EXIT
# svc_run DİZİN KOMUT...: servis kullanıcısı + servis ortamı, verilen dizinde (değerler dosyadan; komut satırında yok)
svc_run() {
  local dir="$1"; shift
  as_svc bash -c 'd="$1"; f="$2"; shift 2; while IFS= read -r l; do export "$l"; done < "$f"; cd "$d" && exec "$@"' \
    _ "$dir" "$ENVFILE" "$@"
}
py() { svc_run "$APP" "$VENV/bin/python" "$@"; }

book_snapshot() {   # defterlerin bakiye ve açık pozisyonları (salt okunur)
  py - "$STATE" <<'PY'
import json, sys, pathlib
st = pathlib.Path(sys.argv[1])
for p in [st / "futures_ledger.json"] + sorted(st.glob("*/futures_ledger.json")):
    if not p.exists():
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print("  %-24s OKUNAMADI: %s" % (p.parent.name if p.parent != st else "ana", type(e).__name__)); continue
    pos = d.get("positions") or {}
    name = "ana" if p.parent == st else p.parent.name
    print("  %-24s bakiye=%-10s acik=%d %s" % (name, str(d.get("wallet_balance", d.get("equity")))[:10], len(pos),
                                            ",".join(sorted(pos))[:80]))
    for r in (d.get("history") or [])[-3:]:
        try:
            pnl = round(float(r.get("pnl") or 0), 3)
        except (TypeError, ValueError):
            pnl = r.get("pnl")
        print("      son kapanış: %-12s açılış %s  kapanış %s  %-22s pnl=%s" % (
            r.get("symbol"), str(r.get("opened_at"))[:16], str(r.get("closed_at"))[:16], r.get("exit_reason"), pnl))
PY
}

memory_report() {   # worker ve panel cgroup belleği (şu an / tepe / sınır) + OOM sayısı
  local u cg
  _mem() { local v; v="$(cat "$1" 2>/dev/null || true)"
           if [[ "$v" =~ ^[0-9]+$ ]]; then numfmt --to=iec "$v" 2>/dev/null || echo "$v"; else echo "${v:-?}"; fi; }
  for u in "$WORKER" "$DASH"; do
    cg="/sys/fs/cgroup/system.slice/$u"
    printf '   %-30s bellek (şu an / tepe / sınır): %s / %s / %s   OOM kill: %s\n' "$u" "$(_mem "$cg/memory.current")" \
      "$(_mem "$cg/memory.peak")" "$(_mem "$cg/memory.max")" "$(awk '$1=="oom_kill"{print $2}' "$cg/memory.events" 2>/dev/null || echo ?)"
  done
}

MEMMAX=""
memmax_check() {    # worker MemoryMax: 6G beklenir. 4G ise YÜKSEK SESLE uyarır (dağıtımı durdurmaz)
  local mm h
  mm="$(systemctl show "$WORKER" -p MemoryMax --value 2>/dev/null || true)"
  MEMMAX="$mm"
  if [[ "$mm" =~ ^[0-9]+$ ]]; then
    h="$(numfmt --to=iec "$mm" 2>/dev/null || echo "$mm")"
    if (( mm == MEM_EXPECT )); then
      ok "worker MemoryMax = $h ($mm bayt; beklenen 6G)"
    elif (( mm <= MEM_OLD )); then
      cat <<EOF
   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
   !!! UYARI: worker MemoryMax = $h ($mm bayt) — BEKLENEN 6G. Worker bugün ~3,4G kullanıyor, tepesi ~4,5G:
   !!! bu sınırda OOM (bellek) ile öldürülme riski YÜKSEK; öğrenme modu kayıt dosyalarını daha hızlı büyütür.
   !!! Dağıtımdan ÖNCE sınırı 6G yapmanız önerilir (kalıcı; worker'ı yeniden başlatmaz):
   !!!     sudo systemctl set-property $WORKER MemoryMax=6G
   !!! Dağıtım bu yüzden DURDURULMADI; geri alma tetiği (tepe > %90 × MemoryMax) bu sınıra göre hesaplanır.
   !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
EOF
    elif (( mm < MEM_EXPECT )); then
      echo "   UYARI: worker MemoryMax = $h ($mm bayt) — beklenen 6G (tepe ~4,5G; %90 eşiği $((mm * 9 / 10 / 1048576)) MB)"
    else
      echo "   worker MemoryMax = $h ($mm bayt; 6G'den büyük)"
    fi
  else
    echo "   UYARI: worker MemoryMax okunamadı ya da sınırsız ('${mm:-?}') — beklenen 6G"
  fi
}

# TRADINGBOT_LEARNING_MODE servis ortamında OLMAMALI: off/false/0/disabled öğrenmeyi SESSİZCE kapatır (dağıtım boşa
# gider), başka her değer ConfigError'dur (worker hiç başlamaz). Unit Environment= (drop-in dahil), EnvironmentFile'lar
# ve $BASE/env taranır.
lm_env_scan() {
  local hits=() e f
  for e in "${ENV_ARGS[@]}"; do
    if [[ "$e" == TRADINGBOT_LEARNING_MODE=* ]]; then hits+=("servis ortamı: $e"); fi
  done
  while IFS= read -r f; do
    [[ -n "$f" && -r "$f" ]] || continue
    if grep -qE '^[[:space:]]*(export[[:space:]]+)?TRADINGBOT_LEARNING_MODE[[:space:]]*=' "$f" 2>/dev/null; then
      hits+=("ortam dosyası: $f")
    fi
  done < <( { systemctl show "$WORKER" -p EnvironmentFiles --value 2>/dev/null | grep -oE '/[^ ]+' || true; echo "$BASE/env"; } | sort -u)
  if (( ${#hits[@]} )); then
    printf '   HATA  TRADINGBOT_LEARNING_MODE tanımlı:\n'; printf '           %s\n' "${hits[@]}"
    return 1
  fi
  ok "TRADINGBOT_LEARNING_MODE servis ortamında yok (config.yaml geçerli)"
}
lm_env_hint="off/false/0/disabled öğrenme modunu SESSİZCE kapatır, başka değer ConfigError (worker başlamaz). Satırı
  silin (unit için: sudo systemctl edit $WORKER; dosya için: $BASE/env) ve betiği yeniden çalıştırın"

mode_check() {      # state/mode.json: yok ya da mode=PAPER (öğrenme yalnız PAPER'da açılır)
  py - "$STATE/mode.json" <<'PY'
import json, os, sys
p = sys.argv[1]
if not os.path.exists(p):
    print("   OK  state/mode.json yok (varsayılan PAPER)")
    sys.exit(0)
try:
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
except Exception as exc:  # noqa: BLE001
    print("   HATA  state/mode.json okunamadı (%s) — elle bakın" % type(exc).__name__)
    sys.exit(1)
m = d.get("mode") if isinstance(d, dict) else None
lop = d.get("live_order_path_enabled") if isinstance(d, dict) else None
if m == "PAPER" and lop is not True:
    print("   OK  state/mode.json: mode=PAPER")
    sys.exit(0)
print("   HATA  state/mode.json: mode=%r live_order_path_enabled=%r — öğrenme modu yalnız PAPER'da açılır" % (m, lop))
sys.exit(1)
PY
}

# --check: öğrenme modu durumu, defter başına slot/marj/karşı-olgusal, dosya boyları ve GERİ ALMA TETİKLERİ (salt okunur;
# son satır "BOXSAMPLE ..." çağırana ölçüm örneğidir, ekrana basılmaz).
learning_report() {
  local rs_epoch="" rs_human="" tours=""
  if [[ -r "$MARK_RESTART" ]]; then
    rs_epoch="$(sed -n 1p "$MARK_RESTART" || true)"; rs_human="$(sed -n 2p "$MARK_RESTART" || true)"
  fi
  # tur süreleri: worker her turun sonunda "🎓 ÖĞRENME: … · <saniye>s" basar (yeniden başlatmadan beri; yoksa son 6 saat)
  tours="$(journalctl -u "$WORKER" --since "${rs_human:--6h}" -o cat --no-pager 2>/dev/null \
            | grep -aE 'ÖĞRENME: .* [0-9]+(\.[0-9]+)?s$' | sed -nE 's/.* ([0-9]+(\.[0-9]+)?)s$/\1/p' | tail -n 40 | tr '\n' ' ' || true)"
  local out
  out="$(py - "$STATE" "$APP" "${MEMMAX:-}" "$rs_epoch" "$tours" "$BOX_SAMPLES" <<'PY'
import datetime as dt
import json
import os
import sys
import time

st, app, memmax, rs_epoch, tours_s, boxs = sys.argv[1:7]
now = time.time()


def rj(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        return {"__err__": type(exc).__name__}


def fl(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def epoch(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.timestamp()


def ago(e):
    return "?" if e is None else "%.0f dk önce" % ((now - e) / 60.0)


def unreadable(d):
    """rj() okunamayan dosya için {"__err__": tür} döner: boş sayılmaz, OKUNAMADI yazılır."""
    return isinstance(d, dict) and "__err__" in d


rs = fl(rs_epoch)
warns = []
print("   (yeniden başlatma: %s)" % (dt.datetime.fromtimestamp(rs).strftime("%Y-%m-%d %H:%M:%S") if rs else "kayıt yok"))

# ---------------------------------------------------------------- öğrenme modu (health.json)
print("\n-- öğrenme modu (health.json)")
h = rj(os.path.join(st, "health.json"))
if unreadable(h):
    print("   health.json OKUNAMADI (%s)" % h["__err__"])
    warns.append("health.json okunamadı")
h = h if isinstance(h, dict) and not unreadable(h) else {}
h_at = epoch(h.get("at"))
if h:
    print("   health: durum=%s  yazıldı=%s (%s)  son tur=%ss" % (h.get("state"), h.get("at"), ago(h_at), h.get("seconds")))
lm = h.get("learning_mode")
if not isinstance(lm, dict):
    print("   learning_mode: health.json'da YOK — yeni worker ilk turunu henüz bitirmedi (WFO + tur 1 saati aşabilir)"
          " ya da öğrenme kapalı")
    warns.append("health.json'da learning_mode yok")
    lm = {}
else:
    print("   aktif=%s  neden=%s  durum değişimi=%s  öğrenme başlangıcı=%s"
          % (lm.get("active"), lm.get("reason"), lm.get("since"), lm.get("learning_mode_since")))
    if lm.get("active") is not True:
        warns.append("öğrenme modu AKTİF DEĞİL (neden %s)" % lm.get("reason"))
    mem = lm.get("memory") if isinstance(lm.get("memory"), dict) else {}
    print("   bellek: rss_mb=%s  hwm_mb=%s  deneyim önbelleği satırları=%s"
          % (mem.get("rss_mb"), mem.get("hwm_mb"), mem.get("exp_cache_rows")))
    tm = mem.get("trade_memory") if isinstance(mem.get("trade_memory"), dict) else {}
    print("   trade_memory okuyucusu: mb=%s  tam okuma=%s  artımlı okuma=%s  temiz=%s"
          % (tm.get("mb"), tm.get("full_loads"), tm.get("incremental_loads"), tm.get("clean")))
    if tm and tm.get("clean") is not True:
        warns.append("trade_memory okuyucusu temiz değil (clean=%s; eski okuyucuya düşüyor)" % tm.get("clean"))
    pb = lm.get("policy_basis") if isinstance(lm.get("policy_basis"), dict) else {}
    if pb.get("status") == "LOST":
        # görünüm öğrenme başladıktan sonra kayboldu; motor kirli öğreniciden YENİDEN KURMAZ (dördüncü doğrulama turu)
        print("   politika tabanı: KAYIP (%s; dosya durumu %s; öğrenme başlangıcı %s)"
              % (pb.get("code"), pb.get("file"), pb.get("learning_mode_since")))
        warns.append("politika tabanı KAYIP (POLICY_BASIS_LOST, dosya %s): state yedeğinden "
                     "learning_policy_basis.json (ya da .bak) geri yüklenip worker yeniden başlatılmalı; o zamana kadar "
                     "politika etiketi LEARNING_LEARNER" % pb.get("file"))
    else:
        print("   politika tabanı: seeded_at=%s  n_policy=%s  n_extra=%s  (updated_at=%s, v1_n_trades=%s)"
              % (pb.get("seeded_at"), pb.get("n_policy"), pb.get("n_extra"), pb.get("updated_at"), pb.get("v1_n_trades")))
        if not pb:
            warns.append("politika tabanı (policy_basis) yok")

# ---------------------------------------------------------------- defterler
print("\n-- defterler: açık pozisyon · slot (açık/K) · Σmarj/E · öğrenmede açılan (ekstra) · karşı-olgusal")
cfg = {}
try:
    import yaml
    with open(os.path.join(app, "config.yaml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
except Exception as exc:  # noqa: BLE001
    warns.append("config.yaml okunamadı (%s): K bilinmiyor" % type(exc).__name__)
lmc = cfg.get("learning_mode") if isinstance(cfg.get("learning_mode"), dict) else {}
lmb = lmc.get("books") if isinstance(lmc.get("books"), dict) else {}


def slots_k(name):
    b = lmb.get(name) if isinstance(lmb.get(name), dict) else {}
    return b.get("slots", 20) if (lmc.get("enabled") is True and b.get("enabled") is True) else None


books = [("main", "ana bot", st, None)]
idx = rj(os.path.join(st, "strategy_paper_index.json"))
if unreadable(idx):
    warns.append("strategy_paper_index.json okunamadı (%s): kâğıt defterler listelenemedi" % idx["__err__"])
for b in ((idx or {}).get("books") or []) if isinstance(idx, dict) else []:
    key = str((b or {}).get("key") or "") if isinstance(b, dict) else ""
    if key and all(c.isalnum() or c == "_" for c in key):
        books.append((str(b.get("name") or key), key, os.path.join(st, key), str(b.get("summary_file") or key + ".json")))
if os.path.isdir(os.path.join(st, "pattern_trader")):
    books.append(("pattern_trader", "pattern_trader", os.path.join(st, "pattern_trader"), "pattern_trader.json"))

for name, label, bdir, summ in books:
    led = rj(os.path.join(bdir, "futures_ledger.json"))
    if not isinstance(led, dict) or "__err__" in led:
        print("   %-30s defter %s" % (name, "yok" if led is None else "OKUNAMADI (%s)" % led.get("__err__")))
        continue
    pos = led.get("positions") or {}
    pos = list(pos.values()) if isinstance(pos, dict) else [p for p in pos if isinstance(p, dict)]
    margins = [fl(p.get("isolated_margin", p.get("margin"))) for p in pos if isinstance(p, dict)]
    msum = sum(m for m in margins if m is not None)
    if name == "main":
        rk = rj(os.path.join(st, "risk.json"))
        eq = fl(((rk or {}).get("learning_mode") or {}).get("equity_basis") if isinstance(rk, dict) else None)
    else:
        sd = rj(os.path.join(st, summ)) if summ else None
        sd = sd if isinstance(sd, dict) else {}
        eq = fl((sd.get("summary") or {}).get("starting_equity") if isinstance(sd.get("summary"), dict) else None) \
            or fl(sd.get("starting_equity"))
    eq = eq or fl(led.get("starting_equity"))
    lt = [((p.get("meta") or {}).get("learning") if isinstance(p.get("meta"), dict) else None) for p in pos]
    n_learn = sum(1 for t in lt if isinstance(t, dict))
    n_extra = sum(1 for t in lt if isinstance(t, dict) and t.get("learning_unlocked_by"))
    if name == "main":
        cf = rj(os.path.join(st, "shadow_book.json"))
        rows = [t for t in (cf.get("trades") or []) if isinstance(t, dict) and t.get("book") == "main"] \
            if isinstance(cf, dict) and not unreadable(cf) else ([] if cf is None else None)
        cf_src = "shadow_book.json (book=main)"
        dropped = None
    else:
        cf = rj(os.path.join(bdir, "counterfactual_trades.json"))
        rows = [t for t in (cf.get("trades") or []) if isinstance(t, dict)] \
            if isinstance(cf, dict) and not unreadable(cf) else None
        cf_src = "counterfactual_trades.json"
        dropped = (cf.get("meta") or {}).get("dropped") if isinstance(cf, dict) and not unreadable(cf) else None
    if unreadable(cf):
        cf_txt = "karşı-olgusal: %s OKUNAMADI (%s)" % (cf_src.split(" ")[0], cf["__err__"])
        warns.append("%s: %s okunamadı" % (name, cf_src.split(" ")[0]))
    elif rows is None:
        cf_txt = "karşı-olgusal: dosya yok"
    else:
        lab = sum(1 for t in rows if isinstance(t.get("outcome"), dict))
        cf_txt = "karşı-olgusal: bekleyen %d · etiketli %d%s  [%s]" % (
            len(rows) - lab, lab, (" · düşürülen %s" % dropped) if dropped else "", cf_src)
    k = slots_k(name)
    frac = (msum / eq) if eq else None
    extra = ""
    if name == "main":
        sp = rj(os.path.join(st, "spot_ledger.json"))
        lots = (sp or {}).get("lots") if isinstance(sp, dict) else None
        n_spot = sum(1 for v in (lots or {}).values() if v) if isinstance(lots, dict) else 0
        extra = " (+%d spot)" % n_spot if n_spot else ""
    print("   %-30s açık %3d%s  slot %3d/%-4s  Σmarj/E %6s  öğrenmede %3d (ekstra %d)  %s"
          % (name, len(pos), extra, len(pos), k if k is not None else "—",
             ("%.0f%%" % (frac * 100)) if frac is not None else "?", n_learn, n_extra, cf_txt))
    if frac is not None and frac > 0.95:
        warns.append("%s: Σmarj/E %.0f%% > %%95 (öğrenme sınırı aşılmış görünüyor)" % (name, frac * 100))
    if k is not None and len(pos) > k:
        warns.append("%s: açık pozisyon %d > slot K=%d" % (name, len(pos), k))

# ---------------------------------------------------------------- dosya boyları
print("\n-- büyüyen kayıt dosyaları (öğrenme açıkken daha hızlı büyür)")
for fn in ("trade_memory.jsonl", "position_path.jsonl", "profitability_experiment_v1_2_events.jsonl"):
    try:
        print("   %-46s %9.2f MB" % (fn, os.path.getsize(os.path.join(st, fn)) / 1048576.0))
    except OSError:
        print("   %-46s yok" % fn)

# ---------------------------------------------------------------- geri alma tetikleri
print("\n-- GERİ ALMA TETİKLERİ: (1) hwm_mb > %90 × MemoryMax  (2) tur > 35 dk  (3) Box kaçan bar > 0/saat")
res = []
mm = int(memmax) if str(memmax).isdigit() else None
hwm = fl((lm.get("memory") or {}).get("hwm_mb")) if isinstance(lm.get("memory"), dict) else None
if mm and hwm is not None:
    thr = 0.9 * mm / 1048576.0
    res.append(("bellek", hwm > thr, "hwm_mb %.0f MB / eşik %.0f MB (MemoryMax %.0f MB)" % (hwm, thr, mm / 1048576.0)))
else:
    res.append(("bellek", None, "ölçülemedi (hwm_mb=%s, MemoryMax=%s)" % (hwm, memmax or "?")))
tours = [fl(x) for x in tours_s.split()]
tours = [x for x in tours if x is not None]
if not tours and fl(h.get("seconds")) is not None and h_at is not None and (rs is None or h_at >= rs):
    tours.append(fl(h.get("seconds")))          # günlükte tur satırı yoksa: health.json'daki son tur
if tours:
    worst = max(tours)
    res.append(("tur süresi", worst > 2100, "en uzun tur %.1f dk (%d tur ölçüldü; son %.1f dk)"
                % (worst / 60.0, len(tours), tours[-1] / 60.0)))
else:
    res.append(("tur süresi", None, "yeniden başlatmadan beri biten tur yok"))
bt = rj(os.path.join(st, "box_timer.json"))
if unreadable(bt):
    warns.append("box_timer.json okunamadı (%s)" % bt["__err__"])
bt = bt if isinstance(bt, dict) and not unreadable(bt) else {}
le = bt.get("last_eval") if isinstance(bt.get("last_eval"), dict) else {}
le_at = epoch(le.get("at"))
missed = bt.get("missed_bars")
print("   Box zamanlayıcısı: toplam kaçan bar=%s  son değerlendirme %s (%s; önceki kaçan=%s)  hata=%s"
      % (missed, le.get("bar_open"), ago(le_at), le.get("missed_before"), bt.get("errors")))
sample = None
if isinstance(missed, int) and le_at is not None and (rs is None or le_at >= rs):
    samples = []
    try:
        with open(boxs, encoding="utf-8") as fh:
            for ln in fh:
                p = ln.split()
                if len(p) == 3 and all(x.lstrip("-").isdigit() for x in p):
                    samples.append(tuple(int(x) for x in p))
    except OSError:
        pass
    ref = None
    for e, m, la in samples:          # en yeni, ≥ 50 dk önceki, yeniden başlatma sonrası örnek
        if e <= now - 3000 and (rs is None or la >= rs):
            ref = (e, m, la)
    stalled = (now - le_at) > 900
    if ref is not None:
        d = missed - ref[1]
        hrs = (now - ref[0]) / 3600.0
        res.append(("Box kaçan bar", d > 0 or stalled, "%+d kaçan bar / %.1f saat = %.2f/saat%s"
                    % (d, hrs, d / hrs, "; SON DEĞERLENDİRME %.0f dk ÖNCE (zamanlayıcı durmuş olabilir)"
                       % ((now - le_at) / 60) if stalled else "")))
    elif stalled:
        res.append(("Box kaçan bar", True, "son değerlendirme %.0f dk önce — zamanlayıcı durmuş olabilir"
                    % ((now - le_at) / 60)))
    else:
        res.append(("Box kaçan bar", None, "ölçüm örneği kaydedildi; saatlik oran için --check'i ≥ 1 saat sonra "
                                           "yeniden çalıştırın"))
    sample = "%d %d %d" % (int(now), missed, int(le_at))
else:
    res.append(("Box kaçan bar", None, "yeniden başlatmadan sonra henüz değerlendirme yok (zamanlayıcı ilk turla "
                                       "başlar; WFO + tur 1 saati aşabilir)"))
fired = [r for r in res if r[1] is True]
for name, f, txt in res:
    print("   %-13s %-14s %s" % ("[TETİKLENDİ]" if f is True else ("[tamam]" if f is False else "[ölçülemedi]"), name, txt))
for w in warns:
    print("   UYARI: %s" % w)
if fired:
    print("\n   >>> GERİ ALMA TETİĞİ: %s. Bu --check çıktısını bana iletin. Acil durumda öğrenme modunu kapatın:"
          % ", ".join(r[0] for r in fired))
    print("       config.yaml → learning_mode.enabled: false ; sudo systemctl restart tradingbot-worker tradingbot-dashboard")
else:
    print("   tetiklenen yok")
if sample:
    print("BOXSAMPLE " + sample)
PY
)" || { printf '%s\n' "$out"; echo "   öğrenme raporu okunamadı"; return 0; }
  printf '%s\n' "$out" | grep -v '^BOXSAMPLE ' || true
  local s; s="$(printf '%s\n' "$out" | sed -n 's/^BOXSAMPLE //p' | tail -n 1 || true)"
  if [[ -n "$s" && -f "$MARK_RESTART" ]]; then echo "$s" >> "$BOX_SAMPLES" 2>/dev/null || true; fi
}

# Kodu dağıtım ÖNCESİ duruma döndürür: worker DURUR → Formasyon öğrenme planları temizlenir (YENİ kod hâlâ yerindeyken;
# betik yalnız yeni kodda var) → aynı dal adı + önceki commit → requirements değiştiyse pip → worker başlar, panel yeniden
# başlar. Yedeklere ve defterlere dokunmaz. Temizlik başarısızsa (çıkış 2: plans.json okunamadı/bozuk) DURUR: worker
# kapalı ve yeni kod yerinde kalır, eski kod başlatılmaz (eski kod öğrenme planlarını protokol işlemi olarak doldururdu).
REQ_CHANGED=""
revert_code() {
  trap - ERR
  trap '' INT TERM              # geri alma yarıda kesilmez (ikinci Ctrl+C dahil); ÖNCE iş, SONRA ekrana yazı
  systemctl stop "$WORKER" || true
  if [[ -f "$APP/scripts/learning_mode_rollback_prep.py" ]]; then
    local rc=0
    svc_run "$APP" "$VENV/bin/python" scripts/learning_mode_rollback_prep.py --state "$STATE" || rc=$?
    if (( rc != 0 )); then
      cat >&2 <<EOF

DUR: GERİ ALMA DURDURULDU — öğrenme planı temizliği başarısız (çıkış $rc; 2 = pattern_trader/plans.json okunamadı/bozuk).
  Durum: worker DURDURULDU; kod hâlâ ${T7} (yeni); eski kod BAŞLATILMADI; yedek ve defterlere dokunulmadı.
  Yapılacak (plans.json'a elle bakıldıktan sonra):
    sudo -u $SVC_USER $VENV/bin/python $APP/scripts/learning_mode_rollback_prep.py --state $STATE
    sudo -u $SVC_USER git -C $APP checkout -q -B $BRANCH_NOW ${PREV:0:7}     # (dal yoksa: checkout ${PREV:0:7})
    sudo systemctl start $WORKER; sudo systemctl restart $DASH
  Ya da yeni kodla sürdürmek için: sudo systemctl start $WORKER. Bu çıktıyı bana iletin.
EOF
      exit 3
    fi
  else
    echo "   (öğrenme planı temizliği betiği yok — çalışma ağacı yeni kodda değil)" || true
  fi
  if [[ "$BRANCH_NOW" != "detached" ]]; then
    # dalı yalnız bu betiğin az önce yaptığı ileri sarmadan geri alır (dal önceden tam olarak PREV'deydi)
    gitc checkout -q -B "$BRANCH_NOW" "$PREV" || gitc checkout -q "$PREV" \
      || echo "   UYARI: checkout başarısız — elle: sudo -u $SVC_USER git -C $APP checkout ${PREV:0:7}" >&2
  else
    gitc checkout -q "$PREV" || echo "   UYARI: checkout başarısız — elle: sudo -u $SVC_USER git -C $APP checkout ${PREV:0:7}" >&2
  fi
  if [[ -n "$REQ_CHANGED" ]]; then
    "$VENV/bin/pip" install -q -r "$APP/requirements.txt" >/dev/null 2>&1 || true
    chown -R "$SVC_USER:$SVC_USER" "$VENV" 2>/dev/null || true
  fi
  systemctl start "$WORKER" || true
  systemctl restart "$DASH" || true
  echo "   GERİ ALINDI: kod ${PREV:0:7} (öğrenme planları temizlendi); worker başlatıldı, panel yeniden başlatıldı" || true
}

[[ $EUID -eq 0 ]] || die "sudo ile çalıştırın: sudo bash $0 ${MODE}"
[[ -d "$APP/.git" ]] || die "$APP bir git kopyası değil"
load_env

# ------------------------------------------------------------------ --detach: SSH'tan bağımsız ayrı görev + izleme
if [[ "$MODE" == "--detach" ]]; then
  UNIT="tb-deploy-$T7"
  if systemctl is-active --quiet "$UNIT"; then
    echo "dağıtım zaten çalışıyor ($UNIT). İzlemek için: sudo journalctl -u $UNIT -f -o cat"; exit 0
  fi
  START="$(date '+%Y-%m-%d %H:%M:%S')"
  systemd-run --unit="$UNIT" --collect --quiet --setenv=TRADINGBOT_BASE="$BASE" --setenv=TRADINGBOT_USER="$SVC_USER" \
    bash "$HERE/$(basename "${BASH_SOURCE[0]}")" deploy || die "ayrı görev başlatılamadı (systemd-run)"
  cat <<EOF
Dağıtım AYRI bir görev olarak başladı ($UNIT). Bu pencereyi ya da PC'yi kapatsanız da sürer.
  Ctrl+C yalnız İZLEMEYİ bırakır, dağıtımı ETKİLEMEZ.
  Sonra tekrar izlemek:  sudo journalctl -u $UNIT -f -o cat     (görev bitince: sudo tail -60 $LOGDIR/$T7-deploy.log)
  Dağıtımı durdurmak:    sudo systemctl stop $UNIT   (yeniden başlatmadan önceyse kod geri alınır)
EOF
  journalctl -u "$UNIT" -f -o cat --no-pager --since "$START" \
    > >(grep --line-buffered -vE '^ +root : |pam_unix\(sudo:session\)') & jp=$!
  sleep 3
  while systemctl is-active --quiet "$UNIT"; do sleep 3; done
  sleep 2; kill "$jp" 2>/dev/null || true
  echo; echo "görev bitti. Durum için: sudo bash $0 --check"
  exit 0
fi

# ------------------------------------------------------------------ --check: dağıtım sonrası durum (state'e dokunmaz)
if [[ "$MODE" == "--check" ]]; then
  say "kod"
  echo "   HEAD $(gitc rev-parse --short HEAD) ($(gitc symbolic-ref --quiet --short HEAD || echo detached)) — hedef $T7" \
       "$([[ -r "$LOGDIR/$T7-prev-commit.txt" ]] && echo "— dağıtım öncesi $(head -c 7 "$LOGDIR/$T7-prev-commit.txt")")"
  say "servisler"
  for u in "$WORKER" "$DASH"; do
    echo "   $u: $(systemctl is-active "$u" || true), yeniden başlama sayısı $(systemctl show "$u" -p NRestarts --value)"
  done
  memory_report
  memmax_check
  echo "   panel: $(curl -fsS -m 5 http://127.0.0.1:8080/health/live 2>/dev/null | head -c 200 || echo 'yanıt yok')"
  say "ÖĞRENME MODU (yalnız PAPER)"
  learning_report
  say "defterler (şimdi)"
  book_snapshot
  if [[ -f "$LOGDIR/$T7-before.txt" ]]; then say "defterler (dağıtımdan hemen önce)"; cat "$LOGDIR/$T7-before.txt"; fi
  say "defterler: D4 (4h trend), C4 / C4S (mum varyasyonları), Box yapı modu, formasyon protokolü"
  py - "$STATE" <<'PY'
import json, os, sys
st = sys.argv[1]
for key, label in (("strategy_paper_trend4h", "D4 (4h trend, gözlem)"), ("strategy_paper_candle4h", "C4 (mum varyasyonları)"),
                   ("strategy_paper_candle4h_strict", "C4S (mum varyasyonları, sıkı)")):
    f = os.path.join(st, key + ".json")
    if not os.path.exists(f):
        print("   %s: özet henüz yok (ilk turdan sonra görünür)" % label)
        continue
    d = json.load(open(f, encoding="utf-8"))
    s, c = d.get("summary") or {}, d.get("counters") or {}
    print("   %s: özkaynak=%s açılan=%s kapanan=%s tur=%s son kural turu=%s"
          % (label, s.get("equity"), c.get("opened"), c.get("closed"), c.get("tours"), d.get("rule_evaluated_at")))
PY
  py - <<'PY' || true
import yaml
raw = yaml.safe_load(open("config.yaml", encoding="utf-8"))
print("   Box yapı modu (config):", (raw.get("structures") or {}).get("b1_box_fade"))
_ex = {b.get("name"): b for b in ((raw.get("strategy_paper") or {}).get("extra") or []) if isinstance(b, dict)}
for _n in ("c4_candle_variations", "c4s_candle_variations_strict"):
    print("   %s etkin varyasyon (config): %d" % (_n, len(((_ex.get(_n) or {}).get("rule_params") or {}).get("variations") or [])))
print("   formasyon protokolü (config):", (raw.get("pattern_trader") or {}).get("protocol"))
print("   öğrenme modu (config): enabled =", (raw.get("learning_mode") or {}).get("enabled"))
PY
  say "karne (maliyet sonrası; öğrenme öncesi/sonrası ayrı; salt okunur)"
  py scripts/bot_scorecard.py --state "$STATE" 2>&1 | tail -n 40 || echo "   karne okunamadı"
  say "son 30 dk uyarı/hata satırları (worker)"
  journalctl -u "$WORKER" --since "-30min" -p warning --no-pager 2>/dev/null | tail -n 25 || true
  exit 0
fi

[[ "$MODE" == "deploy" || "$MODE" == "--dry-run" ]] || die "bilinmeyen seçenek: $MODE (--dry-run | --detach | --check | seçeneksiz)"

# ------------------------------------------------------------------ 1) kontroller (hiçbir şeye dokunmaz)
say "1/9 kod kaynağı"
if [[ -f "$BUNDLE_SRC" ]]; then
  SOURCE="bundle"
  [[ -n "$BUNDLE_SHA256" ]] || die "bu sürüm için bundle tanımlı değil: $BUNDLE_SRC dosyasını kaldırın (kod GitHub'dan alınır). HİÇBİR ŞEYE DOKUNULMADI"
  got="$(sha256sum "$BUNDLE_SRC" | awk '{print $1}')"
  [[ "$got" == "$BUNDLE_SHA256" ]] || die "bundle sha256 uyuşmuyor: $got (beklenen $BUNDLE_SHA256) — dosya bozuk/eksik indirilmiş"
  ok "bundle, sha256 $got"
else
  SOURCE="github"
  ok "GitHub ($REPO_URL), commit ${TIP}"
fi

say "2/9 mevcut kod"
PREV="$(gitc rev-parse HEAD)"
BRANCH_NOW="$(gitc symbolic-ref --quiet --short HEAD || echo detached)"
echo "   HEAD ${PREV:0:7} ($BRANCH_NOW)"
if [[ "$PREV" == "$TIP" ]]; then echo "   zaten hedefte ($T7). Durum için: sudo bash $0 --check"; exit 0; fi
dirty="$(gitc status --porcelain --untracked-files=no)"
[[ -z "$dirty" ]] || die "izlenen dosyalarda yerel değişiklik var (HİÇBİR ŞEYE DOKUNULMADI):
$dirty"
ok "izlenen dosyalar temiz"

say "3/9 hedef commit'in getirilmesi (salt ekleme; çalışma ağacı değişmez)"
if [[ "$SOURCE" == "bundle" ]]; then
  install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$BASE/deploy-bundles"
  BUNDLE="$BASE/deploy-bundles/$(basename "$BUNDLE_SRC")"
  install -o "$SVC_USER" -g "$SVC_USER" -m 0640 "$BUNDLE_SRC" "$BUNDLE"
  gitc bundle verify -q "$BUNDLE" || die "bundle bu kopyaya uygulanamıyor (gereken taban $(shorts "${PREREQ[@]}") yok). HEAD ${PREV:0:7}. HİÇBİR ŞEYE DOKUNULMADI"
  gitc fetch -q "$BUNDLE" "$BUNDLE_REF:refs/deploy/$T7"
  [[ "$(gitc rev-parse "refs/deploy/$T7")" == "$TIP" ]] || die "bundle'daki dal ucu beklenen commit değil"
else
  # önce tam SHA ile (GitHub destekler); olmazsa dalı getirip commit'i onun içinde ara
  if ! as_svc env -C "$APP" HOME="$BASE" git fetch -q "$REPO_URL" "$TIP" 2>/dev/null; then
    as_svc env -C "$APP" HOME="$BASE" git fetch -q "$REPO_URL" "$BUNDLE_REF" \
      || die "GitHub'a erişilemedi ($REPO_URL). HİÇBİR ŞEYE DOKUNULMADI — VPS'in GitHub erişimini kontrol edin"
  fi
  gitc cat-file -e "${TIP}^{commit}" 2>/dev/null || die "hedef commit $T7 getirilemedi. HİÇBİR ŞEYE DOKUNULMADI"
  gitc update-ref "refs/deploy/$T7" "$TIP"
fi
ok "hedef commit $T7 hazır ($SOURCE)"

say "4/9 ileri sarma mümkün mü + çalışan sürüm"
gitc merge-base --is-ancestor "$PREV" "$TIP" || die "mevcut HEAD ${PREV:0:7} hedefin atası değil (ayrışmış kod). HİÇBİR ŞEYE DOKUNULMADI; bana HEAD'i iletin"
echo "   ${PREV:0:7} → $T7: $(gitc rev-list --count "$PREV..$TIP") commit"
FOUND=""
for p in "${PREREQ[@]}"; do             # en yeni eşleşen kazanır (liste eskiden yeniye)
  if gitc merge-base --is-ancestor "$p" "$PREV" 2>/dev/null; then FOUND="$p"; fi
done
[[ -n "$FOUND" ]] || die "çalışan sürüm ${PREV:0:7}, beklenenlerden ($(shorts "${PREREQ[@]}")) hiçbiri ya da soyu değil (daha ESKİ kod).
  Önce o sürümlerin betiklerini sırayla çalıştırın ya da bana HEAD'i iletin. HİÇBİR ŞEYE DOKUNULMADI"
if [[ "$PREV" == "$FOUND" ]]; then
  ok "çalışan sürüm ${FOUND:0:7} (kabul edilenler: $(shorts "${PREREQ[@]}"))"
else
  ok "çalışan sürüm ${PREV:0:7}: ${FOUND:0:7} soyundan ve hedefin atası (kabul edilenler: $(shorts "${PREREQ[@]}"))"
fi
for p in "${PREREQ[@]}"; do
  if [[ "$p" != "$FOUND" ]] && gitc merge-base --is-ancestor "$FOUND" "$p" 2>/dev/null \
     && ! gitc merge-base --is-ancestor "$p" "$PREV" 2>/dev/null; then
    echo "   ${p:0:7} sürümünün değişiklikleri de bu dağıtıma DAHİL (o sürümün betiğini ayrıca çalıştırmayın)"
  fi
done
if gitc diff --quiet "$PREV" "$TIP" -- requirements.txt; then REQ_CHANGED=""; else REQ_CHANGED=1; fi
echo "   requirements.txt: $([[ -n "$REQ_CHANGED" ]] && echo "DEĞİŞTİ (pip install yapılacak)" || echo "değişmedi (pip gerekmez)")"
ok "hızlı ileri sarma mümkün"

say "5/9 disk, servisler, bellek sınırı, ortam, mod"
free_kb="$(df -Pk "$DATA" | awk 'NR==2{print $4}')"
[[ "$free_kb" -gt 2097152 ]] || die "diskte 2 GB'tan az boş yer var ($((free_kb/1024)) MB) — yedek için yetersiz"
ok "boş disk $((free_kb/1024)) MB"
for u in "$WORKER" "$DASH"; do echo "   $u: $(systemctl is-active "$u" || true)"; done
memory_report
memmax_check
lm_env_scan || die "TRADINGBOT_LEARNING_MODE servis ortamında tanımlı: $lm_env_hint. HİÇBİR ŞEYE DOKUNULMADI"
mode_check || die "state/mode.json PAPER değil ya da okunamıyor — öğrenme modu yalnız PAPER'da açılır. HİÇBİR ŞEYE DOKUNULMADI"
say "defterler (şimdi)"
book_snapshot

# ------------------------------------------------------------------ 2) yeni kodun sınanması + değişiklik
if [[ "$MODE" == "deploy" ]]; then
  mkdir -p "$LOGDIR"
  # SSH bağlantısı koparsa (HUP) dağıtım yarıda KALMAZ: sona kadar sürer, çıktı ayrıca log dosyasına yazılır.
  trap '' HUP
  # tee de INT/TERM/HUP'u yok sayar: Ctrl+C önce tee'yi öldürürse betiğin sonraki yazımı SIGPIPE ile onu da öldürür ve
  # geri alma YARIM kalır (sahte VPS'te ölçüldü).
  exec > >(trap '' INT TERM HUP; exec tee -a "$LOGDIR/$T7-deploy.log") 2>&1
  echo "   (çıktı ayrıca: $LOGDIR/$T7-deploy.log)"
  book_snapshot > "$LOGDIR/$T7-before.txt" 2>&1 || true
  echo "$PREV" > "$LOGDIR/$T7-prev-commit.txt"
fi

# Ctrl+C / systemctl stop: kod henüz değişmediyse yalnız çıkar; değiştiyse geri alır; servis yeni kodla başladıysa bildirir.
MERGED=""; RESTARTED=""
WT="$BASE/deploy-wt-$T7"
cleanup_wt() { gitc worktree remove --force "$WT" >/dev/null 2>&1 || true; }
on_abort() {
  trap '' INT TERM
  if [[ -n "$RESTARTED" ]]; then
    echo "DUR: durduruldu — servisler yeni kodla başlatılmıştı; durum için: sudo bash $0 --check" >&2 || true
  elif [[ -n "$MERGED" ]]; then
    revert_code
    echo "DUR: durduruldu → kod ${PREV:0:7}'e geri alındı; worker eski kodla yeniden başlatıldı" >&2 || true
  else
    cleanup_wt
    echo "DUR: durduruldu — kod DEĞİŞMEDİ, servisler yeniden başlatılmadı" >&2 || true
  fi
  exit 130
}
trap on_abort INT TERM
trap 'cleanup_wt' ERR

say "6/9 yeni kodun sınanması — AYRI çalışma kopyasında, servis ortamıyla (çalışan bot ve kodu DEĞİŞMEZ)"
# Kod, ancak worker boşa çıktığı anda ve yeniden başlatmadan SANİYELER önce değiştirilir. Önceki sürümde kod beklemeden
# ÖNCE değişiyordu: bekleme sırasında worker kendiliğinden yeniden başlarsa (OOM) yeni kodla açılıyor, betik sonra
# diski geri alsa da bellekte yeni kod çalışmaya devam ediyordu (2026-09-25/26 VPS'te oldu).
cleanup_wt
[[ -e "$WT" ]] && rm -rf -- "${WT:?}"
gitc worktree add -q --detach "$WT" "$TIP"
if ! svc_run "$WT" "$VENV/bin/python" -m tradingbot preflight --quick; then
  cleanup_wt; die "preflight (yeni kod) başarısız → kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
fi
# Değişmezler yeni kodun KENDİ yükleyicisiyle (`load_config`: yinelenen anahtar reddi + servis ortamı, ör.
# TRADINGBOT_LEARNING_MODE) okunur; yükleme hatası (ConfigError) da düşme sayılır.
if ! svc_run "$WT" "$VENV/bin/python" - "$STATE" <<'PY'
import json
import os
import sys
from pathlib import Path

st = Path(sys.argv[1])
try:
    from tradingbot.config import load_config, load_yaml_strict
    cfg = load_config(Path("config.yaml"))
    raw = load_yaml_strict(Path("config.yaml").read_text(encoding="utf-8"))
except Exception as exc:  # noqa: BLE001 — ConfigError dahil (ör. geçersiz TRADINGBOT_LEARNING_MODE) → fail-closed
    print("   HATA  config yeni kodla ve servis ortamıyla yüklenemedi: %s: %s" % (type(exc).__name__, exc))
    sys.exit(1)
v3 = cfg.v3
from tradingbot import candle_variations as V
from tradingbot import learning_mode as LMM
from tradingbot.learn.research_coordinator import mode_gate
from tradingbot.risk.modes import ModeState
from tradingbot.risk.profiles import PROFILES
from tradingbot.strategy_paper import book_specs

books = {b.name: b for b in book_specs(v3)}
C4_IDS = ["CV001_BREAKOUT20_TREND_VOL_L", "CV002_BREAKOUT20_TREND_VOL_S", "CV003_PULLBACK_ENGULF_L", "CV004_PULLBACK_ENGULF_S",
          "CV005_SUPPORT_HARAMI_L", "CV006_RESIST_HARAMI_S", "CV007_SWEEP_REJECT_ENGULF_L", "CV008_SWEEP_REJECT_ENGULF_S"]
# docs/ogrenme_modu/CONTRACT.md "config.yaml values for L1" (+ 6fb39cd: ana bot leverage_max 5) — birebir
LM_SCALARS = {"enabled": True, "risk_per_trade_pct": 0.5, "max_total_open_risk_pct": 100, "margin_reserve_pct": 5,
              "liq_buffer_mult": 2.0, "min_notional_bump": True, "counterfactual": True, "counterfactual_max_pending": 2000}
LM_BOOKS = {
    "main": {"enabled": True, "slots": 20, "leverage_max": 5},
    "t2_trend_regime": {"enabled": True, "slots": 40, "leverage_max": 4},
    "m2_tsmom28": {"enabled": True, "slots": 40, "leverage_max": 4},
    "b1_box_fade": {"enabled": True, "slots": 20, "leverage_max": 4, "min_stop_pct": 0.32},
    "d4_donchian_20_10": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
    "c4_candle_variations": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
    "c4s_candle_variations_strict": {"enabled": False},
    "pattern_trader": {"enabled": True, "slots": 30, "leverage_max": 3},
}
LM_OVR = {"regime_gate_shadow": True, "candle_veto_shadow": True,
          "structures_entry_shadow": ["main", "t2_trend_regime", "m2_tsmom28"],
          "economics_exploration": True, "leverage_confidence_fallback": True}
lmraw = raw.get("learning_mode") if isinstance(raw.get("learning_mode"), dict) else {}
lm = v3.learning_mode


def same(a, b):
    """Birebir eşitlik; bool yalnız bool'a eşit (1 == True sayılmaz), sayılar değerce."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def lm_scalars():
    typed = {k: getattr(lm, k) for k in LM_SCALARS}
    rawv = {k: lmraw.get(k) for k in LM_SCALARS}
    return same(typed, LM_SCALARS) and same(rawv, LM_SCALARS) \
        and set(lmraw) == set(LM_SCALARS) | {"books", "strategy_overrides"}


def lm_books():
    exp_norm = {n: LMM.BookLearningCfg(**c).to_dict() for n, c in LM_BOOKS.items()}
    return same(lmraw.get("books"), LM_BOOKS) and same({n: b.to_dict() for n, b in lm.books.items()}, exp_norm)


def lm_runtime():
    """Yeni kodun `LearningMode`'u config'i defterlere birebir dağıtıyor mu (kapı açık varsayımıyla)."""
    m = LMM.LearningMode.from_config(v3, lambda: (True, "OK"))
    m.refresh()
    for n in LMM.BOOK_NAMES:
        c, b = LM_BOOKS.get(n) or {}, m.book(n)
        if not c.get("enabled"):
            if b is not None:
                return False
            continue
        if b is None or (b.slots, b.leverage_max, b.risk_pct, b.reserve_pct, b.liq_buffer_mult, b.hard_cap_pct,
                         b.min_stop_pct, b.symbols) != (c["slots"], c["leverage_max"], 0.5, 5.0, 2.0, 2.0,
                                                        c.get("min_stop_pct"), c.get("symbols")):
            return False
    return (m.structures_entry_shadow("main") and m.structures_entry_shadow("m2_tsmom28")
            and not m.structures_entry_shadow("b1_box_fade") and m.override("economics_exploration", False) is True)


def mode_file_ok():
    p = st / "mode.json"
    if p.exists():
        d = json.loads(p.read_text(encoding="utf-8"))
        if not (isinstance(d, dict) and d.get("mode") == "PAPER" and d.get("live_order_path_enabled") is not True):
            return False
    ms = ModeState(p)
    return mode_gate(ms.mode.value, v3.execution.gateway, ms.is_live_order_path_enabled()) == (True, "OK")


rp = raw.get("risk") or {}
checks = [
    # --- önceki sürümün 15 değişmezi (üçü TABAN olarak yeniden adlandırıldı: öğrenme kendi kaldıraç/risk
    #     tavanını defter başına ayrı taşır; config'teki TABAN değerler AYNEN kalır)
    ("mod PAPER", lambda: v3.mode.mode == "PAPER"),
    ("gerçek işlem kapalı", lambda: not bool(getattr(v3.mode, "live_trading", False))),
    ("T2/M2/Box/D4/C4/C4S defterleri", lambda: set(books) == {"t2_trend_regime", "m2_tsmom28", "b1_box_fade",
                                                             "d4_donchian_20_10", "c4_candle_variations",
                                                             "c4s_candle_variations_strict"}),
    ("D4 TABAN kaldıraç 1 (öğrenme kaldıracı ayrı: learning_mode.books)",
     lambda: int(books["d4_donchian_20_10"].rule_params.get("leverage", 1)) == 1),
    ("D4 ayrı defter", lambda: books["d4_donchian_20_10"].state_dir == "strategy_paper_trend4h"),
    ("C4/C4S TABAN kaldıraç 1", lambda: all(int(books[n].rule_params.get("leverage", 1)) == 1
                                            for n in ("c4_candle_variations", "c4s_candle_variations_strict"))),
    ("C4: CV001–CV008 gözlem olarak etkin",
     lambda: list(books["c4_candle_variations"].rule_params.get("variations") or []) == C4_IDS),
    ("C4 varyasyonları kapıdan geçiyor (standart)", lambda: all(V.gate(v)[1] is None for v in C4_IDS)),
    ("C4S boş (sıkı hükümle GÜÇLÜ varyasyon yok)",
     lambda: not list(books["c4s_candle_variations_strict"].rule_params.get("variations") or [])),
    ("C4S sıkı hüküm", lambda: books["c4s_candle_variations_strict"].rule_params.get("verdict_mode") == "strict"),
    ("C4/C4S ayrı defterler", lambda: (books["c4_candle_variations"].state_dir,
                                       books["c4s_candle_variations_strict"].state_dir)
                                      == ("strategy_paper_candle4h", "strategy_paper_candle4h_strict")),
    ("Box yapı katmanı SHADOW (karar B)", lambda: (raw.get("structures") or {}).get("b1_box_fade") == "SHADOW"),
    ("formasyon protokolü v3", lambda: v3.pattern_trader.protocol == "momentum_4h_v3"),
    ("tek coin tavanı %30", lambda: float(rp["max_position_pct"]) == 30.0),
    ("TABAN işlem riski %2 = öğrenme sert tavanı",
     lambda: float(rp["risk_per_trade_pct"]) == 2.0 and float(LMM.HARD_CAP_PCT) == 2.0),
    # --- öğrenme modu (CONTRACT.md "Additions" dahil)
    ("öğrenme modu ETKİN (servis ortamı dahil)", lambda: lm.enabled is True),
    ("öğrenme değerleri birebir: risk %0,5 · toplam açık risk 100 · rezerv %5 · likidasyon 2,0 × stop · "
     "min-notional yükseltme · karşı-olgusal (en çok 2000 bekleyen)", lm_scalars),
    ("öğrenme defterleri birebir: main K20 L5 · T2/M2 K40 L4 · Box K20 L4 min stop 0,32 · D4/C4 K20 L3 evren · "
     "C4S kapalı · Formasyon K30 L3", lm_books),
    ("öğrenme strateji ezmeleri birebir: rejim/mum gölge · yapı girişi gölge [main, T2, M2] · keşif · kaldıraç tabanı",
     lambda: same(lmraw.get("strategy_overrides"), LM_OVR) and same(dict(lm.strategy_overrides), LM_OVR)),
    ("öğrenme anahtarı defterlere doğru dağılıyor (yeni kod LearningMode)", lm_runtime),
    ("risk profili PAPER_RESEARCH, overrides {}",
     lambda: str(v3.risk_profiles.profile).upper() == "PAPER_RESEARCH"
     and (raw.get("risk_profiles") or {}).get("overrides") == {} and not (v3.risk_profiles.overrides or {})),
    ("TABAN profil sabitleri değişmedi (PAPER_RESEARCH: işlem %2, toplam açık risk %6; öğrenme profili kayıtsız)",
     lambda: (PROFILES["PAPER_RESEARCH"].risk_per_trade_pct, PROFILES["PAPER_RESEARCH"].max_total_open_risk_pct)
     == (2.0, 6.0) and set(PROFILES) == {"PAPER_RESEARCH", "TESTNET", "SHADOW_LIVE", "LIVE_LIMITED", "LIVE"}),
    ("yürütme kapısı paper", lambda: str(v3.execution.gateway).lower() == "paper"),
    ("testnet kapalı", lambda: v3.execution.testnet_enabled is False),
    ("TABAN adet tavanları 3 değişmedi (risk.max_open_positions, futures.max_positions, pattern_trader)",
     lambda: int(rp["max_open_positions"]) == 3 and int((raw.get("futures") or {})["max_positions"]) == 3
     and int(v3.pattern_trader.max_open_positions) == 3),
    ("state/mode.json yok ya da PAPER; mod kapısı açık (öğrenme AKTİF olur)", mode_file_ok),
]
bad = []
for n, fn in checks:
    try:
        okk = bool(fn())
        why = ""
    except Exception as exc:  # noqa: BLE001 — değerlendirilemeyen değişmez düşmüş sayılır
        okk, why = False, " (değerlendirilemedi: %s: %s)" % (type(exc).__name__, exc)
    if not okk:
        bad.append(n)
    print("   %s  %s%s" % ("OK  " if okk else "HATA", n, why))
if lm.enabled is not True and os.environ.get("TRADINGBOT_LEARNING_MODE"):
    print("   (servis ortamında TRADINGBOT_LEARNING_MODE=%s öğrenmeyi kapatıyor)" % os.environ["TRADINGBOT_LEARNING_MODE"])
print("   %d/%d değişmez geçti" % (len(checks) - len(bad), len(checks)))
sys.exit(1 if bad else 0)
PY
then
  cleanup_wt; die "config değişmezleri (yeni kod) düştü → kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
fi
cleanup_wt
ok "yeni kod ayrı kopyada preflight ve değişmezlerden geçti"

if [[ "$MODE" == "--dry-run" ]]; then
  say "KURU ÇALIŞMA BİTTİ — çalışan kod, state ve servisler DEĞİŞMEDİ (hedef kod git nesnelerine eklendi, ayrı kopya silindi)."
  echo "   Dağıtmak için: sudo bash $0   (ya da SSH'tan bağımsız: sudo bash $0 --detach)"
  exit 0
fi

say "7/9 doğrulanmış yedek (git ve servislere dokunmadan ÖNCE)"
svc_run "$APP" env TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$STATE" \
  TRADINGBOT_BACKUPS_DIR="$DATA/backups" bash "$APP/deploy/backup.sh" manual \
  || die "YEDEK BAŞARISIZ ya da DOĞRULANAMADI → dağıtım DURDURULDU (git/servis dokunulmadı)"
echo "$PREV" > "$BASE/.last_good_commit"       # deploy/rollback.sh bunu kullanır
chown "$SVC_USER:$SVC_USER" "$BASE/.last_good_commit" 2>/dev/null || true
ok "yedek alındı; geri dönüş işaretçisi ${PREV:0:7}"

say "8/9 worker'ın turlar arası beklemeye girmesi bekleniyor (kod hâlâ ${PREV:0:7})"
RESTART_AT="$(date '+%Y-%m-%d %H:%M:%S')"
key_log() {    # yalnız karar verdiren satırlar (analiz gürültüsü değil)
  journalctl -u "$WORKER" --since "$RESTART_AT" --no-pager 2>/dev/null \
    | grep -E 'Started|Stopping|Stopped|Main process exited|Scheduled restart|Failed|Traceback|ERROR|CRITICAL|Killed|oom|BLOCK|ALLOW|ConfigError|LEARNING' \
    | tail -n 40 || true
}
# Worker SIGTERM'de MEVCUT TURU bitirip çıkar; tur ~10-25 dk sürer, TimeoutStopSec 90 sn → tur ortasında durdurmak SIGKILL
# demektir (2026-09-25 ilk denemede iki kez oldu). Turlar arası beklemede ise 2 sn içinde temiz kapanır ve state yazılır.
# Bekleme aralığı: heartbeat.json yalnız turlar arasında "source": "watch" ile ~30 sn'de bir yazılır (tur başı yazımı
# bu alanı taşımaz).
idle_now() {
  py - "$STATE/heartbeat.json" <<'PY'
import datetime as dt, json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    t = dt.datetime.fromisoformat(str(d.get("ts") or d.get("at")).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    age = (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
except Exception:
    sys.exit(1)
sys.exit(0 if d.get("source") == "watch" and age < 45 else 1)
PY
}
# Worker her yeniden başlatmadan sonra ve her yeni 4h mumda TURDAN ÖNCE tam spot WFO döngüsü koşar (bekleme döngüsü
# bunu kontrol etmez); WFO + tur 1 saati aşabiliyor (2026-09-25 VPS: 30 dk içinde hiç bekleme görülmedi) → 3 saat.
phase_now() {
  journalctl -u "$WORKER" --since "-6h" -o cat --no-pager 2>/dev/null \
    | grep -E '^===== .* — (SPOT WFO|TUR #)' | tail -n 1 | tr -d '=' | sed 's/^ *//;s/ *$//' || true
}
waited=0
until idle_now; do
  if (( waited >= 10800 )); then
    die "worker 3 saat içinde turlar arası beklemeye girmedi → kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
  fi
  if (( waited % 300 == 0 )); then
    echo "   $(date '+%H:%M') worker meşgul [$(phase_now)] — bitince yeniden başlatılacak (bekleniyor: $((waited / 60)) dk)"
  fi
  sleep 10; waited=$((waited + 10))
done
ok "worker turlar arası beklemede"

say "9/9 son ortam denetimi + kod ${PREV:0:7} → $T7 (ff-only) + hemen yeniden başlatma + 60 sn kararlılık"
# bekleme saatler sürebilir: ortam ve mod bu arada değişmiş olabilir → kodu değiştirmeden hemen önce yeniden bak
ENV_ARGS=(); rm -f "$ENVFILE"; load_env
lm_env_scan || die "TRADINGBOT_LEARNING_MODE beklerken tanımlanmış: $lm_env_hint. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
mode_check || die "state/mode.json beklerken PAPER dışına çıkmış. Kod DEĞİŞMEDİ, servisler yeniden başlatılmadı"
MERGED=1
gitc merge -q --ff-only "$TIP"
# Bu noktadan sonra beklenmeyen her hata kodu geri alır (worker durur → plan temizliği → eski kod → worker başlar).
trap 'revert_code; echo "DUR: beklenmeyen hata (satır $LINENO) → kod ${PREV:0:7}e geri alındı" >&2' ERR
[[ "$(gitc rev-parse HEAD)" == "$TIP" ]] || { revert_code; die "ileri sarma sonrası HEAD hedef değil → kod ${PREV:0:7}'e geri alındı"; }
if [[ -n "$REQ_CHANGED" ]]; then
  "$VENV/bin/pip" install -q -r "$APP/requirements.txt"
  chown -R "$SVC_USER:$SVC_USER" "$VENV" 2>/dev/null || true
fi
RESTART_EPOCH="$(date +%s)"
RESTART_AT="$(date -d "@$RESTART_EPOCH" '+%Y-%m-%d %H:%M:%S')"
printf '%s\n%s\n' "$RESTART_EPOCH" "$RESTART_AT" > "$MARK_RESTART"
RESTARTED=1
systemctl restart "$WORKER" "$DASH" || true      # preflight düşerse restart hata döner; aşağıda yakalanır
# NRestarts yalnız OTOMATİK yeniden başlamaları sayar ve elle restart'ta SIFIRLANIR: dağıtım öncesi değerle
# KARŞILAŞTIRILMAZ (2026-09-25 VPS: sağlıklı worker bu yüzden "kalkmadı" sayılıp geri alınmıştı). Taban = restart'tan
# hemen sonraki değer; kararlılık = 60 sn boyunca aynı süreç (MainPID) ve taban üstünde yeni otomatik başlama yok.
n0="$(systemctl show "$WORKER" -p NRestarts --value)"
up=""
for _ in $(seq 1 36); do                         # en çok 3 dk: preflight + başlatma
  sleep 5
  if [[ "$(systemctl is-active "$WORKER" || true)" == "active" ]]; then up=1; break; fi
done
pid1=""
if [[ -n "$up" ]]; then
  pid1="$(systemctl show "$WORKER" -p MainPID --value)"
  sleep 60
  if [[ "$(systemctl is-active "$WORKER" || true)" != "active" || -z "$pid1" || "$pid1" == "0" \
        || "$(systemctl show "$WORKER" -p MainPID --value)" != "$pid1" \
        || "$(systemctl show "$WORKER" -p NRestarts --value)" != "$n0" ]]; then
    up=""
  fi
fi
if [[ -z "$up" ]]; then
  key_log
  revert_code
  die "worker kararlı çalışmadı (aktif olmadı ya da 60 sn içinde yeniden başladı) → worker durduruldu, öğrenme planları temizlendi, kod ${PREV:0:7}'e geri alındı ve servisler yeniden başlatıldı. Yukarıdaki satırları bana iletin"
fi
trap - ERR INT TERM
ok "worker çalışıyor (PID $pid1, 60 sn kararlı)"
echo "   panel: $(curl -fsS -m 5 http://127.0.0.1:8080/health/live 2>/dev/null | head -c 200 || echo 'henüz yanıt yok (birkaç sn sonra --check)')"
memory_report
memmax_check

cat <<EOF

DAĞITIM TAMAM: ${PREV:0:7} → $T7 — ÖĞRENME MODU (yalnız PAPER). Defterler sıfırlanmadı (önceki durum: $LOGDIR/$T7-before.txt).

İzleme (ilk 2-3 turda birkaç kez):
  sudo bash $0 --check                         # öğrenme durumu, defter slotları, bellek, GERİ ALMA TETİKLERİ, karne
  journalctl -u $WORKER -f                     # canlı log (Ctrl+C ile çık)
Yeni worker önce tam spot WFO döngüsünü, sonra ilk turu çalıştırır (toplam 1 saati aşabilir); öğrenme durumu health.json'a
ilk turun sonunda yazılır, Box zamanlayıcısı ilk turla başlar. İşlem başına USDT yaklaşık 4 kat küçüktür (%0,5 risk);
R değişmez. Karşı-olgusal ("olsaydı") kayıtlar P&L'e girmez.
Geri alma tetikleri (--check işaretler): tepe bellek (hwm_mb) > %90 × MemoryMax · tur > 35 dk · Box kaçan bar > 0/saat.
Biri tetiklenirse --check çıktısını bana iletin.

Öğrenme modunu KAPATMAK (tercih edilen geri alma; kod aynı kalır):
  config.yaml → learning_mode: enabled: false ; sudo systemctl restart $WORKER $DASH
ESKİ KODA dönmek (yalnız gerekirse; SIRAYLA — plan temizliği yeni kod yerindeyken):
  sudo systemctl stop $WORKER
  sudo -u $SVC_USER $VENV/bin/python $APP/scripts/learning_mode_rollback_prep.py --state $STATE   # çıkış 2 → DUR, bana iletin
  sudo bash $APP/deploy/rollback.sh            # → ${PREV:0:7} (worker'ı başlatır)
EOF
if gitc merge-base --is-ancestor "$PREV" "$OOM_FIX" 2>/dev/null && [[ "$(gitc rev-parse "$OOM_FIX" 2>/dev/null)" != "$PREV" ]]; then
  echo "  UYARI: ${PREV:0:7} bellek (OOM) onarımından ÖNCEKİ koddur — ona dönmek OOM'u GERİ GETİRİR; sağlıklı bir dönüş değildir."
fi
