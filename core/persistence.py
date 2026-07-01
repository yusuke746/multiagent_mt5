"""JSON 永続化ヘルパー（data/signal_history.json / data/position_meta.json）。

- signal_history.json … signal_filter 用。フリップフロップ防止履歴。
- position_meta.json  … MT5 から取得不能な initial_lots / scale_count 等の補足情報。
  キーは MT5 実シンボル名（会社名）。ヘッジ口座のため position/order 基準で保持する（§7）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from logger import get_logger

log = get_logger("persistence")

_BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (_BASE_DIR / p)


def _signal_history_path() -> Path:
    return _resolve(os.getenv("SIGNAL_HISTORY_PATH", "./data/signal_history.json"))


def _position_meta_path() -> Path:
    return _resolve(os.getenv("POSITION_META_PATH", "./data/position_meta.json"))


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read %s: %s. Using empty.", path.name, e)
        return {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)  # アトミックに置き換え


def load_signal_history() -> dict[str, list[dict[str, Any]]]:
    return _load(_signal_history_path())


def save_signal_history(data: dict[str, list[dict[str, Any]]]) -> None:
    _save(_signal_history_path(), data)


def load_position_meta() -> dict[str, Any]:
    return _load(_position_meta_path())


def save_position_meta(data: dict[str, Any]) -> None:
    _save(_position_meta_path(), data)
