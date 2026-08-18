# LIVE GRADUATION — kapılar (bu görevde LIVE aktif edilmedi)

Durumlar: OBSERVE → PAPER (varsayılan) → TESTNET → SHADOW_LIVE → LIVE_LIMITED → LIVE. Geçişler `risk/modes.py::ModeState.request_transition` ile **yalnız manuel**; her talep `state/mode.json` geçmişine yazılır.

- PAPER→TESTNET: manuel config, testnet anahtarı var, test paketi geçti, health ok.
- TESTNET→SHADOW_LIVE: operatör onayı, secret doğrulama, read-only izinler, reconciliation ok.
- SHADOW_LIVE→LIVE_LIMITED (`GraduationGates`, config ile değişir; düşürülürse **uyarı** verilir, sessiz kabul yok): ≥90 gün paper, ≥300 kapanmış işlem, ≥3 rejim, masraflar sonrası pozitif OOS expectancy, bootstrap CI ok, max DD ≤ %8, ≥30 gün kritik incident yok, testnet lifecycle ok, shadow ↔ paper yakınlığı, açık manuel onay + `ALLOW_LIVE_TRADING=true` env + config bayrağı + typed token (`stable_id("LIVE-CONFIRM", account_label)`).
- LIVE: bu sürümde **her koşulda reddedilir** (`ExecutionDisabledError`; `LIVE_ORDER_PATH_ENABLED_IN_THIS_BUILD=False`); `LiveGateway.submit` bütün kilitler açık olsa bile `NotImplementedError`. Config'te `mode: LIVE/LIVE_LIMITED` veya `execution.gateway: live` → program başlamaz.
- LIVE_LIMITED tasarımı: küçük sabit risk (%0.25), düşük notional, max 1-2x, izole, whitelist coin, manuel aktivasyon, günlük kill switch.
