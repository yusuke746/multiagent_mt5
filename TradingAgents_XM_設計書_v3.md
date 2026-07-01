# TradingAgents × XMTrading 自動売買システム 設計書 v3.0

> **この設計書の読み方**
> VSCode AgentなどのAIコーディングエージェントが実装ミスをしないよう、
> データ構造・ルール・境界条件・禁止事項を明示的に定義する。
> 曖昧な表現は一切使わない。迷ったらこの設計書に立ち返ること。

---

## 0. 全確定前提（変更禁止）

| 項目           | 決定内容                                                                    | 禁止事項                                 |
| -------------- | --------------------------------------------------------------------------- | ---------------------------------------- |
| 取引対象       | 米国株CFD（XMTrading提供）                                                  | FXメジャーペアへの適用                   |
| LLM活用方針    | 分析・判断シグナルの生成のみ                                                | LLMに直接発注権限を持たせること          |
| 保有期間管理   | 方式1：定期再評価。保有期間をLLMに予測させない                              | horizon予測の実装                        |
| スケーリング   | 含み益方向のみ買い増し許可                                                  | ナンピン（含み損中の買い増し）の実装     |
| 利益確定（TP） | **固定TPは一切使用しない**                                            | tp値を指定した発注                       |
| 出口判断       | **LLMがCLOSE/DECREASEを出すか、トレーリングSLが刈り取るまで保有継続** | 固定日数・固定価格での機械的利確         |
| リスク管理     | **トレーリングSL（日次更新・有利方向のみ）が唯一の強制出口**          | SLを不利方向に動かす更新                 |
| 実行環境       | Python on Windows（MT5ターミナルが動く環境）                                | GASでの実装                              |
| 運用フェーズ   | デモ口座での検証から開始                                                    | Phase5より前のリアル口座接続コードの実装 |
| 監視銘柄数     | 15銘柄固定                                                                  | 初期実装で15を超えること                 |
| 最大保有数     | 同時6銘柄（システムが自動拒否）                                             | 6を超える発注の通過                      |

---

## 1. システム全体構成

### 1.1 ディレクトリ構成

```
xm-tradingagents-bot/
├── .env
├── config/
│   ├── symbols.yaml
│   └── risk.yaml
├── core/
│   ├── mt5_client.py             # MT5接続・切断・ヘルスチェック
│   ├── market_data.py            # MT5から価格・ATRデータ取得
│   ├── portfolio_state.py        # 保有ポジション状態の管理（最重要）
│   ├── screener.py               # 一次スクリーニング（軽量）
│   ├── tradingagents_runner.py   # TradingAgentsGraph呼び出し（最重要）
│   ├── signal_filter.py          # フリップフロップ防止フィルタ
│   ├── risk_manager.py           # ロット計算・SL計算・トレーリングSL更新
│   ├── order_executor.py         # MT5への発注・SL更新（TRADE_ACTION_SLTP）
│   └── account_guard.py          # 口座レベルサーキットブレーカー
├── data/
│   ├── signal_history.json       # フリップフロップ防止用履歴（自動生成）
│   └── position_meta.json        # MT5から取得できないポジション補足情報
├── logs/
├── scheduler.py
├── logger.py
└── notifier.py
```

### 1.2 データフロー（1サイクルの処理順序）

**以下の順序を絶対に守ること。順序を変えるとリスク管理が機能しなくなる。**

```
[scheduler.py 起動（日本時間 毎日早朝6:00）]
      ↓
[1] account_guard.check()
      → NG（日次/週次損失リミット超過）なら即終了（発注なし）
      ↓
[2] portfolio_state.sync_from_mt5()
      → MT5から現在の保有ポジションを取得・ローカルに同期
      ↓
[3] 【v3.0追加】保有ポジションの日次トレーリングSL更新
      risk_manager.update_trailing_sl(各保有ポジション)
      → 有利方向のみSLを更新。不利方向には絶対に動かさない
      → order_executor.update_sl_only(TRADE_ACTION_SLTP) で反映
      ↓
[4] screener.run(全15銘柄)
      → 軽量スクリーニングで明らかに動きのない銘柄を除外
      → 保有中の銘柄はスクリーニング除外対象外（必ず分析する）
      ↓
[5] tradingagents_runner.run_parallel(分析対象リスト)
      → 保有ポジション情報をプロンプトに注入
      → Exponential Backoffつき並列実行
      ↓
[6] signal_filter.apply(各銘柄のシグナル)
      → フリップフロップ防止フィルタ適用（signal_history.json を参照）
      → 有効シグナルのみ通過
      ↓
[7] 各シグナルに対してアクション実行
      BUY      → risk_manager.calculate_initial_lot() → order_executor.open_position()
                  ※TP指定なし・SLのみ設定
      INCREASE → risk_manager.can_scale_in() チェック → order_executor.add_position()
                  ※買い増し後SLを平均取得単価ベースで再計算
      DECREASE → order_executor.partial_close() （保有ロットの50%を決済）
      CLOSE    → order_executor.close_position() （全決済）
      HOLD     → 何もしない（ステップ[3]でSLが既に更新済み）
      ↓
[8] logger.write() / notifier.send()
```

---

## 2. 設定ファイル仕様

### 2.1 config/symbols.yaml

```yaml
# XMTradingの実際のシンボル名はmt5.symbols_get()で必ず確認してから設定すること

symbols:
  - ticker: "AAPL"
    mt5_symbol: "AAPLm"
    sector: "technology"
    analysis_interval_days: 3   # 保有中は毎日に自動上書き

  - ticker: "MSFT"
    mt5_symbol: "MSFTm"
    sector: "technology"
    analysis_interval_days: 3

  - ticker: "GOOGL"
    mt5_symbol: "GOOGLm"
    sector: "technology"
    analysis_interval_days: 3

  - ticker: "AMZN"
    mt5_symbol: "AMZNm"
    sector: "consumer_discretionary"
    analysis_interval_days: 3

  - ticker: "NVDA"
    mt5_symbol: "NVDAm"
    sector: "technology"
    analysis_interval_days: 3

  - ticker: "META"
    mt5_symbol: "METAm"
    sector: "technology"
    analysis_interval_days: 3

  - ticker: "TSLA"
    mt5_symbol: "TSLAm"
    sector: "consumer_discretionary"
    analysis_interval_days: 3

  - ticker: "JPM"
    mt5_symbol: "JPMm"
    sector: "financials"
    analysis_interval_days: 3

  - ticker: "JNJ"
    mt5_symbol: "JNJm"
    sector: "healthcare"
    analysis_interval_days: 3

  - ticker: "V"
    mt5_symbol: "Vm"
    sector: "financials"
    analysis_interval_days: 3

  - ticker: "WMT"
    mt5_symbol: "WMTm"
    sector: "consumer_staples"
    analysis_interval_days: 3

  - ticker: "XOM"
    mt5_symbol: "XOMm"
    sector: "energy"
    analysis_interval_days: 3

  - ticker: "UNH"
    mt5_symbol: "UNHm"
    sector: "healthcare"
    analysis_interval_days: 3

  - ticker: "PG"
    mt5_symbol: "PGm"
    sector: "consumer_staples"
    analysis_interval_days: 3

  - ticker: "MA"
    mt5_symbol: "MAm"
    sector: "financials"
    analysis_interval_days: 3
```

### 2.2 config/risk.yaml

```yaml
# ==============================
# ポートフォリオ全体の制約
# ==============================
portfolio:
  max_positions: 6
  max_positions_per_sector: 2
  max_risk_per_trade_pct: 1.5       # 1トレードの最大損失（口座残高に対する%）
  max_total_risk_pct: 9.0           # 全ポジション合計の最大損失（口座残高に対する%）

# ==============================
# ストップロス設定
# ==============================
stop_loss:
  atr_period: 14
  atr_multiplier: 2.0
  sl_required: true                 # SLなし発注はorder_executorでエラー拒否

# ==============================
# テイクプロフィット設定
# ==============================
take_profit:
  tp_required: false                # 固定TPは一切使用しない
  # tp値を指定した発注コードを書いてはいけない

# ==============================
# トレーリングSL設定（v3.0追加）
# ==============================
trailing:
  enabled: true
  update_on_hold: true              # HOLD判定時でも日次でSLを更新する
  # 新SLの計算式: 現在価格 ± (ATR × atr_multiplier)
  # BUYポジション: new_sl = current_price - (ATR × atr_multiplier)
  # 更新条件: new_sl > current_sl の場合のみ更新（有利方向のみ）
  # 禁止: new_sl < current_sl の場合の更新（絶対に行わない）

# ==============================
# スケーリング（買い増し）ルール
# ==============================
scaling:
  allow_scale_in: true
  require_unrealized_profit: true   # 含み益がある場合のみ許可
  max_total_multiplier: 2.5         # 初回ロットの最大2.5倍まで
  scale_in_ratio: 0.6               # 買い増しロット = 前回追加ロット × 0.6（逓減）
  recalculate_sl_on_scale: true     # 買い増し後SLを平均取得単価ベースで再計算

# ==============================
# サーキットブレーカー
# ==============================
circuit_breaker:
  daily_loss_limit_pct: 3.0
  weekly_loss_limit_pct: 6.0
  min_margin_level_pct: 300.0

# ==============================
# フリップフロップ防止
# ==============================
signal_filter:
  min_confidence: 0.6
  direction_change_consecutive_required: 2

# ==============================
# API実行制御（v3.0追加）
# ==============================
execution:
  max_parallel_analysis: 4
  # Exponential Backoff設定（Tenacityライブラリ使用）
  retry_enabled: true
  retry_max_attempts: 5
  retry_wait_min_seconds: 4         # 初回待機時間
  retry_wait_max_seconds: 60        # 最大待機時間
  retry_multiplier: 2               # 待機時間の指数倍率
```

---

## 3. 核心モジュールの仕様

### 3.1 portfolio_state.py（最重要）

#### なぜこのモジュールが最重要か

TradingAgentsは標準では「今どの銘柄をどれだけ保有しているか」を知らない。
`propagate(ticker, date)` はポートフォリオ状態を一切考慮しない。
このモジュールがMT5から保有状態を取得し、TradingAgentsのプロンプトに
渡せる形式に変換する責務を持つ。

これが正しく実装されていない場合に起きる事故：

- 保有中なのに新規BUYシグナルで二重発注する
- ポートフォリオが満杯なのに新規BUYを推奨する
- 含み損中の銘柄への買い増しをLLMが推奨する（ナンピン事故）

#### データ構造

```python
from dataclasses import dataclass, field

@dataclass
class PositionInfo:
    ticker: str               # 例: "AAPL"（TradingAgentsに渡す名前）
    mt5_symbol: str           # 例: "AAPLm"（MT5に渡す名前）
    direction: str            # "BUY" のみ（v3.0はロングのみ対応）
    total_lots: float         # 現在の総保有ロット数
    avg_entry_price: float    # 平均取得単価（買い増し後に再計算済み）
    current_sl: float         # 現在MT5に設定中のSL価格
    # current_tp は存在しない（v3.0はTPなし設計）
    unrealized_pnl: float     # 現在の含み損益（口座通貨額）
    initial_lots: float       # 最初のエントリー時のロット数
    scale_count: int          # 買い増し実施回数（0=初回のみ）
    entry_date: str           # 最初のエントリー日（YYYY-MM-DD）

@dataclass
class PortfolioState:
    positions: list[PositionInfo] = field(default_factory=list)
    account_balance: float = 0.0
    account_equity: float = 0.0
    margin_level_pct: float = 0.0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    snapshot_time: str = ""

    def get_position(self, ticker: str) -> "PositionInfo | None":
        return next((p for p in self.positions if p.ticker == ticker), None)

    def available_slots(self) -> int:
        return 6 - len(self.positions)  # max_positions=6 はrisk.yamlから読む

    def to_llm_context(self) -> str:
        """TradingAgentsのプロンプトに注入するテキストを生成する"""
        lines = ["=== CURRENT PORTFOLIO STATUS ==="]
        if not self.positions:
            lines.append("No positions currently held.")
        else:
            for p in self.positions:
                lines.append(
                    f"- {p.ticker}: {p.direction} {p.total_lots} lots "
                    f"@ avg ${p.avg_entry_price:.2f}, "
                    f"unrealized P&L: ${p.unrealized_pnl:+.2f}, "
                    f"SL: ${p.current_sl:.2f}, "
                    f"scale-ins done: {p.scale_count}"
                )
        lines.append(f"Available new position slots: {self.available_slots()}/6")
        lines.append(f"Today's P&L: ${self.daily_pnl:+.2f}")
        lines.append(f"Weekly P&L: ${self.weekly_pnl:+.2f}")
        lines.append("=================================")
        return "\n".join(lines)
```

#### sync_from_mt5() の実装要件

```python
def sync_from_mt5() -> PortfolioState:
    """
    MT5から現在のポジション情報を取得してPortfolioStateを構築する。

    実装上の注意：
    - mt5.positions_get() で全ポジションを取得する
    - XMシンボル名（例：AAPLm）→ ticker名（AAPL）への変換は
      symbols.yaml のマッピングテーブルを参照する
    - initial_lots と scale_count はMT5から取得できないため、
      data/position_meta.json から読み込む
    - position_meta.json に存在しないポジションは
      initial_lots=現在のtotal_lots, scale_count=0 として扱い、
      WARNログを出力する
    - 取得失敗時は例外をraiseし、scheduler.pyでキャッチして
      その日の処理をスキップする（中途半端に処理しない）
    """
    pass
```

---

### 3.2 tradingagents_runner.py（v3.0更新）

#### TradingAgentsの設定

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import asyncio

TA_CONFIG = DEFAULT_CONFIG.copy()
TA_CONFIG["llm_provider"] = "openai"
TA_CONFIG["deep_think_llm"] = "gpt-5.4"
TA_CONFIG["quick_think_llm"] = "gpt-5.4-mini"
TA_CONFIG["max_debate_rounds"] = 1
TA_CONFIG["checkpoint_enabled"] = True  # LangGraphの再開機能を有効化
```

#### ポートフォリオ状態の注入

```python
def build_ticker_context(portfolio_state: PortfolioState, ticker: str) -> str:
    """
    1銘柄分のプロンプト注入テキストを生成する。
    保有中か否かで指示内容を変える。
    """
    base_context = portfolio_state.to_llm_context()
    position = portfolio_state.get_position(ticker)

    if position:
        ticker_context = (
            f"\n=== POSITION STATUS FOR {ticker} ===\n"
            f"Status: CURRENTLY HOLDING\n"
            f"Direction: {position.direction}\n"
            f"Total lots: {position.total_lots} (initial: {position.initial_lots})\n"
            f"Average entry price: ${position.avg_entry_price:.2f}\n"
            f"Current SL: ${position.current_sl:.2f}\n"
            f"Unrealized P&L: ${position.unrealized_pnl:+.2f}\n"
            f"Scale-ins done: {position.scale_count}\n"
            f"\nAvailable actions for this position:\n"
            f"- INCREASE: Add to the position (only if outlook has improved AND unrealized P&L > 0)\n"
            f"- DECREASE: Partially close to lock in some profit\n"
            f"- HOLD: Maintain current position\n"
            f"- CLOSE: Fully exit the position\n"
            f"DO NOT output BUY or SELL for a position you already hold.\n"
            f"=================================="
        )
    else:
        slots = portfolio_state.available_slots()
        if slots <= 0:
            ticker_context = (
                f"\n=== POSITION STATUS FOR {ticker} ===\n"
                f"Status: NOT HOLDING\n"
                f"IMPORTANT: Portfolio is FULL (6/6). "
                f"Output HOLD only. DO NOT recommend BUY.\n"
                f"=================================="
            )
        else:
            ticker_context = (
                f"\n=== POSITION STATUS FOR {ticker} ===\n"
                f"Status: NOT HOLDING\n"
                f"Available slots: {slots}/6\n"
                f"Available actions: BUY (new entry) or HOLD (wait)\n"
                f"=================================="
            )

    return base_context + ticker_context
```

#### parse_decision（v3.0最重要追加：BUY/SELL読み替えロジック）

```python
def parse_decision(
    raw_decision,
    ticker: str,
    portfolio_state: PortfolioState
) -> dict:
    """
    TradingAgentsの出力を統一フォーマットに変換する。

    【v3.0追加：保有状態に応じたアクション強制変換ルール】
    TradingAgentsは保有中の銘柄に対しても BUY/SELL/HOLD で返すことがある。
    以下のルールで強制変換する：

    保有中の銘柄に対して：
      LLMが BUY  → INCREASE に変換
        （理由：既に保有中のためBUYは新規注文になってしまう。意図はポジション追加）
      LLMが SELL → CLOSE に変換
        （理由：保有中に対するSELLは決済の意味。ショートポジションは開かない）

    未保有の銘柄に対して：
      LLMが INCREASE/DECREASE/CLOSE → HOLD に変換
        （理由：保有していないのにこれらのアクションは実行不能）
      LLMが SELL → HOLD に変換
        （理由：v3.0はロングのみ対応。ショート開始を禁止）

    パースエラー時は必ずHOLDを返す（フェイルセーフ）。

    返り値の形式（必ずこの構造にすること）:
    {
        "action": str,       # "BUY"/"HOLD"/"INCREASE"/"DECREASE"/"CLOSE" のいずれか
        "confidence": float, # 0.0〜1.0
        "rationale": str,
        "raw": any,
        "converted_from": str | None  # 変換が行われた場合に元のアクションを記録
    }
    """
    VALID_FINAL_ACTIONS = {"BUY", "HOLD", "INCREASE", "DECREASE", "CLOSE"}

    try:
        if isinstance(raw_decision, dict):
            action = str(raw_decision.get("action", "HOLD")).upper().strip()
            confidence = float(raw_decision.get("confidence", 0.0))
            rationale = str(raw_decision.get("rationale", "No rationale"))
        else:
            action = str(raw_decision).upper().strip()
            confidence = 0.5
            rationale = str(raw_decision)

        confidence = max(0.0, min(1.0, confidence))
        original_action = action
        is_holding = portfolio_state.get_position(ticker) is not None
        converted_from = None

        if is_holding:
            # 保有中：BUY→INCREASE, SELL→CLOSE に強制変換
            if action == "BUY":
                action = "INCREASE"
                converted_from = original_action
            elif action == "SELL":
                action = "CLOSE"
                converted_from = original_action
        else:
            # 未保有：INCREASE/DECREASE/CLOSE/SELL → HOLD に強制変換
            if action in {"INCREASE", "DECREASE", "CLOSE", "SELL"}:
                action = "HOLD"
                converted_from = original_action

        # 最終的に有効なアクションでなければHOLDにフォールバック
        if action not in VALID_FINAL_ACTIONS:
            action = "HOLD"
            converted_from = original_action

        return {
            "action": action,
            "confidence": confidence,
            "rationale": rationale,
            "raw": raw_decision,
            "converted_from": converted_from
        }

    except Exception as e:
        return {
            "action": "HOLD",
            "confidence": 0.0,
            "rationale": f"PARSE ERROR - defaulting to HOLD: {str(e)}",
            "raw": raw_decision,
            "converted_from": None
        }
```

#### Exponential Backoff つき実行（v3.0追加）

```python
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

@retry(
    retry=retry_if_exception_type(Exception),  # RateLimitError等を含む全APIエラー
    wait=wait_exponential(multiplier=2, min=4, max=60),  # 4秒→8秒→16秒→...最大60秒
    stop=stop_after_attempt(5),
    reraise=True  # 5回失敗したら例外をraiseして呼び出し元でHOLDにフォールバック
)
async def _call_trading_agents(ta: TradingAgentsGraph, ticker: str, date_str: str):
    """Tenacityによるリトライラッパー。直接呼ばずrun_single経由で使うこと。"""
    return ta.propagate(ticker, date_str)


async def run_single(ticker: str, date_str: str, portfolio_state: PortfolioState) -> dict:
    """1銘柄の分析を非同期で実行する。エラー時は必ずHOLDを返す。"""
    try:
        config = TA_CONFIG.copy()
        config["portfolio_context"] = build_ticker_context(portfolio_state, ticker)
        ta = TradingAgentsGraph(debug=False, config=config)
        _, raw_decision = await _call_trading_agents(ta, ticker, date_str)
        decision = parse_decision(raw_decision, ticker, portfolio_state)
        return {"ticker": ticker, "decision": decision, "error": None}

    except Exception as e:
        return {
            "ticker": ticker,
            "decision": {
                "action": "HOLD",
                "confidence": 0.0,
                "rationale": f"Runner failed after retries: {str(e)}",
                "raw": None,
                "converted_from": None
            },
            "error": str(e)
        }


async def run_parallel(
    tickers: list[str],
    date_str: str,
    portfolio_state: PortfolioState,
    max_parallel: int = 4  # risk.yamlのexecution.max_parallel_analysisから読む
) -> list[dict]:
    """複数銘柄を並列実行する。max_parallelでAPIレート制限を回避する。"""
    semaphore = asyncio.Semaphore(max_parallel)

    async def run_with_semaphore(ticker):
        async with semaphore:
            return await run_single(ticker, date_str, portfolio_state)

    return await asyncio.gather(*[run_with_semaphore(t) for t in tickers])
```

---

### 3.3 risk_manager.py（v3.0更新）

#### update_trailing_sl（v3.0最重要追加）

```python
def update_trailing_sl(
    position_info: PositionInfo,
    current_price: float,
    atr_value: float,
    atr_multiplier: float = 2.0
) -> float | None:
    """
    トレーリングSLの新しい価格を計算して返す。

    【最重要フェイルセーフ】
    新SLが現SLより「有利な方向」に動く場合のみ更新値を返す。
    不利な方向への更新は絶対に行わない。

    BUYポジションの場合：
      new_sl = current_price - (atr_value × atr_multiplier)
      返す条件: new_sl > position_info.current_sl のみ
      禁止: new_sl <= position_info.current_sl の場合はNoneを返す（更新しない）

    SELLポジションの場合：
      v3.0はロングのみのためSELLポジションは存在しない。
      SELLポジションの引数が来た場合はValueErrorをraiseする。

    返り値：
      float: 新しいSL価格（更新すべき場合）
      None:  更新不要（現SLより不利、または変化なし）
    """
    if position_info.direction != "BUY":
        raise ValueError(f"v3.0 supports BUY positions only. Got: {position_info.direction}")

    new_sl = current_price - (atr_value * atr_multiplier)

    if new_sl > position_info.current_sl:
        return new_sl  # 有利方向への移動 → 更新
    else:
        return None    # 不利または変化なし → 更新しない
```

#### calculate_initial_lot

```python
def calculate_initial_lot(
    entry_price: float,
    sl_price: float,
    account_balance: float,
    lot_value_per_unit: float,
    max_risk_pct: float = 1.5
) -> float:
    """
    新規エントリー時のロット数を計算する。

    計算式：
      許容損失額 = account_balance × max_risk_pct / 100
      価格リスク = abs(entry_price - sl_price)
      lot_size   = 許容損失額 / (価格リスク × lot_value_per_unit)

    結果はXMの最小ロット単位（0.01）に切り捨てる。
    計算結果が0.01未満の場合は0を返す（発注しない）。

    TPは計算しない（v3.0はTPなし設計）。
    """
    pass


def calculate_scale_in_lot(
    position_info: PositionInfo,
    scale_in_ratio: float = 0.6,
    max_total_multiplier: float = 2.5
) -> float:
    """
    買い増し時のロット数を計算する。

    計算式（逓減方式）：
      scale_count == 0（初回買い増し）: 追加ロット = initial_lots × scale_in_ratio
      scale_count >= 1（2回目以降）:  追加ロット = 前回追加ロット × scale_in_ratio

    前回追加ロットはposition_meta.jsonのscale_historyから取得する。
    scale_historyが空の場合（初回買い増し）: initial_lots を使う。

    最大倍率チェック:
      (total_lots + 追加ロット) > initial_lots × max_total_multiplier の場合、
      追加ロットを (initial_lots × max_total_multiplier - total_lots) に圧縮する。
      圧縮後が0.01未満なら0を返す（これ以上買い増し不可）。
    """
    pass


def recalculate_sl_after_scale(
    position_info: PositionInfo,
    new_entry_price: float,
    new_lots: float,
    atr_value: float,
    atr_multiplier: float = 2.0
) -> float:
    """
    買い増し後の平均取得単価ベースでSLを再計算する。

    手順：
    1. 新しい平均取得単価を算出：
       new_avg = (position_info.total_lots × position_info.avg_entry_price
                  + new_lots × new_entry_price)
                 / (position_info.total_lots + new_lots)
    2. 新SL = new_avg - (atr_value × atr_multiplier)

    フェイルセーフ：
    新SLが現SLより不利な方向（lower）にならないことを確認する。
    もし新SLが現SLより低い場合は、現SL（position_info.current_sl）をそのまま返す。
    """
    pass


def can_scale_in(
    position_info: PositionInfo,
    account_balance: float,
    all_positions: list[PositionInfo],
    max_risk_pct: float = 1.5,
    max_total_risk_pct: float = 9.0,
    max_total_multiplier: float = 2.5
) -> tuple[bool, str]:
    """
    買い増しの可否を判定する。Falseの場合は理由文字列も返す。

    拒否条件（1つでも当てはまればFalse）：
    1. unrealized_pnl <= 0
       → (False, "Cannot scale in: position has no unrealized profit")
    2. total_lots >= initial_lots × max_total_multiplier
       → (False, "Cannot scale in: maximum multiplier (2.5x) reached")
    3. 買い増し後の全ポジション合計リスクが max_total_risk_pct を超える
       → (False, "Cannot scale in: would exceed max total portfolio risk")
    """
    pass
```

---

### 3.4 order_executor.py（v3.0更新）

#### update_sl_only（v3.0追加・トレーリングSL反映用）

```python
def update_sl_only(mt5_symbol: str, position_ticket: int, new_sl: float) -> dict:
    """
    既存ポジションのSL価格のみを更新する。
    TPは変更しない（v3.0はTPなし設計のためTPフィールドは0を設定する）。
    ロット数は変更しない。

    MT5のリクエスト形式：
      request = {
          "action": mt5.TRADE_ACTION_SLTP,
          "symbol": mt5_symbol,
          "sl": new_sl,
          "tp": 0.0,       # TPなし
          "position": position_ticket,
      }

    戻り値：
      {"success": bool, "retcode": int, "comment": str}

    注意：
      このメソッドはrisk_manager.update_trailing_sl() が float を返した場合のみ呼ぶこと。
      Noneが返ってきた場合（更新不要）はこのメソッドを呼ばない。
    """
    pass
```

#### 発注バリデーション

```python
def validate_order(order_params: dict, portfolio_state: PortfolioState) -> tuple[bool, str]:
    """
    発注パラメータをバリデーションする。
    1つでも失敗したら(False, 理由)を返す。全て合格したら(True, "OK")を返す。

    チェックリスト（この順序で実施）：
    1. sl が設定されていること
    2. "tp" キーが存在しないこと、または tp == 0.0 であること
       （v3.0はTPを設定してはいけない）
    3. lot_size >= 0.01 であること
    4. 新規BUYの場合: portfolio_state.available_slots() > 0
    5. 新規BUYの場合: 同一セクターの保有数 < max_positions_per_sector(2)
    6. INCREASEの場合: can_scale_in() が True であること
    """
    pass
```

---

### 3.5 signal_filter.py

```python
def apply(ticker: str, new_decision: dict, signal_history: dict) -> dict:
    """
    シグナルにフィルタを適用する。
    フィルタに引っかかった場合はHOLDに変換して返す。

    signal_history: {ticker: [最新のdecision, ...]} 新しい順

    ルール1: 確信度チェック（risk.yaml signal_filter.min_confidence）
      confidence < 0.6 → HOLDに変換

    ルール2: 方向転換の連続シグナル要件
      (BUY/INCREASE) ↔ (DECREASE/CLOSE) の転換は
      連続2回同じ方向が出た場合のみ採用

    重要: signal_history.json（このフィルタ用）と
    TradingAgents内蔵のDecision Log（LLMコンテキスト用）は別物。
    このフィルタでは必ず signal_history.json を参照すること。
    """
    MIN_CONFIDENCE = 0.6
    CONSECUTIVE_REQUIRED = 2

    if new_decision["confidence"] < MIN_CONFIDENCE:
        return {**new_decision, "action": "HOLD",
                "filter_reason": f"confidence {new_decision['confidence']:.2f} < {MIN_CONFIDENCE}"}

    history = signal_history.get(ticker, [])
    if not history:
        return new_decision

    current_action = new_decision["action"]
    last_action = history[0]["action"]

    buy_side = {"BUY", "INCREASE"}
    exit_side = {"DECREASE", "CLOSE"}

    current_is_buy = current_action in buy_side
    last_is_buy = last_action in buy_side
    current_is_exit = current_action in exit_side
    last_is_exit = last_action in exit_side

    is_direction_change = (current_is_buy and last_is_exit) or (current_is_exit and last_is_buy)

    if is_direction_change:
        needed = CONSECUTIVE_REQUIRED - 1
        same_count = sum(
            1 for h in history[:needed]
            if (current_is_buy and h["action"] in buy_side) or
               (current_is_exit and h["action"] in exit_side)
        )
        if same_count < needed:
            return {**new_decision, "action": "HOLD",
                    "filter_reason": f"Direction change needs {CONSECUTIVE_REQUIRED} consecutive signals"}

    return new_decision
```

---

## 4. 分析スケジュール仕様

### 4.1 実行タイミング

| 対象                             | タイミング               | 分析深度                               |
| -------------------------------- | ------------------------ | -------------------------------------- |
| 保有中の銘柄（最大6）            | 毎日 日本時間 早朝6:00   | トレーリングSL更新 → フル分析         |
| 監視中の銘柄（残り最大9）        | 週2回（月・木 早朝6:00） | 軽量スクリーニング → 候補のみフル分析 |
| 重大イベント時（急変動・決算等） | 即時・割り込み実行       | フルパイプライン                       |

**トレーリングSL更新（ステップ[3]）は分析（ステップ[5]）より必ず先に実行する。**
理由：分析中にSLに引っかかって決済されても、それはシステムの正常動作。

### 4.2 screener.py の除外条件

```
除外条件1（コスト負け確定）: スプレッド / ATR(14) > 5%
除外条件2（流動性異常）: 直近5日の平均出来高 < 通常の50%
除外条件3（動きなし）: 直近1日の価格変動 < ±0.5%

保有中の銘柄はこのスクリーニングの対象外（必ず分析する）。
条件3のみの場合: 「no movement」ログを記録してHOLD維持。
条件1または2の場合: 「screened out」ログを記録。
```

---

## 5. リスク管理の全ルール一覧（優先度順）

AIエージェントはこの優先度順でチェックを実装すること。

| 優先度 | ルール           | 発動条件                          | 処置                                 |
| ------ | ---------------- | --------------------------------- | ------------------------------------ |
| 1      | 週次損失リミット | weekly_pnl < -(残高×6%)          | 即時全処理停止（週次リセットまで）   |
| 2      | 日次損失リミット | daily_pnl < -(残高×3%)           | 当日の新規発注を全停止               |
| 3      | 証拠金維持率     | margin_level < 300%               | 新規発注を全停止（既存SLはそのまま） |
| 4      | SL必須チェック   | SL未設定の発注                    | 発注を拒否・ERRORログ                |
| 5      | TP禁止チェック   | tp > 0 の発注                     | 発注を拒否・ERRORログ                |
| 6      | 最大ポジション数 | 保有数=6 で新規BUY                | BUYを拒否・WARNログ                  |
| 7      | セクター集中制限 | 同一セクター2銘柄中に3つ目のBUY   | BUYを拒否・WARNログ                  |
| 8      | ナンピン禁止     | 含み損中のINCREASE                | INCREASEを拒否・HOLD変換             |
| 9      | SL不利更新禁止   | new_sl <= current_sl（BUYの場合） | 更新しない・INFOログ                 |
| 10     | 確信度フィルタ   | confidence < 0.6                  | HOLDに変換・INFOログ                 |
| 11     | 連続シグナル要件 | 方向転換シグナルが1回のみ         | HOLDに変換・INFOログ                 |
| 12     | スケーリング上限 | 買い増し後にinitial×2.5超        | ロット数を上限まで圧縮               |

---

## 6. メモリ・履歴の役割分担（v3.0追加）

本システムには2つの独立した履歴管理が共存する。混同してはいけない。

| 項目           | TradingAgents Decision Log                                | data/signal_history.json                     |
| -------------- | --------------------------------------------------------- | -------------------------------------------- |
| 管理主体       | TradingAgents内部                                         | 本システム（scheduler.py）                   |
| 目的           | LLMが過去の判断を文脈として参照するための自己学習用メモリ | フリップフロップ防止・連続シグナルチェック用 |
| 使用箇所       | TradingAgentsの各エージェントのプロンプト内               | signal_filter.apply() のみ                   |
| 更新タイミング | TradingAgentsが自動管理                                   | scheduler.pyが発注後に更新                   |
| 参照方法       | TradingAgentsが内部で自動参照                             | signal_filter.pyが明示的に読み込む           |

**AIエージェントへの注意：**

- Decision Logを signal_filter.py で参照するコードを書いてはいけない
- signal_history.json をTradingAgentsの設定に渡すコードを書いてはいけない
- 2つを統合・マージするコードを書いてはいけない

---

## 7. データ永続化の仕様

### 7.1 data/signal_history.json

```json
{
  "AAPL": [
    {
      "date": "2026-06-30",
      "action": "INCREASE",
      "confidence": 0.78,
      "converted_from": "BUY",
      "rationale": "...",
      "filter_applied": false,
      "filter_reason": null,
      "executed": true
    }
  ]
}
```

### 7.2 data/position_meta.json

```json
{
  "AAPLm": {
    "ticker": "AAPL",
    "initial_lots": 0.10,
    "scale_count": 1,
    "entry_date": "2026-06-25",
    "scale_history": [
      {"date": "2026-06-28", "lots": 0.06, "price": 187.50}
    ]
  }
}
```

---

## 8. ログ仕様

```
フォーマット: [YYYY-MM-DD HH:MM:SS] [LEVEL] [MODULE] MESSAGE

出力例（v3.0）:
[2026-06-30 06:00:01] [INFO]  [ACCOUNT_GUARD]   Limits OK. Trading allowed.
[2026-06-30 06:00:02] [INFO]  [PORTFOLIO_STATE]  Synced 3 positions from MT5.
[2026-06-30 06:00:03] [INFO]  [RISK_MANAGER]     AAPL: trailing SL updated $182.00 → $184.50
[2026-06-30 06:00:03] [INFO]  [RISK_MANAGER]     MSFT: trailing SL not updated (new_sl <= current_sl)
[2026-06-30 06:00:03] [INFO]  [ORDER_EXEC]       AAPL: SLTP updated. SL=$184.50 TP=0
[2026-06-30 06:00:05] [INFO]  [SCREENER]         TSLA: screened out (spread/ATR: 6.2%)
[2026-06-30 06:02:10] [INFO]  [TA_RUNNER]        AAPL: raw=BUY → converted to INCREASE (holding)
[2026-06-30 06:02:10] [WARN]  [SIGNAL_FILTER]    MSFT: direction change filtered. Need 2 consecutive.
[2026-06-30 06:02:11] [WARN]  [RISK_MANAGER]     AAPL: scale-in rejected. No unrealized profit.
[2026-06-30 06:02:12] [INFO]  [ORDER_EXEC]       NVDA: BUY 0.10 lot @ $950.00 SL=$930.00 TP=none
[2026-06-30 06:02:13] [ERROR] [ORDER_EXEC]       NVDA: MT5 retcode 10006 - request rejected
[2026-06-30 06:02:14] [WARN]  [ORDER_EXEC]       GOOGL: rejected - tp > 0 is forbidden in v3.0
```

---

## 9. .env ファイルの仕様

```bash
MT5_ACCOUNT=123456789
MT5_PASSWORD=your_demo_password
MT5_SERVER=XMTrading-MT5 3        # 口座開設メール記載のサーバー名をそのまま使う

OPENAI_API_KEY=sk-...
ALPHA_VANTAGE_API_KEY=...         # TradingAgentsのファンダメンタルズ取得用
LINE_NOTIFY_TOKEN=...             # 通知（オプション）

LOG_DIR=./logs
SIGNAL_HISTORY_PATH=./data/signal_history.json
POSITION_META_PATH=./data/position_meta.json
```

---

## 10. 開発フェーズ

| フェーズ | 実装内容                                                      | 完了条件                                                       |
| -------- | ------------------------------------------------------------- | -------------------------------------------------------------- |
| Phase 1  | mt5_client / portfolio_state / market_data                    | デモ口座のポジション・残高・ATRが正しく取得できること          |
| Phase 2  | screener / tradingagents_runner（シグナル出力のみ・発注なし） | 15銘柄のシグナルがエラーなく並列取得できること                 |
| Phase 3  | signal_filter / risk_manager / account_guard の全unit test    | 全リスクルール・トレーリングSLフェイルセーフのテストが通ること |
| Phase 4  | order_executor（デモ口座で実発注）                            | BUY・SLTP更新・CLOSE・部分決済が正常動作すること               |
| Phase 5  | scheduler.pyでフルサイクル自動実行                            | 1ヶ月以上デモで安定稼働後、結果を評価してリアル移行を検討      |

---

## 11. AIコーディングエージェントへの実装禁止事項

1. **TPを設定した発注コードを書いてはいけない**
   v3.0はTPなし設計。order_executorのvalidate_orderがtp>0の発注を拒否する。
   この拒否ロジックを迂回するコードも書いてはいけない。
2. **SLを不利方向に動かすトレーリングSL更新コードを書いてはいけない**
   update_trailing_sl()がNoneを返した場合、update_sl_only()を呼ばないこと。
   「とりあえず更新する」コードは最も危険な実装ミス。
3. **ナンピンロジックを実装してはいけない**
   含み損中のINCREASEはcan_scale_in()がFalseを返す設計。
   この拒否を迂回するコードを書いてはいけない。
4. **SLなし発注を通してはいけない**
   デモ環境だからといってSLを省略するロジックを書かないこと。
5. **TradingAgentsの出力を直接発注に使ってはいけない**
   parse_decision → signal_filter → risk_manager → order_executorの
   全段階を必ず経ること。どの段階もショートカット禁止。
6. **Decision LogとSignal Historyを混同・統合してはいけない**
   2つは独立した別物。Section 6の役割分担表を参照すること。
7. **保有中の銘柄にBUY/SELL発注を出してはいけない**
   parse_decision()の変換ロジックが正しく機能していれば起きないが、
   order_executor側でも「保有中銘柄へのBUY発注」を検知したらエラーログを出すこと。
8. **リアル口座への接続コードをPhase5より前に実装しないこと**
