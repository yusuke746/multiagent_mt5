# TradingAgents × XMTrading 米国株CFD 自動売買システム

TradingAgents（マルチエージェント LLM 分析）の判断を、XMTrading MT5（デモ）の
米国株 CFD 発注へ橋渡しする自動売買システム。設計の全確定仕様は
[TradingAgents_XM_設計書_v3.2.md](TradingAgents_XM_設計書_v3.2.md)、
実機検証の根拠は [Phase0_判定レポート.md](Phase0_判定レポート.md) を参照。

> リスクプロファイルは **「やや攻め」**（per_trade 2.5% / total 12% / per_sector 3、
> サーキットブレーカー daily 5% / weekly 10%）。ロングのみ・TP なし・トレーリング SL。

## セットアップ

```powershell
# 1) 依存パッケージ
pip install -r requirements.txt

# 2) TradingAgents 本体（別リポジトリ）を clone して editable install
#    ※ multiagent_mt5/TradingAgents に clone 済みなら sys.path 経由で自動解決される
git clone https://github.com/TauricResearch/TradingAgents.git
pip install -e ./TradingAgents

# 3) .env を作成（.env.example をコピーして値を埋める）
Copy-Item .env.example .env
```

`.env` には MT5 認証情報・OpenAI API キー・Discord Webhook を設定する
（`.env` は `.gitignore` 済み。**コミット厳禁**）。

## 実行

```powershell
python scheduler.py
```

日本時間 早朝 6:00 に Windows タスクスケジューラ等で `scheduler.py` を起動する想定。

## 処理フロー（1サイクル）

```
[0] 注入パッチ適用（resolve_instrument_context ラップ）
[1] account_guard.check      … 日次/週次損失・証拠金維持率
[2] portfolio_state.sync     … MT5 から保有状態を同期（ヘッジ口座）
[3] トレーリング SL 更新     … 有利方向のみ（分析より先に必ず実行）
[4] screener.run             … spread/ATR・流動性・値動きで一次除外
[5] runner.run_parallel      … propagate を to_thread で並列（同期メソッド）
[6] parse_decision           … 5段階レーティング → BUY/HOLD/INCREASE/DECREASE/CLOSE
[7] signal_filter.apply      … レーティングゲート＋フリップフロップ防止
[8] 発注実行                 … SL のみ設定・TP なし・銘柄別ロット丸め
[9] logger / Discord 通知
```

## ディレクトリ

```
config/     symbols.yaml（15銘柄・11セクター分散） / risk.yaml
core/       mt5_client, market_data, portfolio_state, screener,
            tradingagents_patch, tradingagents_runner, signal_filter,
            risk_manager, order_executor, account_guard, config_loader, persistence
data/       signal_history.json / position_meta.json（実行時生成・gitignore）
logs/       日次ログ（gitignore）
scheduler.py, logger.py, notifier.py
```

## 重要な設計上の注意（Phase 0 確定事実）

- `propagate` は **同期**メソッド。`await propagate` は禁止（`asyncio.to_thread` を使う）。
- 戻り値の signal は **5段階レーティング文字列**（Buy/Overweight/Hold/Underweight/Sell）。
- 最終判断に **confidence は存在しない**（レーティング階層ゲートで代替）。
- 文脈注入は `config["portfolio_context"]` では**効かない**。
  `TradingAgentsGraph.resolve_instrument_context` のラップを使う。
- MT5 シンボルは **会社名**（`Nvidia` / `JPMorgan` 等）。`volume_min` は銘柄ごとに異なる。
