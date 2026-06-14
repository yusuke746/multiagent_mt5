# Multi-Agent MT5 Trading System

MT5 (XMTrading KIWAMI口座) + TradingAgents による多エージェントLLM自動売買システム。  
**ロング専用モード**: Buy/Overweight でロングのみ建て、Sell で全クローズ。ショートポジションは建てない。

## アーキテクチャ

```
┌─────────────────────────────────────────────────────┐
│  H1バックグラウンドスレッド (1時間ごと)                        │
│  TradingAgents.propagate(symbol, today)                    │
│  → Buy / Overweight / Hold / Underweight / Sell をキャッシュ  │
│  ※ 市場クローズ中(土日)は分析スキップ+キャッシュ全クリア          │
└─────────────────┬───────────────────────────────────┘
                  │ キャッシュ参照
┌─────────────────▼───────────────────────────────────┐
│  M15メインサイクル (15分ごと)                                  │
│                                                              │
│  [Exit]                                                      │
│    ├─ 緊急ATR離脱                                            │
│    ├─ 市場クローズ前強制手仕舞い (週末30分前フラット)             │
│    ├─ シグナルフリップ決済 (Sell出現時にロング全クローズ)          │
│    └─ 利益保護 BE → LP1 → LP2 → LP3 (SL自動引き上げ)           │
│                                                              │
│  [Entry / ポジション管理]  ← ロング専用                        │
│    ├─ Buy (ポジなし)   : 新規ロング                            │
│    ├─ Buy (ポジあり)   : 買い増し (H1シグナルごと1回のみ)         │
│    ├─ Overweight       : 既存ロングへ買い増し (同上)            │
│    ├─ Hold             : 維持のみ / 新規なし                   │
│    ├─ Underweight      : 1本縮小 (H1シグナルごと1回・最低1ポジ保持) │
│    └─ Sell             : ロング全クローズ (新規ショートなし)      │
│                                                              │
│  [エントリーフィルター (Buy/Overweight 共通)]                  │
│    ├─ 乖離フィルター (ATR×N: シグナル時点からの価格乖離)           │
│    ├─ 直前確定M15足方向フィルター                              │
│    └─ 買い増し時: ADX≥閾値 + EMA5/EMA20クロス                  │
└─────────────────────────────────────────────────────┘
```

## 5段階レーティングの動作

| TradingAgentsレーティング | 動作 | ロット | スロットル |
|---|---|---|---|
| **Buy** (ポジなし) | 新規ロング | 通常 | クールダウン1本 |
| **Buy** (ポジあり) | ロング買い増し | ×`OVERWEIGHT_LOT_MULT` | H1シグナルごと1回 |
| **Overweight** | 既存ロングへ買い増し | ×`OVERWEIGHT_LOT_MULT` | H1シグナルごと1回 |
| **Hold** | 維持のみ | - | - |
| **Underweight** | 最新チケットを1本クローズ | - | H1シグナルごと1回・最低1ポジ保持 |
| **Sell** | ロング全クローズ | - | - |

> **過剰クローズ防止**: `_last_overweight_add_at` と `_last_underweight_reduction_at` に H1 シグナルの `timestamp` を記録し、同一シグナルサイクル内では M15 が何度回っても2回目以降はスキップ。

## TradingAgents アナリスト構成

| アナリスト   | 分析内容                       | .env キー                |
| ------------ | ------------------------------ | ------------------------ |
| market       | テクニカル指標 (MACD/RSI等)    | TA_USE_MARKET=true       |
| news         | グローバルニュース・マクロ経済 | TA_USE_NEWS=true         |
| social       | StockTwits/Reddit センチメント | TA_USE_SOCIAL=false ※   |
| fundamentals | ファンダメンタルズ             | TA_USE_FUNDAMENTALS=true |

※ KIWAMI口座では先物備ティッカーにReddit/StockTwitsが403エラーのため無効化済み。

## 対応銘柄

| MT5シンボル | yfinance ティッカー | 備考       |
| ----------- | ------------------- | ---------- |
| GOLD        | GLD (金ETF)         | デモ運用中 |
| USDJPY      | USDJPY=X            | 将来追加可 |
| EURUSD      | EURUSD=X            | 将来追加可 |
| US100Cash   | QQQ                 | 将来追加可 |
| OILCash     | USO                 | 将来追加可 |

**KIWAMI口座の `#`suffixについて**: MT5内部では `GOLD#`などシンボル名に `#`が付く。全MT5 API呼び出しは自動補正済み。

追加方法: `symbol_map.py` の `_SYMBOL_TO_YF` に追記 + `.env` の `SYMBOLS=` に追加。

## セットアップ

### 1. Python 仓想環境作成

```powershell
cd C:\Users\user\openHands-test\multiagent_mt5
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 2. 依存パッケージインストール

```powershell
pip install -r requirements.txt
```

TradingAgents は自動で `git+https://...` からインストールされます。

### 3. .env 設定

```powershell
Copy-Item .env.example .env
# .env をエディタで開いて必要事項を記入
```

最低限必須の設定:

```ini
OPENAI_API_KEY=sk-...
MT5_LOGIN=あなたのログインID
MT5_PASSWORD=あなたのパスワード
MT5_SERVER=XMTrading-MT5 3
```

### 4. MT5 起動 & ログイン

XMTrading MT5 を起動し、デモ口座にログインしておく。

### 5. 実行

```powershell
python main.py
```

## 主要設定 (.env)

### 基本設定

| 設定キー               | 説明                    | デフォルト |
| ---------------------- | ----------------------- | ---------- |
| SYMBOLS                | 監視銘柄 (カンマ区切り) | GOLD       |
| RISK_PER_TRADE         | 1トレードあたりリスク率 | 0.02 (2%)  |
| MAX_LOT                | 最大ロット              | 10.0       |
| ENTRY_SL_ATR_MULT      | SLバッファ (ATR×倍率)  | 1.5        |
| ENTRY_SL_ATR_MULT_GOLD | GOLDのSL倍率            | 2.0        |
| ENTRY_TP_R             | TP = SL距離 × この倍率 | 2.0 (2R)   |

### TradingAgents

| 設定キー                  | 説明                             | デフォルト |
| ------------------------- | -------------------------------- | ---------- |
| TA_DEEP_MODEL             | 深い推論に使うモデル             | gpt-5.4    |
| TA_QUICK_MODEL            | 高速タスクに使うモデル           | gpt-5-mini |
| TA_ANALYSIS_INTERVAL_SEC  | 分析間隔秒数                     | 3600 (1h)  |
| TA_EXIT_ON_SIGNAL_FLIP    | シグナル反転時に全ポジション決済 | true       |
| TA_EXIT_ON_SIGNAL_NEUTRAL | NEUTRAL時に決済                  | false      |

### エントリーフィルター

| 設定キー                     | 説明                                    | デフォルト |
| ---------------------------- | --------------------------------------- | ---------- |
| ENTRY_DRIFT_FILTER_ATR_MULT  | シグナル時点からの最大許容乖離(ATR×N)  | 1.5        |
| ENTRY_CANDLE_DIR_FILTER      | 直前確定M15足方向フィルター有効         | true       |
| SYMBOL_REENTRY_COOLDOWN_BARS | 同銘柄再エントリークールダウン(M15本数) | 1          |

### 積み増しトレンドフィルター (ADX + EMAクロス)

| 設定キー              | 説明                              | デフォルト |
| --------------------- | --------------------------------- | ---------- |
| REENTRY_TREND_FILTER  | 積み増し時のADX+EMAフィルター有効 | true       |
| REENTRY_ADX_PERIOD    | ADX期間                           | 14         |
| REENTRY_ADX_THRESHOLD | ADX閾値 (これ以上でトレンドあり)  | 25         |
| REENTRY_EMA_FAST      | EMA短期期間                       | 5          |
| REENTRY_EMA_SLOW      | EMA長期期間                       | 20         |
| OVERWEIGHT_LOT_MULT   | 買い増し時のロット倍率 (通常の何倍) | 0.5      |

### 利益保護 (3ステージ式ストップロス)

| 設定キー                  | 説明                             | デフォルト |
| ------------------------- | -------------------------------- | ---------- |
| PROFIT_PROTECTION_ENABLED | 利益保護機能有効                 | true       |
| LOCK_PROFIT_1_TRIGGER_R   | LP1発動閾値 (R倍率)              | 1.3        |
| LOCK_PROFIT_1_R           | LP1 SL引上げ先 (エントリー+N×R) | 0.30       |
| LOCK_PROFIT_2_TRIGGER_R   | LP2発動閾値                      | 1.6        |
| LOCK_PROFIT_2_R           | LP2 SL引上げ先                   | 0.70       |
| LOCK_PROFIT_3_TRIGGER_R   | LP3発動閾値                      | 1.85       |
| LOCK_PROFIT_3_R           | LP3 SL引上げ先                   | 1.30       |

## 注意事項

- 本システムは**デモ口座での検証**を目的としています。実運用前に十分なテストを行ってください。
- TradingAgents の分析は非決定的です。
- Gold (GLD) は米国ETFのデータを参照するため、MT5のスポット価格と若干乖離があります。
- **APIコスト**: TradingAgentsは1分析につきLLMを複数回呼び出します (1時間ごと)。市場クローズ中 (土日) は自動スキップします。
- 市場クローズ時にシグナルキャッシュ・買い増し記録・縮小記録をすべてクリアするため、週明けに旧シグナルでエントリーするリスクはありません。
- **ロング専用**: Sell シグナルで新規ショートは建てません。ロング全クローズのみです。

## ファイル構成

```
multiagent_mt5/
├── main.py           # メインループ (H1+M15サイクル)
├── config.py         # 設定 (.envから読み込み)
├── ta_analyzer.py    # TradingAgents統合
├── symbol_map.py     # MT5→yfinanceシンボルマッピング
├── mt5_connector.py  # MT5接続・発注・指標計算(ATR/EMA/ADX)
├── risk_manager.py   # 相関リスク制御
├── lot_calculator.py # ロット計算 (ATRベース・通貨換算対応)
├── market_stress.py  # 市場ストレス検知 (AI対応)
├── discord_notifier.py # Discord通知
├── trade_logger.py   # SQLite記録
├── .env.example      # 設定テンプレート
└── requirements.txt
```
