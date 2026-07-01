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

システムは **1 回の起動で 1 サイクル（分析→発注→通知）を実行して終了する**バッチ型。
常駐プロセスではないため、日次の起動は Windows タスクスケジューラで行う。

### 手動起動（動作確認・臨時実行）

```powershell
cd multiagent_mt5
C:\Python313\python.exe scheduler.py
```

または本番と同じ経路で確認する場合はランナー経由で起動する：

```powershell
cd multiagent_mt5
.\run_cycle.bat
```

### 起動タイミング（米国セッション中）

米国レギュラーセッションは 9:30–16:00 ET。日本時間では夏冬で 1 時間ずれる。

| 期間      | 米セッション（JST） |
| --------- | ------------------- |
| 夏（EDT） | 22:30 – 翌 5:00    |
| 冬（EST） | 23:30 – 翌 6:00    |

新規エントリーを成立させるには**市場オープン中**に実行する必要がある
（クローズ中はスプレッド/ATR フィルタで screener が全銘柄を除外＝正常動作だが新規は建たない）。
夏冬いずれも寄り付き後でセッション内となる **JST 0:30** を既定の起動時刻とする。
米 月〜金セッションを拾うため、JST では **火〜土** に起動する。

> 分析の基準日 `trade_date` は起動時刻ではなく `market_clock.last_completed_us_trading_day()`
> が返す「直近に確定した米取引日」を使うため、夏冬時間は自動で吸収される。

### 本番環境でのタスクスケジューラ登録

管理者権限の PowerShell で以下を実行する（`run_cycle.bat` は python 実体パスとログ追記を内包）。

```powershell
$action    = New-ScheduledTaskAction  -Execute "C:\path\to\multiagent_mt5\run_cycle.bat"
$trigger   = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Wednesday,Thursday,Friday,Saturday -At 00:30
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
             -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "TA_XM_AutoTrade" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "TradingAgents x XMTrading US-stock CFD daily cycle (JST 0:30 Tue-Sat)" -Force
```

登録確認・手動実行・削除：

```powershell
Get-ScheduledTask     -TaskName "TA_XM_AutoTrade"          # 状態確認（State=Ready）
Get-ScheduledTaskInfo -TaskName "TA_XM_AutoTrade"          # 次回実行時刻 NextRunTime
Start-ScheduledTask   -TaskName "TA_XM_AutoTrade"          # 手動で即時実行
Unregister-ScheduledTask -TaskName "TA_XM_AutoTrade" -Confirm:$false  # 削除
```

### 本番運用の前提・注意

- **PC 起動・ログオン状態**：`Interactive` 実行のため、起動時刻に PC が稼働しログオン中である必要がある。
  常時運用ではスリープを無効化する（`powercfg /change standby-timeout-ac 0`）。
  無人サーバーで走らせる場合は `-LogonType S4U`（パスワード保存不要）や
  「ユーザーがログオンしているかどうかにかかわらず実行」への変更を検討する。
- **MT5 ターミナル**：`.env` の `MT5_PATH` を設定しておけば `initialize()` が自動起動する。
  確実を期すなら常時起動しておく。デモ→本番切替時は口座番号・サーバー名・パスワードを差し替える。
- **ログ**：起動/終了は `logs\scheduler_run.log`、詳細は `logs\` の日次ログに出力。
  結果サマリは Discord Webhook に通知される。
- **多重起動抑止**：`-MultipleInstances IgnoreNew` により前サイクル未終了時の重複起動を防ぐ。
- **本番移行チェック**：`.env` を本番口座に更新し、`config/risk.yaml` のリスク値・
  サーキットブレーカー閾値を運用方針に合わせて再確認する。

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
run_cycle.bat  タスクスケジューラから呼ぶ 1 サイクル実行ランナー
```

## 重要な設計上の注意（Phase 0 確定事実）

- `propagate` は **同期**メソッド。`await propagate` は禁止（`asyncio.to_thread` を使う）。
- 戻り値の signal は **5段階レーティング文字列**（Buy/Overweight/Hold/Underweight/Sell）。
- 最終判断に **confidence は存在しない**（レーティング階層ゲートで代替）。
- 文脈注入は `config["portfolio_context"]` では**効かない**。
  `TradingAgentsGraph.resolve_instrument_context` のラップを使う。
- MT5 シンボルは **会社名**（`Nvidia` / `JPMorgan` 等）。`volume_min` は銘柄ごとに異なる。
