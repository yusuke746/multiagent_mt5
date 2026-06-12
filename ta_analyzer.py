"""TradingAgents 統合モジュール

TradingAgentsGraph をラップして:
  1. H1サイクルで各銘柄の方向シグナル (BUY / SELL / NEUTRAL) を生成
  2. 結果をスレッドセーフにキャッシュ
  3. M15サイクルからキャッシュを参照してエントリー判断に使用

TradingAgents は研究用フレームワークのため、出力は非決定的。
レートとタイムゾーンの差異も存在するが、長期バイアス判断には十分有効。
"""

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class TASignal:
    """TradingAgents が返した分析シグナル"""
    direction:     str       # "BUY" | "SELL" | "NEUTRAL"
    rating:        str       # "Buy" | "Overweight" | "Hold" | "Underweight" | "Sell"
    reasoning:     str       # 分析サマリー (PortfolioManager の final_trade_decision)
    yf_ticker:     str       # 分析に使用した yfinance ティッカー
    trade_date:    str       # 分析日 (YYYY-MM-DD)
    timestamp:     datetime  = field(default_factory=datetime.now)
    analysts_used: list[str] = field(default_factory=list)

    def is_fresh(self, max_age_hours: float) -> bool:
        age_h = (datetime.now() - self.timestamp).total_seconds() / 3600
        return age_h <= max_age_hours

    def is_directional(self) -> bool:
        return self.direction in ("BUY", "SELL")


class TAAnalyzer:
    """TradingAgents の分析グラフをラップするシングルトン的ラッパー。

    スレッドセーフなキャッシュを持ち、複数銘柄の並列分析に対応する。
    初期化コストが高いため、インスタンスは1つだけ作成して使い回す。
    """

    def __init__(self):
        self._lock  = threading.Lock()
        self._cache: dict[str, TASignal] = {}
        self._graph = None
        self._analysts_selected: list[str] = []
        self._init_graph()

    # ── グラフ初期化 ────────────────────────

    def _init_graph(self) -> None:
        """TradingAgentsGraph を初期化する。インポートエラー時は警告のみ。"""
        try:
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            from tradingagents.default_config import DEFAULT_CONFIG
        except ImportError as e:
            logger.error(
                "[TA] TradingAgents がインストールされていません: %s\n"
                "  pip install tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git",
                e,
            )
            return

        # アナリスト選択 (.envで各々 ON/OFF)
        analysts = []
        if config.TA_USE_MARKET:
            analysts.append("market")
        if config.TA_USE_SOCIAL:
            analysts.append("social")
        if config.TA_USE_NEWS:
            analysts.append("news")
        if config.TA_USE_FUNDAMENTALS:
            analysts.append("fundamentals")

        if not analysts:
            logger.warning("[TA] 全アナリストが無効になっています。少なくとも market を有効にしてください。")
            analysts = ["market"]

        self._analysts_selected = analysts

        ta_cfg = DEFAULT_CONFIG.copy()
        ta_cfg["llm_provider"]          = "openai"
        ta_cfg["deep_think_llm"]        = config.TA_DEEP_MODEL
        ta_cfg["quick_think_llm"]       = config.TA_QUICK_MODEL
        ta_cfg["max_debate_rounds"]     = config.TA_MAX_DEBATE_ROUNDS
        ta_cfg["max_risk_discuss_rounds"] = config.TA_MAX_RISK_ROUNDS
        ta_cfg["output_language"]       = "Japanese"  # 日本語レポートを要求
        ta_cfg["checkpoint_enabled"]    = False       # ライブ運用では不要

        # OpenAI APIキーを環境変数に設定 (TradingAgents は os.environ を参照)
        if config.OPENAI_API_KEY:
            os.environ.setdefault("OPENAI_API_KEY", config.OPENAI_API_KEY)

        try:
            self._graph = TradingAgentsGraph(
                selected_analysts=analysts,
                config=ta_cfg,
                debug=False,
            )
            logger.info(
                "[TA] TradingAgentsGraph 初期化完了: analysts=%s deep=%s quick=%s",
                analysts, config.TA_DEEP_MODEL, config.TA_QUICK_MODEL,
            )
        except Exception as e:
            logger.error("[TA] TradingAgentsGraph 初期化失敗: %s", e, exc_info=True)
            self._graph = None

    # ── 分析実行 ────────────────────────────

    def run_analysis(
        self,
        symbol:     str,
        yf_ticker:  str,
        trade_date: str,
        asset_type: str = "stock",
    ) -> Optional[TASignal]:
        """TradingAgents を実行して結果をキャッシュに書き込む。

        Args:
            symbol:     MT5シンボル名 (ログ・キャッシュキー用)
            yf_ticker:  yfinance ティッカー (例: "GC=F")
            trade_date: 分析基準日 "YYYY-MM-DD" (今日の日付を渡す)
            asset_type: "stock" または "crypto"

        Returns:
            TASignal (成功時) または None (失敗時)
        """
        if self._graph is None:
            logger.error("[TA] グラフが初期化されていないため分析をスキップします: %s", symbol)
            return None

        logger.info("[TA] %s (%s) 分析開始 date=%s analysts=%s ...",
                    symbol, yf_ticker, trade_date, self._analysts_selected)

        for attempt in range(1, config.TA_MAX_RETRY + 1):
            try:
                final_state, rating = self._graph.propagate(
                    yf_ticker,
                    trade_date,
                    asset_type=asset_type,
                )
                direction = self._map_rating(rating)
                reasoning = (
                    final_state.get("final_trade_decision", "")
                    if isinstance(final_state, dict) else str(final_state)
                )

                signal = TASignal(
                    direction=direction,
                    rating=str(rating),
                    reasoning=reasoning[:2000],
                    yf_ticker=yf_ticker,
                    trade_date=trade_date,
                    analysts_used=list(self._analysts_selected),
                )

                with self._lock:
                    self._cache[symbol] = signal

                logger.info(
                    "[TA] %s → direction=%s rating=%s (attempt %d/%d)",
                    symbol, direction, rating, attempt, config.TA_MAX_RETRY,
                )
                return signal

            except Exception as e:
                logger.warning(
                    "[TA] %s 分析エラー (attempt %d/%d): %s",
                    symbol, attempt, config.TA_MAX_RETRY, e,
                )
                if attempt == config.TA_MAX_RETRY:
                    logger.error("[TA] %s: 最大リトライ達成 → スキップ", symbol, exc_info=True)

        return None

    # ── キャッシュ参照 ──────────────────────

    def get_cached_direction(self, symbol: str) -> str:
        """キャッシュ済みの方向シグナルを返す。

        Returns:
            "BUY" | "SELL" | "NEUTRAL"
            キャッシュ未存在・期限切れ時は "NEUTRAL"
        """
        with self._lock:
            signal = self._cache.get(symbol)
        if signal is None:
            return "NEUTRAL"
        if not signal.is_fresh(config.TA_SIGNAL_MAX_AGE_HOURS):
            logger.debug("[TA] %s: キャッシュ期限切れ → NEUTRAL", symbol)
            return "NEUTRAL"
        return signal.direction

    def get_cached_signal(self, symbol: str) -> Optional[TASignal]:
        """キャッシュ済みのシグナル全体を返す。None は未分析または期限切れ。"""
        with self._lock:
            signal = self._cache.get(symbol)
        if signal is None:
            return None
        if not signal.is_fresh(config.TA_SIGNAL_MAX_AGE_HOURS):
            return None
        return signal

    def clear_cache(self, symbol: str | None = None) -> None:
        """キャッシュをクリアする。symbol 指定時は該当銘柄のみ。"""
        with self._lock:
            if symbol:
                self._cache.pop(symbol, None)
            else:
                self._cache.clear()

    # ── ユーティリティ ──────────────────────

    @staticmethod
    def _map_rating(rating: str) -> str:
        """5段階レーティングを BUY / SELL / NEUTRAL に変換する。

        TradingAgents の SignalProcessor.process_signal() が返す文字列:
          "Buy" | "Overweight" | "Hold" | "Underweight" | "Sell"
        """
        r = str(rating).strip().lower()
        if any(kw in r for kw in ("buy", "overweight")):
            return "BUY"
        if any(kw in r for kw in ("sell", "underweight")):
            return "SELL"
        return "NEUTRAL"

    @property
    def is_available(self) -> bool:
        """TradingAgents が利用可能か (グラフが初期化済みか) を返す。"""
        return self._graph is not None
