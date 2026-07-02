# TradingAgents × XMTrading 自動売買システム 設計書 v3.2

> **この設計書の読み方**
> VSCode AgentなどのAIコーディングエージェントが実装ミスをしないよう、
> データ構造・ルール・境界条件・禁止事項を明示的に定義する。
> 曖昧な表現は一切使わない。迷ったらこの設計書に立ち返ること。

> **v3.2の位置づけ（v3.0/v3.1からの改訂）**
> Phase 0 調査（2026-07-01 実施）で判明した「TradingAgents本体の実装事実」と
> 「XMTrading MT5の実シンボル事実」を全面反映した確定版。
> **リスクプロファイルは「やや攻め」を採用**（per_trade 2.5% / total 12% / per_sector 3、
> サーキットブレーカー daily 5% / weekly 10%）。デモ検証で週+3〜6%を狙うが保証しない。
> 週+20%のような目標はこのパラメータでは達成不能であり、追求すると破産確率が急増する。
> v3.0/v3.1が二次情報で前提としていた以下は**実機で誤りと判明したため破棄・修正**した。
>
> - `propagate` は非同期ではなく**同期**（`asyncio.to_thread` が必要）
> - 判断の戻り値は BUY/SELL/HOLD でも dict でもなく**5段階レーティング文字列**
> - 最終判断に **confidence は存在しない**（confidenceフィルタは廃止）
> - `config["portfolio_context"]` 注入は**黙殺され機能しない**（別チャネルへ変更）
> - MT5シンボルは `AAPLm` 形式ではなく**会社名**（`Apple` / `JPMorgan` 等）
> - 最小ロットは 0.01 固定ではなく**銘柄ごとに 0.02〜0.13 と異なる**
>   詳細な根拠は本リポジトリの `Phase0_判定レポート.md` を参照。

---

## 0. 全確定前提（変更禁止）

| 項目           | 決定内容                                                                    | 禁止事項                                  |
| -------------- | --------------------------------------------------------------------------- | ----------------------------------------- |
| 取引対象       | 米国株CFD（XMTrading提供）                                                  | FXメジャーペアへの適用                    |
| LLM活用方針    | 分析・判断シグナルの生成のみ                                                | LLMに直接発注権限を持たせること           |
| 保有期間管理   | 方式1：定期再評価。保有期間をLLMに予測させない                              | horizon予測をシステムの発注根拠にすること |
| スケーリング   | 含み益方向のみ買い増し許可                                                  | ナンピン（含み損中の買い増し）の実装      |
| 利益確定（TP） | **固定TPは一切使用しない**                                            | tp値を指定した発注                        |
| 出口判断       | **LLMが Sell/Underweight を出すか、トレーリングSLが刈り取るまで保有** | 固定日数・固定価格での機械的利確          |
| リスク管理     | **トレーリングSL（日次更新・有利方向のみ）が唯一の強制出口**          | SLを不利方向に動かす更新                  |
| 実行環境       | Python on Windows（MT5ターミナルが動く環境）                                | GASでの実装                               |
| 運用フェーズ   | デモ口座での検証から開始                                                    | Phase5より前のリアル口座接続コードの実装  |
| 監視銘柄数     | 15銘柄固定（**11 GICSセクター分散**）                                 | 初期実装で15を超えること                  |
| 最大保有数     | 同時6銘柄（システムが自動拒否）                                             | 6を超える発注の通過                       |
| 判断シグナル   | **5段階レーティング**（Buy/Overweight/Hold/Underweight/Sell）         | BUY/SELL/HOLD 3値前提のパーサ実装         |
| 通知手段       | **Discord Webhook**（LINE Notifyは2025-03終了）                       | LINE Notifyへの新規実装                   |

---

## 0.5. Phase 0 実機確認で確定した事実（実装時の絶対基準）

> ここに書かれた事実は実機・実ソースで確認済み。推測で上書きしてはいけない。

### 0.5.1 TradingAgents本体（clone: `TauricResearch/TradingAgents`, commit `85946c2f`, Apache 2.0）

| # | 事実                                                                                                                                                           | 実装への拘束                                                                               |
| - | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| 1 | `TradingAgentsGraph.propagate(company_name, trade_date, asset_type="stock")` は**同期メソッド**                                                        | 並列化は`await asyncio.to_thread(ta.propagate, ...)`。`await ta.propagate(...)` は不可 |
| 2 | 戻り値は`(final_state, signal)` のタプル。`signal` は **`str`** で 5段階レーティング語（`Buy`/`Overweight`/`Hold`/`Underweight`/`Sell`） | `parse_decision` は文字列入力前提。dict/3値前提は禁止                                    |
| 3 | Trader/PMは Pydantic 構造化出力（`schemas.py`：`TraderProposal.action`=Buy/Hold/Sell、`PortfolioDecision.rating`=5段階）                                 | レーティング語を正として扱う                                                               |
| 4 | **confidence は最終判断に存在しない**。confidence フィールドは `SentimentReport`（low/medium/high, score0–10）のみ                                    | confidenceフィルタは廃止。レーティング階層ゲートで代替                                     |
| 5 | `config` は素の dict。未定義キー（`portfolio_context` 等）は保存されるが**どこからも読まれず黙殺**（非エラー）                                       | `config["portfolio_context"]` 注入は禁止。0.5.2 の注入方式を使う                         |
| 6 | `DEFAULT_CONFIG["checkpoint_enabled"]`（既定 `False`）は**実在**                                                                                     | v3.2でも有効設定として維持                                                                 |
| 7 | 既定モデル：`deep_think_llm="gpt-5.5"`, `quick_think_llm="gpt-5.4-mini"`                                                                                   | risk/コンフィグの記載値をこれに合わせる                                                    |
| 8 | `final_state` は各アナリストレポート全文＋PM判断（markdown）を含む                                                                                           | rationale（根拠テキスト）は`final_state["final_trade_decision"]` から抽出可              |
| 9 | 過去判断メモリは本体側`TradingMemoryLog`（`past_context`）が自動管理                                                                                       | 本システムの`signal_history.json` とは別物。混同禁止（§6）                              |

### 0.5.2 ポートフォリオ文脈の注入方式（A'案・確定）

TradingAgents の Trader と Portfolio Manager のプロンプトは、両方とも
`agent_utils.get_instrument_context_from_state(state)` で `state["instrument_context"]`
を読み込む。この `instrument_context` は実行開始時に
`TradingAgentsGraph.resolve_instrument_context(ticker, asset_type)` が生成し初期stateへ注入する。

**確定した注入フック（Phase 0でトークン到達を実証済み）：**

```python
# core/tradingagents_patch.py
import functools
from tradingagents.graph.trading_graph import TradingAgentsGraph

def install_portfolio_injection(get_ticker_context):
    """resolve_instrument_context をラップし、生成される instrument_context 末尾に
    ポートフォリオ文脈を追記する。Trader・PM 両プロンプトへ公式チャネル経由で届く。

    get_ticker_context(ticker: str) -> str : 当該銘柄のポートフォリオ文脈を返す関数
    """
    _orig = TradingAgentsGraph.resolve_instrument_context

    @functools.wraps(_orig)
    def wrapped(self, ticker, asset_type="stock"):
        base = _orig(self, ticker, asset_type)
        extra = get_ticker_context(ticker) or ""
        return f"{base}\n\n{extra}" if extra else base

    TradingAgentsGraph.resolve_instrument_context = wrapped
```

**重要な落とし穴（実装者への警告）：**
`tradingagents.graph.trading_graph` はモジュール読込時に
`from ...agent_utils import build_instrument_context` で名前束縛する。
そのため `agent_utils.build_instrument_context` だけを差し替えても
`_run_graph` 経由の実注入には**効かない**。
必ず上記のように `TradingAgentsGraph.resolve_instrument_context`
（＝ `_run_graph` が呼ぶメソッド）をラップすること。

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
│   ├── tradingagents_patch.py    # resolve_instrument_context 注入パッチ（v3.2追加）
│   ├── tradingagents_runner.py   # TradingAgentsGraph呼び出し（最重要）
│   ├── signal_filter.py          # フリップフロップ防止＋レーティング階層ゲート
│   ├── risk_manager.py           # ロット計算・SL計算・トレーリングSL更新
│   ├── order_executor.py         # MT5への発注・SL更新（TRADE_ACTION_SLTP）
│   └── account_guard.py          # 口座レベルサーキットブレーカー
├── data/
│   ├── signal_history.json       # フリップフロップ防止用履歴（自動生成）
│   └── position_meta.json        # MT5から取得できないポジション補足情報
├── logs/
├── scheduler.py
├── logger.py
└── notifier.py                   # Discord Webhook 通知
```

### 1.2 データフロー（1サイクルの処理順序）

**以下の順序を絶対に守ること。順序を変えるとリスク管理が機能しなくなる。**

```
[scheduler.py 起動（日本時間 毎日早朝6:00）]
      ↓
[0] tradingagents_patch.install_portfolio_injection(runner.get_ticker_context)
      → プロセス起動時に一度だけ適用（resolve_instrument_context をラップ）
      ↓
[1] account_guard.check()
      → NG（日次/週次損失リミット超過）なら即終了（発注なし）
      ↓
[2] portfolio_state.sync_from_mt5()
      → MT5から現在の保有ポジションを取得・ローカルに同期
      ↓
[3] 保有ポジションの日次トレーリングSL更新
      risk_manager.update_trailing_sl(各保有ポジション)
      → 有利方向のみSLを更新。不利方向には絶対に動かさない
      → order_executor.update_sl_only(TRADE_ACTION_SLTP) で反映
      ↓
[4] screener.run(全15銘柄)
      → 軽量スクリーニングで明らかに動きのない銘柄を除外
      → 保有中の銘柄はスクリーニング除外対象外（必ず分析する）
      ↓
[5] tradingagents_runner.run_parallel(分析対象リスト)
      → 各銘柄実行前に portfolio_state を runner に渡し、
        注入パッチが resolve_instrument_context 経由で文脈を差し込む
      → propagate は同期のため asyncio.to_thread で並列化
      → Exponential Backoff つき（Tenacity）
      ↓
[6] parse_decision で 5段階レーティング → システムアクションへ変換（§3.2）
      ↓
[7] signal_filter.apply(各銘柄のシグナル)
      → レーティング階層ゲート＋フリップフロップ防止フィルタ適用
      → 有効シグナルのみ通過
      ↓
[8] 各シグナルに対してアクション実行
      BUY      → risk_manager.calculate_initial_lot() → order_executor.open_position()
                  ※TP指定なし・SLのみ設定
      INCREASE → risk_manager.can_scale_in() チェック → order_executor.add_position()
                  ※買い増し後SLを平均取得単価ベースで再計算
      DECREASE → order_executor.partial_close() （保有ロットの50%を決済）
      CLOSE    → order_executor.close_position() （全決済）
      HOLD     → 何もしない（ステップ[3]でSLが既に更新済み）
      ↓
[9] logger.write() / notifier.send()（Discord）
```

---

## 2. 設定ファイル仕様

### 2.1 config/symbols.yaml（v3.2：実シンボル・11セクター分散・volume_min実測値）

> **確定事項：**
>
> - `mt5_symbol` は XMTrading MT5 の**実在シンボル名（会社名）**。Phase 0 で `mt5.symbols_get()` により全件確認済み。
> - 全銘柄 group path=`Stocks\US\...`、`trade_mode=4`（全取引可）、`contract_size=10`、`currency_profit=USD`、`digits=2`、D1足20本取得可。
> - `volume_min` は**銘柄ごとに異なる実測値**。`volume_step` は全銘柄 0.01。
> - 業態が偏らないよう **11 GICSセクターに分散**し、同一サブ業種の重複を避けた。

```yaml
# XMTradingデモ口座(Server: XMTrading-MT5 3)で mt5.symbols_get() により実在確認済み。
# mt5_symbol は会社名。ティッカーはTradingAgentsへ渡す識別子として保持する。
# volume_min は Phase 0 実測値。ロット丸めは必ず銘柄別の volume_min / volume_step を使うこと。

symbols:
  # --- Information Technology ---
  - ticker: "NVDA"
    mt5_symbol: "Nvidia"
    sector: "information_technology"
    sub_industry: "semiconductors"
    volume_min: 0.04
    volume_step: 0.01
    analysis_interval_days: 3

  - ticker: "MSFT"
    mt5_symbol: "Microsoft"
    sector: "information_technology"
    sub_industry: "software"
    volume_min: 0.02
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Communication Services ---
  - ticker: "GOOGL"
    mt5_symbol: "Google"          # description: Alphabet Inc (GOOG.OQ)
    sector: "communication_services"
    sub_industry: "interactive_media"
    volume_min: 0.03
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Consumer Discretionary ---
  - ticker: "AMZN"
    mt5_symbol: "Amazon"
    sector: "consumer_discretionary"
    sub_industry: "internet_retail"
    volume_min: 0.03
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Consumer Staples ---
  - ticker: "KO"
    mt5_symbol: "Coca-Cola"
    sector: "consumer_staples"
    sub_industry: "beverages"
    volume_min: 0.11
    volume_step: 0.01
    analysis_interval_days: 3

  - ticker: "PG"
    mt5_symbol: "Procter&Gam"     # description: Procter & Gamble Co (PG.N)
    sector: "consumer_staples"
    sub_industry: "household_products"
    volume_min: 0.06
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Health Care ---
  - ticker: "JNJ"
    mt5_symbol: "J&J"             # description: Johnson & Johnson (JNJ.N)
    sector: "health_care"
    sub_industry: "pharmaceuticals"
    volume_min: 0.04
    volume_step: 0.01
    analysis_interval_days: 3

  - ticker: "UNH"
    mt5_symbol: "UnitedHealth"
    sector: "health_care"
    sub_industry: "managed_care"
    volume_min: 0.03
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Financials ---
  - ticker: "JPM"
    mt5_symbol: "JPMorgan"
    sector: "financials"
    sub_industry: "banks"
    volume_min: 0.03
    volume_step: 0.01
    analysis_interval_days: 3

  - ticker: "V"
    mt5_symbol: "Visa"
    sector: "financials"
    sub_industry: "payments"
    volume_min: 0.03
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Industrials ---
  - ticker: "CAT"
    mt5_symbol: "Caterpillar"
    sector: "industrials"
    sub_industry: "machinery"
    volume_min: 0.02
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Energy ---
  - ticker: "XOM"
    mt5_symbol: "ExxonMobil"
    sector: "energy"
    sub_industry: "integrated_oil"
    volume_min: 0.05
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Materials ---
  - ticker: "LIN"
    mt5_symbol: "Linde"           # description: Linde PLC (LIN.OQ)
    sector: "materials"
    sub_industry: "industrial_gases"
    volume_min: 0.02
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Utilities ---
  - ticker: "NEE"
    mt5_symbol: "NextEra"
    sector: "utilities"
    sub_industry: "electric_utility"
    volume_min: 0.09
    volume_step: 0.01
    analysis_interval_days: 3

  # --- Real Estate ---
  - ticker: "AMT"
    mt5_symbol: "AmericanTower"
    sector: "real_estate"
    sub_industry: "telecom_reit"
    volume_min: 0.05
    volume_step: 0.01
    analysis_interval_days: 3
```

**セクター分散の確認（11セクター全カバー）：**

| セクター               | 銘柄（サブ業種）                 |
| ---------------------- | -------------------------------- |
| Information Technology | NVDA(半導体), MSFT(ソフトウェア) |
| Communication Services | GOOGL(ネット広告/メディア)       |
| Consumer Discretionary | AMZN(EC)                         |
| Consumer Staples       | KO(飲料), PG(生活用品)           |
| Health Care            | JNJ(医薬), UNH(医療保険)         |
| Financials             | JPM(銀行), V(決済ネットワーク)   |
| Industrials            | CAT(建機)                        |
| Energy                 | XOM(石油)                        |
| Materials              | LIN(産業ガス)                    |
| Utilities              | NEE(電力)                        |
| Real Estate            | AMT(通信REIT)                    |

> 同一サブ業種の重複はなし。監視リストは1セクター最大2銘柄（IT/Staples/HealthCare/Financials）で構成。
> ただし保有時のセクター集中上限は「やや攻め」設定で `max_positions_per_sector: 3` とする
> （6ポジションを最小2セクターに寄せられるが、通常は分散されることを想定）。

### 2.2 config/risk.yaml（v3.2：confidence廃止・レーティングゲート・銘柄別ロット）

```yaml
# ==============================
# ポートフォリオ全体の制約
# ==============================
portfolio:
  max_positions: 6
  max_positions_per_sector: 3       # やや攻め（監視リストは1セクター最大2だが集中を許容）
  max_risk_per_trade_pct: 2.5       # やや攻め: 1トレードの最大損失（口座残高に対する%）
  max_total_risk_pct: 12.0          # やや攻め: 全ポジション合計の最大損失（口座残高に対する%）

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
# トレーリングSL設定
# ==============================
trailing:
  enabled: true
  update_on_hold: true              # HOLD判定時でも日次でSLを更新する
  # BUYポジション: new_sl = current_price - (ATR × atr_multiplier)
  # 更新条件: new_sl > current_sl の場合のみ更新（有利方向のみ）
  # 禁止: new_sl <= current_sl の場合の更新（絶対に行わない）

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
  daily_loss_limit_pct: 5.0         # やや攻め（per_trade 2.5%×約2トレード分）
  weekly_loss_limit_pct: 10.0       # やや攻め
  min_margin_level_pct: 300.0

# ==============================
# シグナルゲート（v3.2：confidence廃止 → レーティング階層ゲート）
# ==============================
signal_gate:
  # 最終判断は5段階レーティング（Buy/Overweight/Hold/Underweight/Sell）。
  # 数値confidenceは本体に存在しないため使用しない。
  new_entry_ratings: ["Buy", "Overweight"]      # 未保有時にBUYを許可するレーティング
  scale_in_ratings:  ["Buy", "Overweight"]      # 保有時にINCREASEを許可するレーティング
  decrease_ratings:  ["Underweight"]            # 保有時にDECREASEするレーティング
  close_ratings:     ["Sell"]                   # 保有時にCLOSEするレーティング
  # Hold は常にHOLD。上記以外は全てHOLDにフォールバック。
  direction_change_consecutive_required: 2      # フリップフロップ防止（方向転換は連続2回必要）

# ==============================
# API実行制御
# ==============================
execution:
  max_parallel_analysis: 4
  # propagate は同期メソッドのため asyncio.to_thread で並列化する
  # Exponential Backoff設定（Tenacityライブラリ使用）
  retry_enabled: true
  retry_max_attempts: 5
  retry_wait_min_seconds: 4         # 初回待機時間
  retry_wait_max_seconds: 60        # 最大待機時間
  retry_multiplier: 2               # 待機時間の指数倍率

# ==============================
# TradingAgents 設定（v3.2：実在キーのみ）
# ==============================
tradingagents:
  llm_provider: "openai"
  deep_think_llm: "gpt-5.5"         # DEFAULT_CONFIG 既定値
  quick_think_llm: "gpt-5.4-mini"  # DEFAULT_CONFIG 既定値
  max_debate_rounds: 1
  max_risk_discuss_rounds: 1
  checkpoint_enabled: true          # 実在キー。クラッシュ再開を有効化
  output_language: "Japanese"       # レポート言語（内部討論は英語のまま）
```

---

## 3. 核心モジュールの仕様

### 3.1 portfolio_state.py（最重要）

#### なぜこのモジュールが最重要か

TradingAgentsは標準では「今どの銘柄をどれだけ保有しているか」を知らない。
`propagate(ticker, date)` はポートフォリオ状態を一切考慮しない。
このモジュールがMT5から保有状態を取得し、注入パッチ（§0.5.2）が
プロンプトへ差し込める形式に変換する責務を持つ。

これが正しく実装されていない場合に起きる事故：

- 保有中なのに新規BUYシグナルで二重発注する
- ポートフォリオが満杯なのに新規BUYを推奨する
- 含み損中の銘柄への買い増しをLLMが推奨する（ナンピン事故）

#### データ構造

```python
from dataclasses import dataclass, field

@dataclass
class PositionInfo:
    ticker: str               # 例: "NVDA"（TradingAgentsに渡す名前）
    mt5_symbol: str           # 例: "Nvidia"（MT5に渡す実シンボル名＝会社名）
    direction: str            # "BUY" のみ（v3.2はロングのみ対応）
    total_lots: float         # 現在の総保有ロット数
    avg_entry_price: float    # 平均取得単価（買い増し後に再計算済み）
    current_sl: float         # 現在MT5に設定中のSL価格
    # current_tp は存在しない（v3.2はTPなし設計）
    unrealized_pnl: float     # 現在の含み損益（口座通貨=JPY額）
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
        """注入パッチがプロンプトに差し込むポートフォリオ全体テキストを生成する"""
        lines = ["=== CURRENT PORTFOLIO STATUS ==="]
        if not self.positions:
            lines.append("No positions currently held.")
        else:
            for p in self.positions:
                lines.append(
                    f"- {p.ticker}: {p.direction} {p.total_lots} lots "
                    f"@ avg ${p.avg_entry_price:.2f}, "
                    f"unrealized P&L: ¥{p.unrealized_pnl:+.0f}, "
                    f"SL: ${p.current_sl:.2f}, "
                    f"scale-ins done: {p.scale_count}"
                )
        lines.append(f"Available new position slots: {self.available_slots()}/6")
        lines.append(f"Today's P&L: ¥{self.daily_pnl:+.0f}")
        lines.append(f"Weekly P&L: ¥{self.weekly_pnl:+.0f}")
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
    - XMシンボル名（例：Nvidia）→ ticker名（NVDA）への変換は
      symbols.yaml のマッピングテーブルを参照する
    - 口座は margin_mode=2（HEDGING）。同一シンボルに複数チケットが
      並存しうるため、シンボル単位で total_lots を集約し、
      avg_entry_price は加重平均で算出する
    - DB/メタ保存時のチケットは deal ではなく position/order を基準にする
      （ヘッジ口座で別dealを拾う事故を防ぐ）
    - initial_lots と scale_count はMT5から取得できないため、
      data/position_meta.json から読み込む
    - position_meta.json に存在しないポジションは
      initial_lots=現在のtotal_lots, scale_count=0 として扱い、WARNログを出力する
    - 取得失敗時は例外をraiseし、scheduler.pyでキャッチして
      その日の処理をスキップする（中途半端に処理しない）
    """
    pass
```

---

### 3.2 tradingagents_runner.py（v3.2：全面改訂）

#### TradingAgentsの設定と注入

```python
import asyncio
import functools
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from tenacity import (
    retry, stop_after_attempt, wait_exponential, retry_if_exception_type
)

TA_CONFIG = DEFAULT_CONFIG.copy()
TA_CONFIG["llm_provider"]      = "openai"
TA_CONFIG["deep_think_llm"]    = "gpt-5.5"        # 実在既定値
TA_CONFIG["quick_think_llm"]   = "gpt-5.4-mini"   # 実在既定値
TA_CONFIG["max_debate_rounds"] = 1
TA_CONFIG["checkpoint_enabled"] = True            # 実在キー
# 注意: TA_CONFIG に "portfolio_context" 等の独自キーを入れても黙殺される。
#       文脈注入は tradingagents_patch.install_portfolio_injection を使う。
```

#### ポートフォリオ文脈の生成（注入パッチが呼ぶ関数）

```python
# runner はカレントサイクルの portfolio_state を保持し、
# tradingagents_patch に get_ticker_context を渡す。
_current_portfolio_state = None  # 各サイクル開始時にセット

def set_current_portfolio_state(portfolio_state):
    global _current_portfolio_state
    _current_portfolio_state = portfolio_state

def get_ticker_context(ticker: str) -> str:
    """resolve_instrument_context のラッパーから呼ばれ、
    当該銘柄のポートフォリオ文脈テキストを返す（§0.5.2）。"""
    ps = _current_portfolio_state
    if ps is None:
        return ""
    return build_ticker_context(ps, ticker)


def build_ticker_context(portfolio_state, ticker: str) -> str:
    """1銘柄分のプロンプト注入テキストを生成する。保有中か否かで指示を変える。"""
    base_context = portfolio_state.to_llm_context()
    position = portfolio_state.get_position(ticker)

    if position:
        ticker_context = (
            f"\n=== POSITION STATUS FOR {ticker} ===\n"
            f"Status: CURRENTLY HOLDING (LONG)\n"
            f"Total lots: {position.total_lots} (initial: {position.initial_lots})\n"
            f"Average entry price: ${position.avg_entry_price:.2f}\n"
            f"Current SL: ${position.current_sl:.2f}\n"
            f"Unrealized P&L: ¥{position.unrealized_pnl:+.0f}\n"
            f"Scale-ins done: {position.scale_count}\n"
            f"\nRating guidance for a position you ALREADY HOLD (long-only book):\n"
            f"- Buy/Overweight: conviction to ADD (only if outlook improved AND unrealized P&L > 0)\n"
            f"- Hold: maintain the position\n"
            f"- Underweight: reduce / take partial profit\n"
            f"- Sell: exit the position fully\n"
            f"=================================="
        )
    else:
        slots = portfolio_state.available_slots()
        if slots <= 0:
            ticker_context = (
                f"\n=== POSITION STATUS FOR {ticker} ===\n"
                f"Status: NOT HOLDING. Portfolio is FULL (6/6).\n"
                f"Prefer Hold. A new long can only be opened if an existing "
                f"position is closed this cycle.\n"
                f"=================================="
            )
        else:
            ticker_context = (
                f"\n=== POSITION STATUS FOR {ticker} ===\n"
                f"Status: NOT HOLDING. Available slots: {slots}/6.\n"
                f"This is a LONG-ONLY book: Sell/Underweight for an instrument "
                f"you do not hold means 'stay flat', not 'go short'.\n"
                f"=================================="
            )
    return base_context + ticker_context
```

#### parse_decision（v3.2最重要：5段階レーティング → システムアクション変換）

```python
def parse_decision(raw_signal, ticker: str, portfolio_state) -> dict:
    """
    propagate の第2戻り値（5段階レーティング文字列）を統一フォーマットへ変換する。

    【入力】raw_signal は str: "Buy"/"Overweight"/"Hold"/"Underweight"/"Sell"
            （念のため大小文字・前後空白・markdown装飾を許容し正規化する）

    【保有状態に応じた強制変換ルール（v3.2）】
    保有中の銘柄（long-only）:
      Buy / Overweight  → INCREASE   （買い増し。実際の可否は can_scale_in で最終判定）
      Hold              → HOLD
      Underweight       → DECREASE    （部分利確）
      Sell              → CLOSE       （全決済）

    未保有の銘柄（long-only。ショートは開かない）:
      Buy / Overweight  → BUY         （新規ロング）
      Hold              → HOLD
      Underweight       → HOLD        （保有していないので減らせない）
      Sell              → HOLD        （ショート禁止のため何もしない）

    不明・パース不能 → HOLD（フェイルセーフ）

    返り値（この構造を厳守）:
    {
        "action": str,        # "BUY"/"HOLD"/"INCREASE"/"DECREASE"/"CLOSE"
        "rating": str,        # 正規化後の元レーティング（"Buy"等）
        "rationale": str,     # final_state から抽出（呼び出し側で付与）
        "raw": any,
        "converted_from": str # 元レーティング（監査用）
    }

    ※ confidence は返さない（本体に存在しないため）。
    """
    VALID = {"BUY", "HOLD", "INCREASE", "DECREASE", "CLOSE"}
    RATING_SET = {"buy", "overweight", "hold", "underweight", "sell"}

    try:
        rating_raw = str(raw_signal).strip().strip("*").strip()
        rating_norm = rating_raw.lower()
        if rating_norm not in RATING_SET:
            return _hold(raw_signal, f"Unrecognized rating: {rating_raw!r}")

        rating = rating_norm.capitalize()  # "Buy" / "Overweight" / ...
        holding = portfolio_state.get_position(ticker) is not None

        if holding:
            mapping = {
                "buy": "INCREASE", "overweight": "INCREASE",
                "hold": "HOLD",
                "underweight": "DECREASE",
                "sell": "CLOSE",
            }
        else:
            mapping = {
                "buy": "BUY", "overweight": "BUY",
                "hold": "HOLD",
                "underweight": "HOLD",
                "sell": "HOLD",
            }
        action = mapping[rating_norm]
        if action not in VALID:
            action = "HOLD"

        return {
            "action": action,
            "rating": rating,
            "rationale": "",
            "raw": raw_signal,
            "converted_from": rating,
        }
    except Exception as e:
        return _hold(raw_signal, f"PARSE ERROR: {e}")


def _hold(raw_signal, reason):
    return {
        "action": "HOLD", "rating": "Hold",
        "rationale": f"defaulting to HOLD - {reason}",
        "raw": raw_signal, "converted_from": None,
    }
```

#### Exponential Backoff つき同期→非同期実行（v3.2）

```python
@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(5),
    reraise=True,
)
def _call_trading_agents_sync(ta: TradingAgentsGraph, ticker: str, date_str: str):
    """propagate は同期。Tenacityで同期リトライする。"""
    return ta.propagate(ticker, date_str)   # -> (final_state, rating_str)


async def run_single(ticker: str, date_str: str, portfolio_state) -> dict:
    """1銘柄の分析。propagate は同期のため to_thread でオフロードする。
    エラー時は必ずHOLDを返す。"""
    try:
        ta = TradingAgentsGraph(debug=False, config=TA_CONFIG.copy())
        # 注入は install_portfolio_injection 済み（resolve_instrument_context がラップ済み）。
        final_state, rating_str = await asyncio.to_thread(
            _call_trading_agents_sync, ta, ticker, date_str
        )
        decision = parse_decision(rating_str, ticker, portfolio_state)
        # rationale は final_state の最終判断markdownから抽出（Executive Summary等）
        decision["rationale"] = extract_rationale(final_state)
        return {"ticker": ticker, "decision": decision, "error": None}
    except Exception as e:
        return {
            "ticker": ticker,
            "decision": _hold(None, f"Runner failed after retries: {e}"),
            "error": str(e),
        }


async def run_parallel(tickers, date_str, portfolio_state, max_parallel=4) -> list[dict]:
    """複数銘柄を並列実行。max_parallelでAPIレート制限を回避する。"""
    set_current_portfolio_state(portfolio_state)   # 注入パッチが参照する
    semaphore = asyncio.Semaphore(max_parallel)

    async def _run(ticker):
        async with semaphore:
            return await run_single(ticker, date_str, portfolio_state)

    return await asyncio.gather(*[_run(t) for t in tickers])


def extract_rationale(final_state) -> str:
    """final_state['final_trade_decision'] の markdown から根拠テキストを抽出する。
    '**Executive Summary**:' / '**Investment Thesis**:' 行を優先的に拾う。
    取得不能なら空文字を返す（フェイルセーフ）。"""
    pass
```

> **注意：** confidence は取得できないため、`signal_filter` の判定はレーティング階層と
> フリップフロップ履歴のみで行う（§3.5）。`final_state` の Sentiment レポートに含まれる
> `confidence`（low/medium/high）はログ用テレメトリとしてのみ利用可（発注判断には使わない）。

---

### 3.3 risk_manager.py（v3.2：銘柄別ロット丸め）

#### update_trailing_sl（最重要フェイルセーフ・v3.0から不変）

```python
def update_trailing_sl(position_info, current_price: float,
                       atr_value: float, atr_multiplier: float = 2.0):
    """
    トレーリングSLの新価格を返す。有利方向へ動く場合のみ更新値を返す。

    BUYポジション:
      new_sl = current_price - (atr_value × atr_multiplier)
      new_sl > position_info.current_sl → new_sl を返す（更新）
      それ以外 → None を返す（更新しない）

    SELLポジション: v3.2はロングのみのため ValueError を raise。
    """
    if position_info.direction != "BUY":
        raise ValueError(f"v3.2 supports BUY only. Got: {position_info.direction}")
    new_sl = current_price - (atr_value * atr_multiplier)
    return new_sl if new_sl > position_info.current_sl else None
```

#### calculate_initial_lot（v3.2：銘柄別 volume_min / volume_step）

```python
import math

def calculate_initial_lot(entry_price: float, sl_price: float,
                          account_balance: float, lot_value_per_unit: float,
                          volume_min: float, volume_step: float,
                          max_risk_pct: float = 1.5) -> float:
    """
    新規エントリー時のロット数を計算する。

    計算式：
      許容損失額 = account_balance × max_risk_pct / 100
      価格リスク = abs(entry_price - sl_price)
      raw_lot   = 許容損失額 / (価格リスク × lot_value_per_unit)

    丸め（v3.2：0.01固定ではなく銘柄別）：
      lot = floor(raw_lot / volume_step) × volume_step
      lot < volume_min の場合は 0 を返す（発注しない）
      ※ symbols.yaml の volume_min / volume_step を必ず渡すこと。

    TPは計算しない（v3.2はTPなし設計）。
    """
    pass
```

> `calculate_scale_in_lot` / `recalculate_sl_after_scale` / `can_scale_in` は v3.0 の仕様を踏襲。
> ただしロット丸めは全て銘柄別 `volume_min` / `volume_step` を使用すること。
> `can_scale_in` の拒否条件（含み益なし / 2.5倍上限 / 合計リスク超過）は不変。

---

### 3.4 order_executor.py（v3.2：ヘッジング前提を明記）

- 口座は `margin_mode=2`（HEDGING、Phase 0確認済）。同一シンボルに複数チケットが並存しうる。
  部分決済・SLTP更新は**チケット単位**で行うこと（`position` にチケットIDを指定）。
- 発注シンボルは symbols.yaml の `mt5_symbol`（会社名）。ティッカーをそのままMT5へ渡さない。

#### update_sl_only（トレーリングSL反映用・v3.0から不変）

```python
def update_sl_only(mt5_symbol: str, position_ticket: int, new_sl: float) -> dict:
    """既存ポジションのSLのみ更新。TPは0（TPなし設計）。ロットは変えない。
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "symbol": mt5_symbol, "sl": new_sl, "tp": 0.0,
        "position": position_ticket,
    }
    戻り値: {"success": bool, "retcode": int, "comment": str}
    ※ update_trailing_sl が float を返したときのみ呼ぶ。None（更新不要）なら呼ばない。
    """
    pass
```

#### 発注バリデーション（v3.2）

```python
def validate_order(order_params: dict, portfolio_state) -> tuple[bool, str]:
    """1つでも失敗したら(False, 理由)。全合格で(True, "OK")。
    チェック順：
    1. sl が設定されていること
    2. "tp" が無い、または tp == 0.0（tp>0 は禁止）
    3. lot_size >= 当該銘柄の volume_min（symbols.yaml）
    4. 新規BUY: portfolio_state.available_slots() > 0
    5. 新規BUY: 同一セクターの保有数 < max_positions_per_sector(3)
    6. INCREASE: can_scale_in() が True
    7. 保有中銘柄への新規BUY発注を検知したらエラーログ（二重発注防止）
    """
    pass
```

---

### 3.5 signal_filter.py（v3.2：レーティング階層ゲート＋フリップフロップ防止）

```python
def apply(ticker: str, decision: dict, signal_history: dict, gate_cfg: dict) -> dict:
    """
    シグナルにフィルタを適用する。引っかかればHOLDに変換して返す。

    signal_history: {ticker: [最新のdecision, ...]} 新しい順

    ルール1（レーティング階層ゲート ← 旧confidenceフィルタの置換）:
      parse_decision で既にレーティング→アクション変換済み。
      ここでは gate_cfg（risk.yaml signal_gate）に基づく最終妥当性を確認する。
      - action が BUY なのに rating が new_entry_ratings に無い → HOLD
      - action が INCREASE なのに rating が scale_in_ratings に無い → HOLD
      （通常 parse_decision と整合するが、二重防御として確認する）

    ルール2（フリップフロップ防止・v3.0踏襲）:
      (BUY/INCREASE) ↔ (DECREASE/CLOSE) の方向転換は
      direction_change_consecutive_required 回連続で同方向が出た場合のみ採用。
      連続要件を満たさなければ HOLD に変換する。

    重要: signal_history.json（本フィルタ用）と TradingAgents 本体の
    TradingMemoryLog（LLM文脈用）は別物。本フィルタは signal_history.json のみ参照する（§6）。
    """
    CONSECUTIVE = gate_cfg.get("direction_change_consecutive_required", 2)
    action = decision["action"]
    rating = decision.get("rating", "Hold")

    # ルール1: レーティング階層ゲート
    if action == "BUY" and rating not in gate_cfg["new_entry_ratings"]:
        return {**decision, "action": "HOLD",
                "filter_reason": f"rating {rating} not allowed for new entry"}
    if action == "INCREASE" and rating not in gate_cfg["scale_in_ratings"]:
        return {**decision, "action": "HOLD",
                "filter_reason": f"rating {rating} not allowed for scale-in"}

    # ルール2: フリップフロップ防止
    history = signal_history.get(ticker, [])
    if not history:
        return decision

    buy_side  = {"BUY", "INCREASE"}
    exit_side = {"DECREASE", "CLOSE"}
    last_action = history[0]["action"]
    cur_buy  = action in buy_side
    cur_exit = action in exit_side
    last_buy  = last_action in buy_side
    last_exit = last_action in exit_side

    is_change = (cur_buy and last_exit) or (cur_exit and last_buy)
    if is_change:
        needed = CONSECUTIVE - 1
        same = sum(
            1 for h in history[:needed]
            if (cur_buy and h["action"] in buy_side) or
               (cur_exit and h["action"] in exit_side)
        )
        if same < needed:
            return {**decision, "action": "HOLD",
                    "filter_reason": f"direction change needs {CONSECUTIVE} consecutive signals"}

    return decision
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

> **夏冬時間（DST）対応（v3.2）：** TradingAgents へ渡す `trade_date` は実行時刻（JST）ではなく
> `core/market_clock.last_completed_us_trading_day()` が返す「直近に確定した米国取引日」を使う。
> `zoneinfo("America/New_York")` により EDT/EST を自動判定するため、実行時刻に依存せず両季節へ対応する。
> 冬時間は米国クローズ（16:00 EST ＝ 6:00 JST）が起動時刻と重なり当日D1足が未確定になり得るため、
> クローズ前起動時は前営業日を返す（未確定バーでの判断を回避）。祝日は非対応（データ側フォールバック前提）。
> なお推奨としてタスクスケジューラ起動は通年 **7:00 JST** が安全。

**ロット価値の算出（v3.2・実装者への警告）：**
`lot_value_per_unit` は `symbol_info.trade_tick_value / trade_tick_size` で求めては**いけない**。
XM米株CFDの `trade_tick_value` は利益通貨（USD）建てで返り、JPY口座では約160倍過大なロットになる。
必ず通貨変換を含む `mt5.order_calc_profit(ORDER_TYPE_BUY, symbol, 1.0, price, price-1.0)` の絶対値
（＝口座通貨JPYでの $1 変動あたり損益）を使うこと（`scheduler._lot_value_per_unit` 実装済み）。

### 4.2 screener.py の除外条件

```
除外条件1（コスト異常）: spread / ATR(14) > 25%
  理由: XM 米株CFD の実測スプレッド分布（39サンプル）から統計的に決定。
    中央値 20%, Q1 15%, Q3 25% であり、上位 10-15% の外れ値（38%, 50%等）を除外しつつ、
    AI分析価値のある約 87% の銘柄を通す。条件1での除外下限が 5% では
    市場活況中でも全銘柄が引っかかり、分析が永遠に実行されない。
    
除外条件2（流動性異常）: 直近5日の平均出来高 < 通常の50%
除外条件3（動きなし）: 直近1日の価格変動 < ±0.5%

保有中の銘柄はこのスクリーニングの対象外（必ず分析する）。
条件3のみ: 「no movement」ログを記録してHOLD維持。
条件1または2: 「screened out」ログを記録。
```

---

## 5. リスク管理の全ルール一覧（優先度順・v3.2）

| 優先度 | ルール             | 発動条件                              | 処置                                       |
| ------ | ------------------ | ------------------------------------- | ------------------------------------------ |
| 1      | 週次損失リミット   | weekly_pnl < -(残高×10%)             | 即時全処理停止（週次リセットまで）         |
| 2      | 日次損失リミット   | daily_pnl < -(残高×5%)               | 当日の新規発注を全停止                     |
| 3      | 証拠金維持率       | margin_level < 300%                   | 新規発注を全停止（既存SLはそのまま）       |
| 4      | SL必須チェック     | SL未設定の発注                        | 発注を拒否・ERRORログ                      |
| 5      | TP禁止チェック     | tp > 0 の発注                         | 発注を拒否・ERRORログ                      |
| 6      | 最大ポジション数   | 保有数=6 で新規BUY                    | BUYを拒否・WARNログ                        |
| 7      | セクター集中制限   | 同一セクター3銘柄保有中に4つ目のBUY   | BUYを拒否・WARNログ                        |
| 8      | ナンピン禁止       | 含み損中のINCREASE                    | INCREASEを拒否・HOLD変換                   |
| 9      | SL不利更新禁止     | new_sl <= current_sl（BUYの場合）     | 更新しない・INFOログ                       |
| 10     | レーティングゲート | BUY/INCREASE だがレーティングが許可外 | HOLDに変換・INFOログ（旧confidenceの置換） |
| 11     | 連続シグナル要件   | 方向転換シグナルが1回のみ             | HOLDに変換・INFOログ                       |
| 12     | スケーリング上限   | 買い増し後にinitial×2.5超            | ロット数を上限まで圧縮                     |
| 13     | ロット下限         | 丸め後 lot < 銘柄別 volume_min        | 発注しない（0扱い）・INFOログ              |

> v3.0の「確信度フィルタ（confidence < 0.6）」は**廃止**。数値confidenceが本体に存在しないため、
> レーティング階層ゲート（優先度10）で置換した。

---

## 6. メモリ・履歴の役割分担（v3.2）

本システムには2つの独立した履歴管理が共存する。混同してはいけない。

| 項目           | TradingAgents 本体メモリ（`TradingMemoryLog` / `past_context`） | data/signal_history.json                     |
| -------------- | ------------------------------------------------------------------- | -------------------------------------------- |
| 管理主体       | TradingAgents内部（`memory.py`）                                  | 本システム（scheduler.py）                   |
| 目的           | LLMが過去の判断・結果を文脈として参照する自己学習用メモリ           | フリップフロップ防止・連続シグナルチェック用 |
| 使用箇所       | Portfolio Manager プロンプトの`past_context`                      | signal_filter.apply() のみ                   |
| 更新タイミング | propagate 実行時に本体が自動管理（reflection含む）                  | scheduler.pyが発注後に更新                   |
| 参照方法       | 本体が内部で自動参照                                                | signal_filter.pyが明示的に読み込む           |

**AIコーディングエージェントへの注意：**

- 本体メモリ（`past_context` / `TradingMemoryLog`）を signal_filter.py で参照してはいけない
- signal_history.json を TradingAgents の設定に渡してはいけない
- 2つを統合・マージするコードを書いてはいけない

---

## 7. データ永続化の仕様

### 7.1 data/signal_history.json

```json
{
  "NVDA": [
    {
      "date": "2026-06-30",
      "action": "INCREASE",
      "rating": "Overweight",
      "converted_from": "Overweight",
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
  "Nvidia": {
    "ticker": "NVDA",
    "initial_lots": 0.10,
    "scale_count": 1,
    "entry_date": "2026-06-25",
    "scale_history": [
      {"date": "2026-06-28", "lots": 0.06, "price": 198.50}
    ]
  }
}
```

> キーは MT5 実シンボル名（会社名）。ヘッジ口座のためチケット紐付けは position/order 基準で保持する。

---

## 8. ログ仕様

```
フォーマット: [YYYY-MM-DD HH:MM:SS] [LEVEL] [MODULE] MESSAGE

出力例（v3.2 / spreead/ATR閾値25%）:
[2026-07-01 06:00:01] [INFO]  [ACCOUNT_GUARD]   Limits OK. Trading allowed.
[2026-07-01 06:00:02] [INFO]  [PORTFOLIO_STATE]  Synced 3 positions from MT5 (hedging).
[2026-07-01 06:00:03] [INFO]  [RISK_MANAGER]     Nvidia: trailing SL updated $182.00 → $184.50
[2026-07-01 06:00:03] [INFO]  [RISK_MANAGER]     Microsoft: trailing SL not updated (new_sl <= current_sl)
[2026-07-01 06:00:03] [INFO]  [ORDER_EXEC]       Nvidia: SLTP updated. SL=$184.50 TP=0
[2026-07-01 06:00:05] [INFO]  [SCREENER]         Caterpillar: 条件1(spread/ATR: 8.2%) - 分析対象に含まれる
[2026-07-01 06:00:05] [INFO]  [SCREENER]         Linde: screened out (spread/ATR: 31.5%) - 異常スプレッド
[2026-07-01 06:02:10] [INFO]  [TA_RUNNER]        NVDA: rating=Overweight (holding) → INCREASE
[2026-07-01 06:02:10] [INFO]  [TA_RUNNER]        Linde: rating=Hold → HOLD
[2026-07-01 06:02:10] [WARN]  [SIGNAL_FILTER]    Microsoft: direction change filtered. Need 2 consecutive.
[2026-07-01 06:02:11] [WARN]  [RISK_MANAGER]     NVDA: scale-in rejected. No unrealized profit.
[2026-07-01 06:02:12] [INFO]  [ORDER_EXEC]       ExxonMobil: BUY 0.05 lot @ $136.90 SL=$132.00 TP=none
[2026-07-01 06:02:13] [ERROR] [ORDER_EXEC]       ExxonMobil: MT5 retcode 10006 - request rejected
[2026-07-01 06:02:14] [WARN]  [ORDER_EXEC]       Visa: rejected - tp > 0 is forbidden in v3.2
[2026-07-01 06:02:20] [INFO]  [NOTIFIER]         Discord webhook sent (cycle summary).
```

---

## 9. .env ファイルの仕様（v3.2）

```bash
# --- MT5 接続設定 (デモ口座) ---
MT5_PATH=C:\Program Files\XMTrading MT5\terminal64.exe
MT5_LOGIN=<demo_login>
MT5_PASSWORD=<demo_password>
MT5_SERVER=XMTrading-MT5 3        # Phase 0で一致確認済み

# --- LLM / データ ---
OPENAI_API_KEY=sk-...
ALPHA_VANTAGE_API_KEY=...         # 任意。既定 data_vendors は yfinance
# FRED_API_KEY=...                # マクロニュース(get_macro_indicators)を使う場合

# --- 通知（LINE Notify終了のためDiscordを使用）---
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# --- TradingAgents 環境変数オーバーライド（任意・DEFAULT_CONFIGに反映）---
# TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.5
# TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.4-mini
# TRADINGAGENTS_CHECKPOINT_ENABLED=true
# TRADINGAGENTS_OUTPUT_LANGUAGE=Japanese

# --- パス ---
LOG_DIR=./logs
SIGNAL_HISTORY_PATH=./data/signal_history.json
POSITION_META_PATH=./data/position_meta.json
```

---

## 10. 開発フェーズ

| フェーズ | 実装内容                                                                 | 完了条件                                                                   |
| -------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| Phase 0  | 実機調査（済）                                                           | 本設計書v3.2に反映済み（`Phase0_判定レポート.md`）                       |
| Phase 1  | mt5_client / portfolio_state / market_data                               | デモ口座のポジション・残高・ATRが実シンボル（会社名）で取得できる          |
| Phase 2  | tradingagents_patch / tradingagents_runner（シグナル出力のみ・発注なし） | 15銘柄のレーティングがエラーなく並列取得でき、注入文脈がプロンプトに届く   |
| Phase 3  | signal_filter / risk_manager / account_guard の全unit test               | 全リスクルール・トレーリングSLフェイルセーフ・銘柄別ロット丸めのテスト通過 |
| Phase 4  | order_executor（デモ口座で実発注）                                       | BUY・SLTP更新・CLOSE・部分決済がヘッジ口座で正常動作                       |
| Phase 5  | scheduler.pyでフルサイクル自動実行                                       | 1ヶ月以上デモで安定稼働後、結果を評価してリアル移行を検討                  |

---

## 11. AIコーディングエージェントへの実装禁止事項（v3.2）

1. **TPを設定した発注コードを書いてはいけない。** validate_order が tp>0 を拒否する。迂回禁止。
2. **SLを不利方向に動かすトレーリングSL更新を書いてはいけない。** update_trailing_sl が None のとき update_sl_only を呼ばない。
3. **ナンピンロジックを実装してはいけない。** 含み損中のINCREASEは can_scale_in が False を返す。迂回禁止。
4. **SLなし発注を通してはいけない。** デモでもSL省略禁止。
5. **TradingAgentsの出力を直接発注に使ってはいけない。** parse_decision → signal_filter → risk_manager → order_executor の全段階を経ること。
6. **本体メモリ（past_context/TradingMemoryLog）と signal_history.json を混同・統合してはいけない。**（§6）
7. **保有中の銘柄に新規BUY発注を出してはいけない。** parse_decisionの変換が機能していれば起きないが、order_executor側でも検知したらエラーログ。
8. **リアル口座への接続コードをPhase5より前に実装しないこと。**
9. **`config["portfolio_context"]` 等の独自configキーで文脈注入してはいけない。**（黙殺される。§0.5.2の resolve_instrument_context ラップを使う）
10. **`await ta.propagate(...)` と書いてはいけない。** propagate は同期。並列化は `asyncio.to_thread` 経由。
11. **BUY/SELL/HOLD の3値前提でパーサを書いてはいけない。** 判断は5段階レーティング文字列。
12. **ロット丸めを 0.01 固定で書いてはいけない。** 銘柄別 volume_min / volume_step（symbols.yaml）を使う。
13. **TradingAgents本体ソースを恒久的に書き換えてはいけない。** 文脈注入は monkey-patch（resolve_instrument_context ラップ）で行い、本体ファイルは改変しない。
14. **MT5へティッカー（NVDA等）をそのまま渡してはいけない。** 実シンボルは会社名（symbols.yaml の mt5_symbol）。

---

## 付録A. Phase 0 で確定した15銘柄 実シンボル対応表（実装時の正）

| ticker | mt5_symbol    | セクター               | サブ業種            | volume_min | volume_step | trade_mode | D1×20 |
| ------ | ------------- | ---------------------- | ------------------- | ---------- | ----------- | ---------- | ------ |
| NVDA   | Nvidia        | Information Technology | 半導体              | 0.04       | 0.01        | 4          | OK     |
| MSFT   | Microsoft     | Information Technology | ソフトウェア        | 0.02       | 0.01        | 4          | OK     |
| GOOGL  | Google        | Communication Services | ネット広告/メディア | 0.03       | 0.01        | 4          | OK     |
| AMZN   | Amazon        | Consumer Discretionary | EC                  | 0.03       | 0.01        | 4          | OK     |
| KO     | Coca-Cola     | Consumer Staples       | 飲料                | 0.11       | 0.01        | 4          | OK     |
| PG     | Procter&Gam   | Consumer Staples       | 生活用品            | 0.06       | 0.01        | 4          | OK     |
| JNJ    | J&J           | Health Care            | 医薬品              | 0.04       | 0.01        | 4          | OK     |
| UNH    | UnitedHealth  | Health Care            | 医療保険            | 0.03       | 0.01        | 4          | OK     |
| JPM    | JPMorgan      | Financials             | 銀行                | 0.03       | 0.01        | 4          | OK     |
| V      | Visa          | Financials             | 決済ネットワーク    | 0.03       | 0.01        | 4          | OK     |
| CAT    | Caterpillar   | Industrials            | 建設機械            | 0.02       | 0.01        | 4          | OK     |
| XOM    | ExxonMobil    | Energy                 | 総合石油            | 0.05       | 0.01        | 4          | OK     |
| LIN    | Linde         | Materials              | 産業ガス            | 0.02       | 0.01        | 4          | OK     |
| NEE    | NextEra       | Utilities              | 電力                | 0.09       | 0.01        | 4          | OK     |
| AMT    | AmericanTower | Real Estate            | 通信REIT            | 0.05       | 0.01        | 4          | OK     |

> 全銘柄 contract_size=10 / currency_profit=USD / digits=2。口座通貨=JPY、margin_mode=2（HEDGING）。
> シンボルの厳密同定は `description` の Reuters コード（例 `(NVDA.OQ)`）と group path=`Stocks\US\...` で行うこと。
> GOOGL の実シンボル `Google` は description が `GOOG.OQ`（クラスC）。クラスA厳密一致が必要な場合は要再確認。
