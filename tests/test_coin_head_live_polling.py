"""Coin head tablosu CANLI POLLING sözleşmesi — GERÇEK tarayıcı JS'i node ile yürütülür.

Kapatılan açık: ilk HTML render'ı kapsamı düzeltmişti fakat `live_script` yalnız positions/summary/
health'i yeniliyordu. Sayfa açıkken bot pozisyon açar/kapatırsa üst tablo güncellenirken coin head
tablosu ve «Açık pozisyon kapsamı X / Y» sayacı BAYAT kalıyordu.

Bu dosya JS'i KAYNAKTA ARAMAZ: `live_script()` çıktısı node içinde çalıştırılır, sahte bir DOM
üzerinde gerçek `poll()`/`buildHeadsTable()` işletilir ve ortaya çıkan DOM ölçülür.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient          # noqa: E402

from tradingbot.dashboard.app import create_app    # noqa: E402
from tradingbot.dashboard.config import DashboardConfig  # noqa: E402
from tradingbot.dashboard.templates import HEADS_TABLE_CLS, live_script  # noqa: E402
from tradingbot.dashboard.views import (COIN_HEAD_COLUMNS, NO_DECISION_REASON,  # noqa: E402
                                        NO_DECISION_VERDICT, coin_head_table)

_NODE = shutil.which("node")
needs_node = pytest.mark.skipif(_NODE is None, reason="node yok — gerçek JS yürütme testi atlanır")

OPEN5 = [("BZ/USDT", "F00001", "LONG"), ("XAUT/USDT", "F00002", "LONG"),
         ("LDO/USDT", "F00003", "SHORT"), ("AAVE/USDT", "F00004", "LONG"),
         ("ETH/USDT", "F00005", "SHORT")]
NEW6 = ("SOL/USDT", "F00006", "LONG")


def _pos(sym, pid, side):
    return {"id": pid, "symbol": sym, "market_type": "USDM_PERP", "side": side, "qty": "1",
            "entry_avg": "100", "leverage": 2, "isolated_margin": "50", "stop": "90",
            "targets": ["120"], "last_price": "104", "entry_fee": "0",
            "opened_at": "2026-08-20T00:00:00+00:00", "fills": [{"id": "fill-" + pid}]}


def _head(sym, conf, verdict="FUTURES_LONG"):
    return {"symbol": sym, "verdict": verdict, "direction": "LONG", "confidence_calibrated": conf,
            "p_win": 0.55, "expected_return_net": 0.01, "expected_r": 1.5, "regime": "TREND",
            "generated_at": "2026-08-20T00:00:00+00:00", "vetoes": [],
            "spot_plan": {"valid": False}, "futures_plan": {"valid": True}}


HEADS = ([_head("XAUT/USDT", 0.91), _head("AAVE/USDT", 0.90)]
         + [_head("C%02d/USDT" % i, 0.80 - i * 0.01) for i in range(12)])


def _state(tmp_path: Path, name: str, positions: list[dict], *, trades=None, heads=HEADS) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "futures_ledger.json").write_text(json.dumps({
        "schema_version": 2, "kind": "futures", "equity": "500", "wallet_balance": "500",
        "fees": {"taker_pct": 0.0, "maker_pct": 0.0},
        "positions": {p["symbol"]: p for p in positions}, "history": trades or []}), encoding="utf-8")
    (d / "coin_heads.json").write_text(json.dumps({
        "generated_at": "2026-08-20T00:00:00+00:00", "heads": heads,
        "chief": {"breadth": {"long": 2, "short": 0, "no_trade": 10, "hold": 2, "data_invalid": 0}}}),
        encoding="utf-8")
    (d / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")
    return d


def _client(state_dir: Path, tmp_path: Path) -> TestClient:
    return TestClient(create_app(state_dir, tmp_path / "market", None, DashboardConfig()))


@pytest.fixture
def dirs(tmp_path):
    five = _state(tmp_path, "s5", [_pos(*o) for o in OPEN5])
    six = _state(tmp_path, "s6", [_pos(*o) for o in OPEN5] + [_pos(*NEW6)])
    # BZ kapandi FAKAT hala coin-head seckisinde: satir kalir, statusu AÇIK OLMAMALIDIR.
    four = _state(tmp_path, "s4", [_pos(*o) for o in OPEN5[1:]],
                  trades=[{"id": "F00001", "symbol": "BZ/USDT", "net_pnl": "3.5",
                           "closed_at": "2026-08-21T00:00:00+00:00"}],
                  heads=[_head("BZ/USDT", 0.99)] + HEADS)
    return {"5": five, "6": six, "4": four}


# ===================================================================== node harness
_HARNESS = r"""
const fs = require('fs');
const payloads = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const failing  = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const seed     = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
function mk(id){return {id:id, innerHTML:(seed[id]&&seed[id].innerHTML)||'',
  textContent:(seed[id]&&seed[id].textContent)||'', className:(seed[id]&&seed[id].className)||'',
  style:{display:(seed[id]&&seed[id].display)||''},
  classList:{add(){},remove(){},toggle(){}}, querySelector(){return null;}};}
const nodes={};
['postbl','headstbl','headscov','headsmiss','headsstale','livedot','livelabel','livebar',
 'stalenote','priceage','runage','headsage'].forEach(function(i){nodes[i]=mk(i);});
global.document={getElementById:function(i){return nodes[i]||null;},addEventListener:function(){},hidden:false};
global.window={};
global.setInterval=function(){return 0;};
global.clearInterval=function(){};
global.fetch=function(u){var k=String(u).split('?')[0];
  if(failing.indexOf(k)>=0){return Promise.reject(new Error('endpoint down'));}
  if(!(k in payloads)){return Promise.reject(new Error('no payload '+k));}
  return Promise.resolve({ok:true,json:function(){return Promise.resolve(payloads[k]);}});};
__SCRIPT__
setTimeout(function(){
  var out={};
  Object.keys(nodes).forEach(function(i){out[i]={innerHTML:nodes[i].innerHTML,
    textContent:nodes[i].textContent,className:nodes[i].className,display:nodes[i].style.display};});
  process.stdout.write(JSON.stringify(out));
}, 80);
"""


def _run_js(payloads: dict, *, failing: tuple = (), seed: dict | None = None) -> dict:
    """`live_script()` çıktısını GERÇEKTEN node'da çalıştırır ve ortaya çıkan DOM'u döndürür.

    Yükler argv yerine DOSYADAN okunur (Windows komut satırı uzunluk sınırı).
    """
    import tempfile
    js = live_script(DashboardConfig())
    js = re.sub(r"^<script>|</script>$", "", js.strip(), flags=re.S)
    d = Path(tempfile.mkdtemp())
    (d / "p.json").write_text(json.dumps(payloads), encoding="utf-8")
    (d / "f.json").write_text(json.dumps(list(failing)), encoding="utf-8")
    (d / "s.json").write_text(json.dumps(seed or {}), encoding="utf-8")
    (d / "prog.js").write_text(_HARNESS.replace("__SCRIPT__", js), encoding="utf-8")
    r = subprocess.run(["node", str(d / "prog.js"), str(d / "p.json"), str(d / "f.json"),
                        str(d / "s.json")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _payloads(client) -> dict:
    return {"/api/live/coin-heads": client.get("/api/live/coin-heads").json(),
            "/api/live/positions": client.get("/api/live/positions").json(),
            "/api/live/summary": client.get("/api/live/summary").json(),
            "/api/live/health": client.get("/api/live/health").json()}


class _T(HTMLParser):
    """`.tw > table.X` gövdesini satır/hücre/sınıf olarak ayrıştırır."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cur, self.cell, self.cls = [], None, None, []
        self.headers, self.header_num, self.table_cls, self.tw = [], [], None, 0
        self._in_th = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div" and "tw" in (a.get("class") or "").split():
            self.tw += 1
        if tag == "table":
            self.table_cls = a.get("class")
        if tag == "tr":
            self.cur = []
        if tag == "th":
            self._in_th = True
            self.header_num.append("num" in (a.get("class") or "").split())
            self.headers.append("")
        if tag == "td":
            self.cell = ""
            self.cls.append((a.get("class") or "").split())

    def handle_data(self, d):
        if self._in_th and self.headers:
            self.headers[-1] += d
        elif self.cell is not None:
            self.cell += d

    def handle_endtag(self, tag):
        if tag == "th":
            self._in_th = False
        if tag == "td" and self.cur is not None:
            self.cur.append(self.cell.strip())
            self.cell = None
        if tag == "tr" and self.cur:
            self.rows.append(self.cur)
            self.cur = None


def _shape(html: str) -> dict:
    t = _T()
    t.feed(html)
    return {"headers": [h.strip() for h in t.headers], "rows": t.rows, "cls": t.cls,
            "table_cls": t.table_cls, "tw": t.tw,
            "header_num": [i for i, v in enumerate(t.header_num) if v]}


def _server_headstbl(client) -> str:
    html = client.get("/").text
    i = html.index('<div id="headstbl">')
    j = html.index('<div id="headsstale"', i)
    return html[i:j]


def _syms(shape: dict) -> list[str]:
    return [r[0] for r in shape["rows"]]


# ===================================================================== 1) ilk render
def test_first_render_shows_all_five_open_positions(dirs, tmp_path):
    c = _client(dirs["5"], tmp_path)
    html = c.get("/").text
    assert "Açık pozisyon kapsamı: 5 / 5" in html
    shape = _shape(_server_headstbl(c))
    assert _syms(shape)[:5] == [o[0] for o in OPEN5]
    api = c.get("/api/live/coin-heads").json()
    assert (api["open_positions_total"], api["open_positions_shown"]) == (5, 5)
    assert api["coverage_complete"] is True and api["missing_open_symbols"] == []
    for k in ("coin_head_scope", "generated_at", "columns", "rows", "meta"):
        assert k in api, k


# ===================================================================== 2) canlı polling
@needs_node
def test_polling_updates_table_and_counter_without_page_reload(dirs, tmp_path):
    """Sayfa 5 açıkken yüklendi; bot 6. pozisyonu açtı → DOM yenilenmeden 6/6 olur."""
    c5, c6 = _client(dirs["5"], tmp_path), _client(dirs["6"], tmp_path)
    seed = {"headstbl": {"innerHTML": _server_headstbl(c5)},
            "headscov": {"textContent": "Açık pozisyon kapsamı: 5 / 5", "className": "badge b-ok"}}
    dom = _run_js(_payloads(c6), seed=seed)
    assert dom["headscov"]["textContent"] == "Açık pozisyon kapsamı: 6 / 6"
    assert dom["headscov"]["className"] == "badge b-ok"
    shape = _shape(dom["headstbl"]["innerHTML"])
    assert _syms(shape)[:6] == [o[0] for o in OPEN5] + [NEW6[0]]
    assert dom["headsstale"]["display"] == "none"
    assert dom["headsmiss"]["display"] == "none"


@needs_node
def test_polling_drops_a_closed_position_from_the_forced_open_group(dirs, tmp_path):
    """Pozisyon kapanınca 5/5'e iner ve o satırda AÇIK etiketi KALMAZ."""
    c5, c4 = _client(dirs["5"], tmp_path), _client(dirs["4"], tmp_path)
    seed = {"headstbl": {"innerHTML": _server_headstbl(c5)}}
    dom = _run_js(_payloads(c4), seed=seed)
    assert dom["headscov"]["textContent"] == "Açık pozisyon kapsamı: 4 / 4"
    shape = _shape(dom["headstbl"]["innerHTML"])
    syms = _syms(shape)
    assert syms[:4] == [o[0] for o in OPEN5[1:]]
    row = next(r for r in shape["rows"] if r[0] == "BZ/USDT")   # aday olarak KALIR
    assert row[2] != "AÇIK", row                          # ama AÇIK gibi GÖSTERİLMEZ
    assert row[2] == "KAPANDI"
    assert "AÇIK" not in dom["headstbl"]["innerHTML"].split("BZ/USDT")[1][:400]
    api = _client(dirs["4"], tmp_path).get("/api/live/coin-heads").json()
    assert "BZ/USDT" not in api["coin_head_scope"]["no_decision_symbols"]


@needs_node
def test_polling_renders_fallback_row_for_a_new_unknown_symbol(dirs, tmp_path):
    """Yeni açılan sembol coin-head seçkisinde yoksa fallback satırı canlıda da görünür."""
    dom = _run_js(_payloads(_client(dirs["6"], tmp_path)))
    shape = _shape(dom["headstbl"]["innerHTML"])
    row = next(r for r in shape["rows"] if r[0] == NEW6[0])
    assert row[1] == NO_DECISION_VERDICT and row[2] == "AÇIK"
    assert row[3] == NEW6[2]                              # yön DEFTERDEN
    assert row[12] == NEW6[1]                             # position_id DEFTERDEN
    assert NO_DECISION_REASON in row[14]
    assert row[11].startswith("+") or row[11].startswith("-")   # anlık PnL


@needs_node
def test_polling_never_duplicates_a_symbol(dirs, tmp_path):
    dom = _run_js(_payloads(_client(dirs["5"], tmp_path)))
    syms = _syms(_shape(dom["headstbl"]["innerHTML"]))
    assert len(syms) == len(set(syms))


def test_open_positions_are_never_dropped_by_the_candidate_limit(dirs, tmp_path):
    payload = coin_head_table(HEADS, [_pos(*o) for o in OPEN5], [], candidate_limit=1)
    syms = [r[0] for r in payload["rows"]]
    assert syms[:5] == [o[0] for o in OPEN5] and len(syms) == 6
    assert payload["coverage_complete"] is True


# ===================================================================== 3) parite
@needs_node
def test_api_first_render_and_real_js_dom_are_identical(dirs, tmp_path):
    """API ↔ ilk sunucu render'ı ↔ gerçek JS polling DOM'u BİREBİR aynı sözleşme."""
    c = _client(dirs["5"], tmp_path)
    srv = _shape(_server_headstbl(c))
    pol = _shape(_run_js(_payloads(c))["headstbl"]["innerHTML"])
    assert srv["headers"] == pol["headers"] == list(COIN_HEAD_COLUMNS)
    assert srv["header_num"] == pol["header_num"]
    assert srv["table_cls"] == pol["table_cls"] == HEADS_TABLE_CLS
    assert srv["tw"] == pol["tw"] == 1
    assert srv["rows"] == pol["rows"]                     # hücre metinleri birebir
    assert srv["cls"] == pol["cls"]                       # sütun sınıfları birebir
    api = c.get("/api/live/coin-heads").json()
    assert [r[0] for r in api["rows"]] == _syms(srv) == _syms(pol)


# ===================================================================== 4) hata dayanıklılığı
@needs_node
def test_endpoint_failure_keeps_the_table_and_shows_a_stale_warning(dirs, tmp_path):
    c = _client(dirs["5"], tmp_path)
    before = _server_headstbl(c)
    dom = _run_js(_payloads(c), failing=("/api/live/coin-heads",),
                  seed={"headstbl": {"innerHTML": before},
                        "headsstale": {"display": "none"}})
    assert dom["headstbl"]["innerHTML"] == before          # TABLO SİLİNMEZ
    assert dom["headsstale"]["display"] == ""              # stale uyarısı görünür


@needs_node
def test_next_successful_poll_clears_the_stale_warning(dirs, tmp_path):
    c = _client(dirs["5"], tmp_path)
    dom = _run_js(_payloads(c), seed={"headsstale": {"display": ""}})
    assert dom["headsstale"]["display"] == "none"
    assert dom["headstbl"]["innerHTML"] != ""


@needs_node
def test_polling_keeps_overlap_timeout_and_backoff_contracts():
    js = live_script(DashboardConfig())
    assert "if(busy[key])return;" in js and "AbortController" in js
    assert "document.hidden?base*BG:base" in js
    assert "/api/live/coin-heads" in js
    assert "http://" not in js.replace("http://127.0.0.1", "")   # dış ağ çağrısı yok


# ===================================================================== 5) güvenlik
@needs_node
def test_symbol_and_reason_are_escaped_in_both_surfaces(tmp_path):
    """XSS: sembol ve gerekçe alanları iki yüzeyde de kaçışlanır."""
    evil = '<img src=x onerror=alert(1)>/USDT'
    d = _state(tmp_path, "evil", [_pos(evil, "F0<script>", "LONG")], heads=[])
    c = _client(d, tmp_path)
    html = c.get("/").text
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    api = c.get("/api/live/coin-heads").json()
    assert api["rows"][0][0] == evil                        # JSON'da HAM (kaçışlama render'da)
    dom = _run_js(_payloads(c))
    body = dom["headstbl"]["innerHTML"]
    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body
    assert "<script>" not in body


def test_all_get_paths_are_read_only(dirs, tmp_path):
    """GET/state salt-okunurluk: path + size + mtime_ns + sha256."""
    import hashlib
    d = dirs["5"]

    def fp():
        return {p.name: (p.stat().st_size, p.stat().st_mtime_ns,
                         hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(d.iterdir())}

    c = _client(d, tmp_path)
    before = fp()
    for path in ("/", "/api/overview", "/api/live/coin-heads", "/api/live/positions",
                 "/api/live/summary", "/api/live/health", "/health/live"):
        assert c.get(path).status_code == 200, path
    assert c.get("/health/ready").status_code in (200, 503)   # sentetik state'te heartbeat yok
    assert fp() == before


def test_coin_heads_endpoint_rejects_writes(dirs, tmp_path):
    c = _client(dirs["5"], tmp_path)
    for verb in ("post", "put", "delete", "patch"):
        assert getattr(c, verb)("/api/live/coin-heads").status_code == 405
