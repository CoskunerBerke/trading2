"""Quant Evaluation V1 — offline/salt-okunur araştırma paketi.

Hiçbir modül worker karar döngüsünde çalışmaz; canlı state, ledger, outbox veya order gateway'e
YAZMAZ. Girdi olarak mevcut PAPER artefaktlarını (TradeMemory JSONL, shadow_book.json, replay
çıktıları) okur; çıktı yalnız çağıranın açıkça verdiği yola atomic olarak yazılır.
"""
