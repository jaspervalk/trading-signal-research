"""Centralised configuration. Reads .env + configs/settings.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class TranscriptSettings(BaseModel):
    fetch_delay_seconds: float = 2.5
    fetch_jitter_seconds: float = 1.0
    whisper_backend: str = "faster_whisper"  # 'none' | 'faster_whisper' | 'openai_api'
    whisper_model: str = "base.en"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    audio_cache_dir: str = "data/cache/audio"


class BacktestSettings(BaseModel):
    primary_horizon: str = "5d"
    horizons: list[str] = Field(default_factory=lambda: ["1d", "3d", "5d", "21d"])
    round_trip_cost_bps: int = 10
    trigger_window_days: int = 10
    market_calendar: str = "XNYS"


class ExtractionSettings(BaseModel):
    min_final_confidence: float = 0.5
    llm_context_seconds_before: int = 90
    llm_context_seconds_after: int = 90
    extractor_version: str = "v0.1"


class UniverseSettings(BaseModel):
    ticker_csv_path: str = "configs/universe.csv"
    enforce_membership: bool = True


class ScoringSettings(BaseModel):
    windows: list[str] = Field(default_factory=lambda: ["all", "365d", "90d"])
    min_n_for_scoring: int = 5


class WalkForwardSettings(BaseModel):
    """Walk-forward strategy backtest defaults (ADR 0007)."""

    t2_cutoff: str = "2026-01-01T00:00:00+00:00"
    min_n_trades: int = 30
    default_rebalance: str = "weekly"  # 'daily' | 'weekly' | 'on_signal'
    cost_bps_grid: list[int] = Field(default_factory=lambda: [5, 10, 25, 50])
    benchmark_ticker: str = "SPY"


class DeepResearchSettings(BaseModel):
    """Deep-mode (multi-agent) entry/exit research tunables."""

    cross_lens_round: bool = False  # Phase 2: round-2 debate; cost +~$0.02/run


class ResearchSettings(BaseModel):
    """Entry/exit research feature settings."""

    deep: DeepResearchSettings = DeepResearchSettings()


class ProjectSettings(BaseModel):
    transcript: TranscriptSettings = TranscriptSettings()
    backtest: BacktestSettings = BacktestSettings()
    extraction: ExtractionSettings = ExtractionSettings()
    universe: UniverseSettings = UniverseSettings()
    scoring: ScoringSettings = ScoringSettings()
    walkforward: WalkForwardSettings = WalkForwardSettings()
    research: ResearchSettings = ResearchSettings()


class Env(BaseSettings):
    """Environment-driven settings (secrets, infra)."""

    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    openai_api_key: str = ""    # used by openai_api whisper backend
    youtube_api_key: str = ""
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'tsr.sqlite'}"
    log_level: str = "INFO"
    env: str = "dev"


@lru_cache(maxsize=1)
def load_project_settings(path: Path | None = None) -> ProjectSettings:
    settings_path = path or (REPO_ROOT / "configs" / "settings.yaml")
    if not settings_path.exists():
        return ProjectSettings()
    with settings_path.open() as f:
        data = yaml.safe_load(f) or {}
    return ProjectSettings(**data)


@lru_cache(maxsize=1)
def load_env() -> Env:
    return Env()
