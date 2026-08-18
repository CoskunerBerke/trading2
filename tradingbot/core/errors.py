"""Alan-özel hatalar. Kritik yollarda geniş `except Exception` yerine bunlar kullanılır."""
from __future__ import annotations


class TradingBotError(Exception):
    """Bütün bot hatalarının tabanı."""


class ConfigError(TradingBotError):
    """Geçersiz/eksik yapılandırma. Riskle ilgiliyse program başlamamalı."""


class StorageError(TradingBotError):
    """SQLite/dosya yazma-okuma hatası."""


class DataQualityError(TradingBotError):
    """Bayat, eksik, tutarsız veri — Coin Head NO_TRADE_DATA_INVALID döndürür."""


class RiskBlockedError(TradingBotError):
    """Global Risk Engine veya kill switch işlemi engelledi."""


class ExecutionDisabledError(TradingBotError):
    """Gerçek emir yolu kapalı (PAPER varsayılan; LIVE guard eksik)."""


class LLMSchemaError(TradingBotError):
    """LLM yanıtı şemaya uymuyor (fail-closed)."""
