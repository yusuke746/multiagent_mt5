"""ロギング設定。

フォーマット: [YYYY-MM-DD HH:MM:SS] [LEVEL] [MODULE] MESSAGE
コンソールと logs/ 配下の日次ファイルへ同時出力する。
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_CONFIGURED = False


class _Formatter(logging.Formatter):
    """設計書 §8 のフォーマットに合わせる。MODULE 名は固定幅で揃える。"""

    _LEVEL_ALIAS = {"WARNING": "WARN"}

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        module = record.name.upper()
        level = self._LEVEL_ALIAS.get(record.levelname, record.levelname)
        return f"[{ts}] [{level:<5}] [{module:<15}] {record.getMessage()}"


def setup_logging(log_dir: str | None = None, level: int = logging.INFO) -> None:
    """ルートロガーを一度だけ設定する。scheduler 起動時に呼ぶ。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = log_dir or os.getenv("LOG_DIR", "./logs")
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logfile = Path(log_dir) / f"trading_{datetime.now():%Y-%m-%d}.log"

    formatter = _Formatter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(logfile, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(module: str) -> logging.Logger:
    """モジュール別ロガーを返す。setup_logging 未実行でも安全に動くよう保証する。"""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(module)
