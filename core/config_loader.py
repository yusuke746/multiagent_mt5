"""設定ローダー。

- config/symbols.yaml … 銘柄マスタ（ticker ↔ mt5_symbol、volume_min/step、sector）
- config/risk.yaml    … リスク・シグナル・実行・TradingAgents 設定
- .env                … 認証情報・パス（python-dotenv 経由）

symbols.yaml / risk.yaml は起動時に一度だけ読み込み、以降キャッシュを返す。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parent.parent  # multiagent_mt5/
_CONFIG_DIR = _BASE_DIR / "config"

# .env は import 時に一度だけ読み込む
load_dotenv(_BASE_DIR / ".env")


@dataclass(frozen=True)
class SymbolSpec:
    ticker: str          # 例: "NVDA"（TradingAgents へ渡す識別子）
    mt5_symbol: str      # 例: "Nvidia"（MT5 実シンボル名＝会社名）
    sector: str
    sub_industry: str
    volume_min: float
    volume_step: float
    analysis_interval_days: int


@lru_cache(maxsize=1)
def load_symbols() -> list[SymbolSpec]:
    with open(_CONFIG_DIR / "symbols.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    specs = []
    for item in raw["symbols"]:
        specs.append(
            SymbolSpec(
                ticker=item["ticker"],
                mt5_symbol=item["mt5_symbol"],
                sector=item["sector"],
                sub_industry=item.get("sub_industry", ""),
                volume_min=float(item["volume_min"]),
                volume_step=float(item["volume_step"]),
                analysis_interval_days=int(item.get("analysis_interval_days", 3)),
            )
        )
    return specs


@lru_cache(maxsize=1)
def load_risk() -> dict[str, Any]:
    with open(_CONFIG_DIR / "risk.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# --- ルックアップヘルパー -------------------------------------------------

@lru_cache(maxsize=1)
def _by_ticker() -> dict[str, SymbolSpec]:
    return {s.ticker: s for s in load_symbols()}


@lru_cache(maxsize=1)
def _by_mt5() -> dict[str, SymbolSpec]:
    return {s.mt5_symbol: s for s in load_symbols()}


def spec_for_ticker(ticker: str) -> SymbolSpec | None:
    return _by_ticker().get(ticker)


def spec_for_mt5(mt5_symbol: str) -> SymbolSpec | None:
    return _by_mt5().get(mt5_symbol)


def all_tickers() -> list[str]:
    return [s.ticker for s in load_symbols()]


# --- .env アクセサ --------------------------------------------------------

def env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)


def mt5_credentials() -> dict[str, Any]:
    """MT5 接続情報を返す。login は int へ変換する。"""
    login = env("MT5_LOGIN")
    return {
        "path": env("MT5_PATH"),
        "login": int(login) if login and login.isdigit() else None,
        "password": env("MT5_PASSWORD"),
        "server": env("MT5_SERVER"),
    }
