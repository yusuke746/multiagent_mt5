# Phase 0 判定レポート：TradingAgents連携方式とMT5シンボル実機確認

> 実施日: 2026-07-01 / OS: Windows / Python 3.13.7 / MetaTrader5 5.0.5488
> 本レポートは調査のみ。本体（Phase1以降）は未実装。TradingAgents本体ソースは恒久改変していない（monkey-patch実証は一時スクリプトで実施後に破棄）。

---

## タスクA：TradingAgents本体ソース調査

### A-0. リポジトリ取得
- リポジトリ: `https://github.com/TauricResearch/TradingAgents`（`multiagent_mt5/TradingAgents` にclone、Phase1参照用に保持）
- 最新コミット: `85946c2f60768ab2dae23a5a36cd927662feef94 2026-06-22 02:05:07 +0000`
- ライセンス: **Apache License 2.0**（確認済み）

> ⚠️ 重要：現行版は設計書v3.1が前提としていた旧構造から**大幅に変更**されている（構造化出力・5段階レーティング導入版）。v3.1の複数の前提が実機と食い違う。

### A-1. ディレクトリ構造（主要部）
```
tradingagents/
  agents/
    analysts/    market_analyst.py / news_analyst.py / sentiment_analyst.py / fundamentals_analyst.py / social_media_analyst.py
    managers/    portfolio_manager.py  research_manager.py
    researchers/ bull_researcher.py  bear_researcher.py
    risk_mgmt/   aggressive_debator.py  conservative_debator.py  neutral_debator.py
    trader/      trader.py
    schemas.py            ← Pydantic構造化出力スキーマ（新規）
    utils/       agent_utils.py / structured.py / rating.py / memory.py ほか
  graph/
    trading_graph.py      ← propagate() 定義
    signal_processing.py  ← 最終シグナル抽出
    checkpointer.py       ← checkpoint_enabled 実装
    propagation.py / setup.py / conditional_logic.py / reflection.py
  llm_clients/  factory.py / openai_client.py / anthropic_client.py / google_client.py ほか
  default_config.py
```
- Portfolio Manager: `tradingagents/agents/managers/portfolio_manager.py` の `create_portfolio_manager()`
- Trader: `tradingagents/agents/trader/trader.py` の `create_trader()`

### A-2.【最重要】プロンプト保持方式の判定

| エージェント | 保持方式 |
| --- | --- |
| 各アナリスト（market/news/sentiment/fundamentals） | **②テンプレート型** `ChatPromptTemplate.from_messages([...])` + `.partial(system_message=...)` |
| Trader | **①関数生成型 × ③直書き複合** `create_trader(llm)` 内の `trader_node` でinline dictメッセージをf-stringで組立 |
| Portfolio Manager | **①関数生成型 × ③直書き複合** `create_portfolio_manager(llm)` 内でf-stringプロンプト直書き |

**ただし、より重要な発見：公式のクリーンな注入チャネルが存在する。**

Trader・PM の両プロンプトは、共通ヘルパー `get_instrument_context_from_state(state)`（`agent_utils.py`）を呼び、`state["instrument_context"]` の文字列をプロンプトに埋め込む。この `instrument_context` は実行開始時に `TradingAgentsGraph.resolve_instrument_context()` → `build_instrument_context()` で生成され初期stateに注入される。

→ **プロンプト文字列を直接monkey-patchする必要はない。`build_instrument_context()`（または `resolve_instrument_context()`）を `functools.wraps` でラップして末尾にポートフォリオ文脈を追記すれば、Trader と PM の両方に公式チャネル経由で届く。** これは設計書v3.1のプロンプト直接パッチ案より安全・低リスク。

- 注入対象（推奨）: `tradingagents/agents/utils/agent_utils.py` の `build_instrument_context()`
- 消費箇所: 同ファイル `get_instrument_context_from_state()`（Trader/PM が呼ぶ）

### A-3. `propagate` の実装と戻り値
- `def propagate(self, company_name, trade_date, asset_type="stock")` → **同期（`def`）**。非同期ではない。
  → 並列実行には `asyncio.to_thread()` が必要（設計書の `await ta.propagate(...)` は不可）。
- 戻り値: `return final_state, self.process_signal(final_state["final_trade_decision"])` の**タプル**。
- 第2戻り値 `decision` の型: **文字列（str）**。`process_signal()`→`parse_rating()` が返すのは **5段階レーティング語**：`Buy` / `Overweight` / `Hold` / `Underweight` / `Sell`。
  → 設計書の「BUY/SELL/HOLD、またはdict」という前提は**誤り**。dictでもBUY/SELL/HOLDでもない。
- 第1戻り値 `final_state`: 各アナリストのレポート全文・研究計画・トレーダー提案・PM判断（markdown）を含む dict。

### A-4. confidence 抽出可能性
- 最終判断 `PortfolioDecision`（schemas.py）のフィールド: `rating` / `executive_summary` / `investment_thesis` / `price_target?` / `time_horizon?`。**confidence は無い。**
- `TraderProposal`: `action`(Buy/Hold/Sell) / `reasoning` / `entry_price?` / `stop_loss?` / `position_sizing?`。**confidence は無い。**
- confidence 相当が存在するのは **Sentiment Analyst のみ**（`SentimentReport.confidence` = `low/medium/high`、`overall_score` = 0–10）。これは銘柄全体の最終確信度ではない。
- 結論：判断レベルの数値confidenceは**取得不可（C: フィルタ廃止が現実的）**。代替として、5段階レーティング自体が確信度を内包（Buy=強気確信、Overweight=順風、Underweight=減、Sell=退出）するため、**レーティング階層ゲートで置換**するのが妥当。

### A-5. config と `checkpoint_enabled`
- `DEFAULT_CONFIG` 主要キー: `llm_provider` / `deep_think_llm` / `quick_think_llm` / `backend_url` / `temperature` / `max_debate_rounds` / `max_risk_discuss_rounds` / `max_recur_limit` / `checkpoint_enabled` / `output_language` / `data_vendors` / `tool_vendors` / `benchmark_ticker` ほか。
- **未定義キー（例 `portfolio_context`）の扱い**: config は素の dict。`set_config()` で保存されるがコード中の**どこからも読まれない＝黙殺**。エラーにはならない（grep で参照箇所ゼロを確認）。
  → **設計書v3.1の「config["portfolio_context"] で注入」は機能しない。** A-2の `instrument_context` チャネルを使うこと。
- `checkpoint_enabled`: **実在**（`DEFAULT_CONFIG` 既定 `False`、`graph/checkpointer.py` で実装）。v3.1の該当設定は有効。
- モデル既定値: `deep_think_llm="gpt-5.5"`、`quick_think_llm="gpt-5.4-mini"`。設計書の `gpt-5.4` は要更新。

### A-6. monkey-patch 実証テスト
- パターン①②複合のため実施。ただし全パイプラインのLLM実行（有料・低速）ではなく、**A-2で特定した公式注入チャネルに対する決定論的テスト**を実施（LLM呼び出し・ネットワーク不要・ゼロコスト）。
- 手順: `build_instrument_context()` を `functools.wraps` でラップし末尾に検証トークン `PORTFOLIO_INJECTION_TEST_TOKEN_XYZ` を追記 → 初期stateを構築 → `get_instrument_context_from_state(state)`（Trader/PMが呼ぶ関数）の戻り値にトークンが含まれるか確認。
- 結果:
  - パッチ適用: **成功（例外なし）**
  - 検証トークンがプロンプト消費層に到達: **YES**
  - 実際にTrader/PMが受け取る文字列にトークンが出現することを確認済み。
- 判定: **A'案第1層は採用可能**（プロンプト直接パッチではなく `build_instrument_context` ラップ方式で）。

---

## タスクB：MT5（XMTrading）実シンボル確認

### B-1. 接続・アカウント
- ログイン: **成功**（Login `75562581`）
- サーバー: `XMTrading-MT5 3`（`.env` の `MT5_SERVER` と一致）
- 口座通貨: **JPY**
- 取引許可: True
- **margin_mode = 2 = HEDGING**（ヘッジング口座を確認 → マルチチケット集約前提の裏取りOK）

### B-2/B-3/B-4. シンボル対応表・スペック・D1

> ⚠️ 最重要：XMTradingのMT5シンボルは **`AAPLm` 形式ではなく「会社名」**（group path=`Stocks\US\...`）。設計書v3.1の `mt5_symbol` 例示（AAPLm等）は**全件誤り**。

| ticker | 実MT5シンボル | 説明 | volume_min | volume_step | volume_max | digits | contract | trade_mode | D1×20 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AAPL | `Apple` | Apple Inc (AAPL.OQ) | 0.03 | 0.01 | 39.0 | 2 | 10 | 4 (Full) | OK |
| MSFT | `Microsoft` | Microsoft Corp (MSFT.OQ) | 0.02 | 0.01 | 27.0 | 2 | 10 | 4 | OK |
| GOOGL | `Google` | Alphabet Inc (**GOOG**.OQ) | 0.03 | 0.01 | 34.0 | 2 | 10 | 4 | OK |
| AMZN | `Amazon` | Amazon.com Inc (AMZN.OQ) | 0.03 | 0.01 | 48.0 | 2 | 10 | 4 | OK |
| NVDA | `Nvidia` | NVIDIA Corp (NVDA.OQ) | 0.04 | 0.01 | 57.0 | 2 | 10 | 4 | OK |
| META | `Facebook` | META PLATFORMS INC (META.OQ) | 0.02 | 0.01 | 17.0 | 2 | 10 | 4 | OK |
| TSLA | `Tesla` | Tesla Inc (TSLA.OQ) | 0.02 | 0.01 | 28.0 | 2 | 10 | 4 | OK |
| JPM | `JPMorgan` | JPMorgan Chase & Co (JPM.N) | 0.03 | 0.01 | 34.0 | 2 | 10 | 4 | OK |
| JNJ | `J&J` | Johnson & Johnson (JNJ.N) | 0.04 | 0.01 | 41.0 | 2 | 10 | 4 | OK |
| V | `Visa` | Visa Inc (V.N) | 0.03 | 0.01 | 33.0 | 2 | 10 | 4 | OK |
| WMT | `WalMart` | Walmart Inc (WMT.OQ) | 0.07 | 0.01 | 80.0 | 2 | 10 | 4 | OK |
| XOM | `ExxonMobil` | Exxon Mobil Corp (XOM.N) | 0.05 | 0.01 | 62.0 | 2 | 10 | 4 | OK |
| UNH | `UnitedHealth` | UnitedHealth Group Inc (UNH.N) | 0.03 | 0.01 | 18.0 | 2 | 10 | 4 | OK |
| PG | `Procter&Gam` | Procter & Gamble Co (PG.N) | 0.06 | 0.01 | 70.0 | 2 | 10 | 4 | OK |
| MA | `Mastercard` | Mastercard Inc (MA.N) | 0.02 | 0.01 | 10.0 | 2 | 10 | 4 | OK |

補足事実：
- 全15銘柄 `trade_mode=4`（SYMBOL_TRADE_MODE_FULL、全取引可）。取引不可銘柄は**なし**。
- 全銘柄 `contract_size=10`、`currency_profit=USD`、`digits=2`。
- **`volume_min` は銘柄ごとに異なる（0.02〜0.07）。設計書の「最小ロット0.01」は誤り。** `volume_step` は全銘柄 **0.01** で一致。
- D1足は全銘柄20本取得可（最新確定足は 2026-06-30 / 07-01）。ATR(14)・トレーリングSLの前提OK。
- 注意: `GOOGL` の実シンボル `Google` は説明が `GOOG.OQ`（クラスC）。クラスA(GOOGL)厳密一致が必要なら要再確認。
- 注意: 同名部分一致が多数存在（例 `META` キーワードは Rheinmetall 等を誤ヒット）。実シンボルは必ず `path=Stocks\US\...` と `description` の Reuters コード（例 `(AAPL.OQ)`）で確定すること。

---

## タスクC：通知手段
- `.env` に `DISCORD_WEBHOOK_URL` が既に設定済み。
- 採用: **Discord Webhook**（LINE Notify は2025年3月末終了のため代替確定）。発行・設定は完了済み（URLは `.env` 管理）。

---

## 最終判定

### C-1. TradingAgents連携判定
| # | 確認項目 | 結果 | 設計への影響 |
| --- | --- | --- | --- |
| 1 | プロンプト保持方式 | アナリスト=②、Trader/PM=①×③複合。ただし公式注入口 `instrument_context` あり | A'案第1層は**採用可**（プロンプト直パッチ不要） |
| 2 | 注入対象の関数・パス | `agent_utils.py` `build_instrument_context()`（消費: `get_instrument_context_from_state()`） | ここを `functools.wraps` でラップ |
| 3 | monkey-patch実証（トークン到達） | **YES**（例外なし・決定論的確認） | 第1層採用の最終判定＝可 |
| 4 | `propagate` 同期/非同期 | **同期（def）** | `asyncio.to_thread` 必須。`await propagate` は不可 |
| 5 | `decision` の型 | **文字列**（5段階 Buy/Overweight/Hold/Underweight/Sell） | parse_decision全面改修。BUY/SELL/HOLD前提を破棄 |
| 6 | `final_state` にレポート含むか | **あり**（全アナリスト＋PM判断） | confidence以外の定性抽出は可能 |
| 7 | confidence取得方針 | **C（判断レベルは取得不可）**。数値はSentimentのみ | confidenceフィルタ廃止 → レーティング階層ゲートで代替 |
| 8 | 未定義configキーの扱い | **黙殺**（保存はされるが読まれない・非エラー） | `config["portfolio_context"]` 注入は**不可** |
| 9 | `checkpoint_enabled` の実在 | **あり**（既定False） | v3.1設定は有効・維持 |
| 10 | 指定可能なモデル名 | 既定 `deep=gpt-5.5` / `quick=gpt-5.4-mini` | risk/config 記載値を更新 |

### C-2. MT5シンボル判定
| # | 確認項目 | 結果 |
| --- | --- | --- |
| 1 | 接続・ログイン | **成功** |
| 2 | サーバー名 | `XMTrading-MT5 3`（.env一致） |
| 3 | margin_mode | **2 = HEDGING**（確認） |
| 4 | 15銘柄の実シンボル対応表 | 上表参照（**全て会社名**。`AAPLm` 形式は不使用） |
| 5 | volume_min/step は全銘柄0.01か | **NO**。min は 0.02〜0.07 で銘柄毎に異なる。step は全銘柄0.01 |
| 6 | D1足20本取得可否 | **全銘柄OK** |
| 7 | 取引不可銘柄 | **なし**（全15銘柄 trade_mode=4） |

### C-3. 設計変更の要否

1. **A'案（プロンプト注入・第1層）は採用可能か？** → **可能**。ただし方式変更：設計書のような各エージェントプロンプトの直接パッチではなく、`build_instrument_context()` を `functools.wraps` でラップし、`portfolio_state.to_llm_context()` の文字列を追記する方式にする。これが Trader・PM 両方に公式 `instrument_context` チャネル経由で届くことを実証済み。`config["portfolio_context"]` 経由の注入は**黙殺されるため廃止**。

2. **confidenceフィルタは維持か廃止か？** → **廃止**。最終判断に数値confidenceは存在せず、取得手段がない。代わりに `signal_filter` の `min_confidence` ゲートを、5段階レーティングの階層ゲート（例：新規エントリーは `Buy`/`Overweight` のみ、`Hold` は無視、`Underweight`/`Sell` は保有中のみ DECREASE/CLOSE にマップ）へ置換する。

3. **symbols.yaml の mt5_symbol を例示→実値へ全件更新**（下表が確定値）:
   `AAPL→Apple, MSFT→Microsoft, GOOGL→Google, AMZN→Amazon, NVDA→Nvidia, META→Facebook, TSLA→Tesla, JPM→JPMorgan, JNJ→J&J, V→Visa, WMT→WalMart, XOM→ExxonMobil, UNH→UnitedHealth, PG→Procter&Gam, MA→Mastercard`
   さらに各銘柄に実測 `volume_min` を保持し、`calculate_initial_lot` の丸めは 0.01固定ではなく銘柄別 `volume_min`/`volume_step` を参照すること。

4. **設計書v3.1で修正が必要な項目リスト**:
   - §2.1 `symbols.yaml`：`mt5_symbol` を全件実値化（会社名）。`volume_min` フィールド追加。
   - §3.2 `tradingagents_runner`：
     - `parse_decision` を **5段階レーティング文字列**入力前提に全面改修（BUY/SELL/HOLD/dict前提を撤廃）。
     - `propagate` は同期 → `run_single` で `asyncio.to_thread(ta.propagate, ...)` に変更（`await ta.propagate` 不可）。
     - ポートフォリオ注入は `config["portfolio_context"]` ではなく `build_instrument_context` ラップ方式へ。
     - モデル名 `gpt-5.4`→既定 `gpt-5.5`/`gpt-5.4-mini`（または明示指定）。
   - §3.3/§3.5 `risk_manager`/`calculate_initial_lot`：最小ロット丸めを銘柄別 `volume_min`/`volume_step` に。
   - §3.4 `order_executor`：ヘッジング口座前提でOK（margin_mode=2確認済）。
   - §2.2 `signal_filter.min_confidence`：数値confidence廃止 → レーティング階層ゲートへ差し替え。§3.6/§5の確信度ルール（優先度10）も同様に改訂。
   - §3.2 `checkpoint_enabled`：実在するため維持。
   - 通知：LINE→Discord Webhook（`.env` の `DISCORD_WEBHOOK_URL`）。

---

## 禁止事項の遵守
- TradingAgents本体ソースは恒久改変していない（monkey-patch実証は一時スクリプトで実施し破棄済み）。
- リアル口座接続なし・実発注なし（`order_send` 未呼び出し、read-onlyプローブのみ）。
- 確認できない項目は明記（例：GOOGL=GOOGクラス差の厳密一致は要追確認）。
- Phase1以降の本体機能は未実装。
