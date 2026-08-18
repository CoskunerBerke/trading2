"""Salt-okunur web paneli (FastAPI + Plotly, CDN yok). `create_app(state_dir, data_dir, vault_dir, cfg)`."""
from .candles import CandleSource, build_candle_payload
from .config import DashboardConfig
from .state import STATE_FILES, StateReader

__all__ = ["DashboardConfig", "StateReader", "STATE_FILES", "CandleSource", "build_candle_payload", "create_app", "run_dashboard"]


def create_app(*args, **kwargs):  # tembel: fastapi yalnızca panel başlatılınca import edilir
    from .app import create_app as _create
    return _create(*args, **kwargs)


def run_dashboard(*args, **kwargs):
    from .app import run_dashboard as _run
    return _run(*args, **kwargs)
