"""システム設定

.env ファイルから全設定を読み込む。
デフォルト値はデモ口座での安全な運用を想定した保守的な値に設定してある。
"""
import os
from dotenv import load_dotenv

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_CURRENT_DIR, ".env"))

# ──────────────────────────────────────
# MT5 接続設定
# ──────────────────────────────────────
MT5_PATH     = os.getenv("MT5_PATH", r"C:\Program Files\XMTrading MT5\terminal64.exe")
MT5_LOGIN    = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER   = os.getenv("MT5_SERVER", "XMTrading-MT5 3")

# ──────────────────────────────────────
# OpenAI API
# ──────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ──────────────────────────────────────
# Discord Webhook
# ──────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ──────────────────────────────────────
# 監視銘柄リスト (MT5シンボル名)
# .env の SYMBOLS にカンマ区切りで指定: SYMBOLS=GOLD
# ──────────────────────────────────────
SYMBOLS = [
    s.strip()
    for s in os.getenv("SYMBOLS", "GOLD").split(",")
    if s.strip()
]

# ──────────────────────────────────────
# 通貨グループ定義 (相関リスク制御用)
# ──────────────────────────────────────
CURRENCY_GROUPS: dict[str, list[str]] = {
    "GOLD":      ["USD", "XAU"],
    "USDJPY":    ["USD", "JPY"],
    "EURUSD":    ["USD", "EUR"],
    "US100Cash": ["USD"],
    "OILCash":   ["USD"],
    "BTCUSD":    ["BTC"],
    "ETHUSD":    ["ETH"],
    # 米国株個別銘柄 (XM MT5シンボル名 = 会社名形式)
    "Apple":               ["US_STOCK"],
    "AdvMicroDev":         ["US_STOCK"],
    "Arm Holdings":        ["US_STOCK"],
    "Broadcom":            ["US_STOCK"],
    "Coinbase":            ["US_STOCK"],
    "Salesforce":          ["US_STOCK"],
    "Crowdstrike":         ["US_STOCK"],
    "Google":              ["US_STOCK"],
    "Facebook":            ["US_STOCK"],
    "Microsoft":           ["US_STOCK"],
    "Netflix":             ["US_STOCK"],
    "Nvidia":              ["US_STOCK"],
    "Palantir":            ["US_STOCK"],
    "Super Micro Computer":["US_STOCK"],
    "Taiwan-Semiconductor":["US_STOCK"],
}

# ──────────────────────────────────────
# 資金管理パラメータ
# ──────────────────────────────────────
RISK_PER_TRADE          = float(os.getenv("RISK_PER_TRADE", "0.01"))  # 残高の 1% リスク (デモ保守的)
MAX_LOT                 = float(os.getenv("MAX_LOT", "0.5"))
MAX_CORRELATED_POSITIONS = int(os.getenv("MAX_CORRELATED_POSITIONS", "1"))

# ──────────────────────────────────────
# エントリー SL / TP 設定 (ATRベース)
# ──────────────────────────────────────
# SL = entry ± ATR(M15, 14) × SL_ATR_MULT
ENTRY_SL_ATR_MULT = float(os.getenv("ENTRY_SL_ATR_MULT", "1.5"))

# 銘柄別SL ATR倍率オーバーライド
ENTRY_SL_ATR_MULT_BY_SYMBOL: dict[str, float] = {
    "GOLD":      float(os.getenv("ENTRY_SL_ATR_MULT_GOLD",      "2.0")),
    "US100Cash": float(os.getenv("ENTRY_SL_ATR_MULT_US100CASH", "2.0")),
    "OILCash":   float(os.getenv("ENTRY_SL_ATR_MULT_OILCASH",   "1.8")),
    "USDJPY":    float(os.getenv("ENTRY_SL_ATR_MULT_USDJPY",    "1.5")),
    "EURUSD":    float(os.getenv("ENTRY_SL_ATR_MULT_EURUSD",    "1.5")),
    "BTCUSD":    float(os.getenv("ENTRY_SL_ATR_MULT_BTCUSD",    "2.5")),
    # 米国株個別銘柄 (XM MT5シンボル名 = 会社名形式)
    # 個別株はボラが大きいため 2.0 (デフォルト1.5より広め)
    "Nvidia":              float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "AdvMicroDev":         float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Microsoft":           float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Apple":               float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Google":              float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Facebook":            float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Salesforce":          float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Palantir":            float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Arm Holdings":        float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Broadcom":            float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Taiwan-Semiconductor":float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Coinbase":            float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Crowdstrike":         float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Netflix":             float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
    "Super Micro Computer":float(os.getenv("ENTRY_SL_ATR_MULT_US_STOCK", "2.0")),
}

# TP = SL距離 × R倍率
ENTRY_TP_R = float(os.getenv("ENTRY_TP_R", "2.0"))

# ──────────────────────────────────────
# エントリーフィルター設定
# ──────────────────────────────────────
# 乖離フィルター:
# シグナルキャッシュ時点の価格から現在価格が ATR × N 以上離れていたらスキップ
# 強いトレンド中に乗り遅れた高値掴みを防ぐ (0.0 = 無効)
ENTRY_DRIFT_FILTER_ATR_MULT = float(os.getenv("ENTRY_DRIFT_FILTER_ATR_MULT", "1.5"))

# 直近足方向フィルター:
# 直近確定M15足がシグナルと同方向(BUY=陽線/SELL=陰線)のときのみ発注
# 逆流への突っ込みエントリーを防ぐ (false = 無効)
ENTRY_CANDLE_DIR_FILTER = os.getenv("ENTRY_CANDLE_DIR_FILTER", "true").lower() == "true"

# ──────────────────────────────────────
# TradingAgents (TA) 設定
# ──────────────────────────────────────
# アナリスト選択 (各々 "true"/"false" で .env から制御)
TA_USE_MARKET       = os.getenv("TA_USE_MARKET",       "true").lower()  == "true"
TA_USE_NEWS         = os.getenv("TA_USE_NEWS",         "true").lower()  == "true"
TA_USE_SOCIAL       = os.getenv("TA_USE_SOCIAL",       "true").lower()  == "true"
TA_USE_FUNDAMENTALS = os.getenv("TA_USE_FUNDAMENTALS", "false").lower() == "true"

# LLM モデル (OpenAIのみ対応)
TA_DEEP_MODEL  = os.getenv("TA_DEEP_MODEL",  "gpt-5.4")
TA_QUICK_MODEL = os.getenv("TA_QUICK_MODEL", "gpt-5-mini")

# ディベートラウンド数 (多いほど精度↑・コスト↑・時間↑)
TA_MAX_DEBATE_ROUNDS = int(os.getenv("TA_MAX_DEBATE_ROUNDS", "1"))
TA_MAX_RISK_ROUNDS   = int(os.getenv("TA_MAX_RISK_ROUNDS",   "1"))

# キャッシュ有効期限 (時間)
# H1サイクルより少し長めに設定してキャッシュ切れでエントリー機会を失わないようにする
TA_SIGNAL_MAX_AGE_HOURS = float(os.getenv("TA_SIGNAL_MAX_AGE_HOURS", "2.5"))

# TA分析のリトライ設定
TA_MAX_RETRY = int(os.getenv("TA_MAX_RETRY", "2"))

# ──────────────────────────────────────
# シグナルフリップ時のエグジット制御
# ──────────────────────────────────────
# TradingAgentsの方向が反転した場合にポジションをクローズするか
TA_EXIT_ON_SIGNAL_FLIP    = os.getenv("TA_EXIT_ON_SIGNAL_FLIP",    "true").lower()  == "true"
# TradingAgentsの方向がNEUTRALになった場合にもクローズするか
TA_EXIT_ON_SIGNAL_NEUTRAL = os.getenv("TA_EXIT_ON_SIGNAL_NEUTRAL", "false").lower() == "true"

# ──────────────────────────────────────
# 機械式緊急エグジット
# ──────────────────────────────────────
EMERGENCY_EXIT_ENABLED               = os.getenv("EMERGENCY_EXIT_ENABLED", "true").lower() == "true"
EMERGENCY_EXIT_ADVERSE_ATR           = float(os.getenv("EMERGENCY_EXIT_ADVERSE_ATR", "2.0"))
EMERGENCY_EXIT_ATR_SPIKE_MULTIPLIER  = float(os.getenv("EMERGENCY_EXIT_ATR_SPIKE_MULTIPLIER", "2.0"))
EMERGENCY_EXIT_ATR_SPIKE_MIN_ADVERSE = float(os.getenv("EMERGENCY_EXIT_ATR_SPIKE_MIN_ADVERSE", "0.8"))
EMERGENCY_EXIT_ATR_SPIKE_LOOKBACK    = int(os.getenv("EMERGENCY_EXIT_ATR_SPIKE_LOOKBACK", "20"))

# 銘柄別緊急エグジットATR倍率
EMERGENCY_EXIT_ADVERSE_ATR_BY_SYMBOL: dict[str, float] = {
    "GOLD":      2.5,
    "US100Cash": 2.2,
    "OILCash":   2.5,
    "USDJPY":    2.0,
    "EURUSD":    2.0,
    "BTCUSD":    3.0,
    # 米国株個別銘柄 (ギャップ・急騰急落リスクを考慮して広め)
    "Nvidia":              2.5,
    "AdvMicroDev":         2.5,
    "Microsoft":           2.5,
    "Apple":               2.5,
    "Google":              2.5,
    "Facebook":            2.5,
    "Salesforce":          2.5,
    "Palantir":            3.0,
    "Arm Holdings":        3.0,
    "Broadcom":            2.5,
    "Taiwan-Semiconductor":3.0,
    "Coinbase":            3.0,
    "Crowdstrike":         2.5,
    "Netflix":             2.5,
    "Super Micro Computer":3.0,
}

# ──────────────────────────────────────
# 利益保護設定
# ──────────────────────────────────────
PROFIT_PROTECTION_ENABLED   = os.getenv("PROFIT_PROTECTION_ENABLED", "true").lower() == "true"
BREAKEVEN_R                 = float(os.getenv("BREAKEVEN_R",          "1.0"))
BREAKEVEN_BUFFER_R          = float(os.getenv("BREAKEVEN_BUFFER_R",   "0.10"))
LOCK_PROFIT_1_TRIGGER_R     = float(os.getenv("LOCK_PROFIT_1_TRIGGER_R", "1.3"))
LOCK_PROFIT_1_R             = float(os.getenv("LOCK_PROFIT_1_R",        "0.30"))
LOCK_PROFIT_2_TRIGGER_R     = float(os.getenv("LOCK_PROFIT_2_TRIGGER_R", "1.6"))
LOCK_PROFIT_2_R             = float(os.getenv("LOCK_PROFIT_2_R",         "0.70"))
LOCK_PROFIT_3_TRIGGER_R     = float(os.getenv("LOCK_PROFIT_3_TRIGGER_R", "1.85"))
LOCK_PROFIT_3_R             = float(os.getenv("LOCK_PROFIT_3_R",         "1.30"))

# ──────────────────────────────────────
# 市場クローズ前強制手仕舞い
# ──────────────────────────────────────
FLAT_BEFORE_MARKET_CLOSE_ENABLED      = os.getenv("FLAT_BEFORE_MARKET_CLOSE_ENABLED",      "true").lower()  == "true"
FLAT_BEFORE_MARKET_CLOSE_HOUR         = int(os.getenv("FLAT_BEFORE_MARKET_CLOSE_HOUR",         "0"))
FLAT_BEFORE_MARKET_CLOSE_MINUTE       = int(os.getenv("FLAT_BEFORE_MARKET_CLOSE_MINUTE",       "0"))
FLAT_BEFORE_MARKET_CLOSE_LEAD_MINUTES = max(0, int(os.getenv("FLAT_BEFORE_MARKET_CLOSE_LEAD_MINUTES", "15")))
FLAT_BEFORE_WEEKEND_CLOSE_ENABLED     = os.getenv("FLAT_BEFORE_WEEKEND_CLOSE_ENABLED",     "true").lower()  == "true"
FLAT_BEFORE_WEEKEND_CLOSE_LEAD_MINUTES = max(0, int(os.getenv("FLAT_BEFORE_WEEKEND_CLOSE_LEAD_MINUTES", "30")))

# ──────────────────────────────────────
# ATR / MA 設定
# ──────────────────────────────────────
ATR_PERIOD = 14
MA_PERIOD  = 20

# ──────────────────────────────────────
# タイムフレーム
# ──────────────────────────────────────
EXECUTION_TF = "M15"
TREND_TF     = "H1"

# ──────────────────────────────────────
# 市場ストレス検知 (スプレッド/ATR急変)
# ニュース監視は TradingAgents に委譲
MARKET_STRESS_MODEL = os.getenv("MARKET_STRESS_MODEL", "gpt-5-mini")

# ──────────────────────────────────────
# 市場ストレス検知
# ──────────────────────────────────────
MARKET_STRESS_SPREAD_RATIO         = float(os.getenv("MARKET_STRESS_SPREAD_RATIO",         "3.0"))
MARKET_STRESS_SPREAD_CLOSE_RATIO   = float(os.getenv("MARKET_STRESS_SPREAD_CLOSE_RATIO",   "5.0"))
MARKET_STRESS_ATR_RATIO            = float(os.getenv("MARKET_STRESS_ATR_RATIO",            "2.5"))
MARKET_STRESS_AI_ENABLED           = os.getenv("MARKET_STRESS_AI_ENABLED",           "true").lower() == "true"
MARKET_STRESS_HOLD_MIN_MIN         = int(os.getenv("MARKET_STRESS_HOLD_MIN_MIN",         "10"))
MARKET_STRESS_HOLD_MAX_MIN         = int(os.getenv("MARKET_STRESS_HOLD_MAX_MIN",         "480"))
MARKET_STRESS_FORCE_CLEAR_GRACE_MIN = int(os.getenv("MARKET_STRESS_FORCE_CLEAR_GRACE_MIN", "30"))
MARKET_STRESS_SPREAD_CLEAR_RATIO   = float(os.getenv("MARKET_STRESS_SPREAD_CLEAR_RATIO",  "1.5"))
MARKET_STRESS_ATR_CLEAR_RATIO      = float(os.getenv("MARKET_STRESS_ATR_CLEAR_RATIO",     "1.3"))
MARKET_STRESS_SPREAD_BASELINE_N    = int(os.getenv("MARKET_STRESS_SPREAD_BASELINE_N",     "50"))
MARKET_STRESS_MAX_CHAIN_HOURS      = float(os.getenv("MARKET_STRESS_MAX_CHAIN_HOURS",     "3.0"))
POST_RECOVERY_TRADE_COUNT          = int(os.getenv("POST_RECOVERY_TRADE_COUNT",          "1"))
POST_RECOVERY_LOT_MULTIPLIER       = float(os.getenv("POST_RECOVERY_LOT_MULTIPLIER",     "0.5"))

# ──────────────────────────────────────
# SQLite 設定
# ──────────────────────────────────────
DB_PATH                      = os.path.join(_CURRENT_DIR, os.getenv("DB_PATH", "trades.db"))
DB_MAINTENANCE_INTERVAL_SEC  = int(os.getenv("DB_MAINTENANCE_INTERVAL_SEC",  "3600"))
DB_FULL_VACUUM_INTERVAL_SEC  = int(os.getenv("DB_FULL_VACUUM_INTERVAL_SEC",  "86400"))
DB_RETENTION_DAYS_AI_LOGS    = int(os.getenv("DB_RETENTION_DAYS_AI_LOGS",    "14"))
DB_RETENTION_DAYS_HEARTBEATS = int(os.getenv("DB_RETENTION_DAYS_HEARTBEATS", "30"))
DB_RETENTION_DAYS_CLOSED_TRADES = int(os.getenv("DB_RETENTION_DAYS_CLOSED_TRADES", "365"))
DB_MAX_AI_LOG_ROWS           = int(os.getenv("DB_MAX_AI_LOG_ROWS",           "5000"))
DB_MAX_HEARTBEAT_ROWS        = int(os.getenv("DB_MAX_HEARTBEAT_ROWS",        "2000"))

# ──────────────────────────────────────
# 週次スクリーニング設定
# ──────────────────────────────────────
# 毎週月曜日に WEEKLY_UNIVERSE をスクリーニングし、今週の監視銘柄を自動選定する。
# 選定結果は config.SYMBOLS をメモリ上で更新し、H1スレッドが自動ピックアップする。
WEEKLY_SCREENING_ENABLED = os.getenv("WEEKLY_SCREENING_ENABLED", "true").lower() == "true"

# 監視ユニバース (yfinanceティッカーで指定)
# MT5シンボルではなく yfinance ティッカーで記述すること
WEEKLY_UNIVERSE: list[str] = [
    s.strip()
    for s in os.getenv(
        "WEEKLY_UNIVERSE",
        # yfinance ティッカーで指定 (SHOP は XM未確認のため除外)
        "NVDA,AMD,MSFT,AAPL,GOOGL,META,CRM,PLTR,ARM,AVGO,TSM,COIN,CRWD,NFLX,SMCI",
    ).split(",")
    if s.strip()
]

# TradingAgents に渡す候補数 (1銘柄あたり約 $0.05〜0.15 / 週)
WEEKLY_SCREENER_TOP_N = int(os.getenv("WEEKLY_SCREENER_TOP_N", "3"))

# スクリーニング実行曜日 (0=月曜, 1=火曜, ..., 6=日曜)
WEEKLY_SCREENING_DOW = int(os.getenv("WEEKLY_SCREENING_DOW", "0"))

# ──────────────────────────────────────
# ログ / ループ設定
# ──────────────────────────────────────
LOG_DIR                = os.path.join(_CURRENT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

HEARTBEAT_INTERVAL_SEC       = 3600
HEARTBEAT_NOTIFY_DRAWDOWN_PCT = float(os.getenv("HEARTBEAT_NOTIFY_DRAWDOWN_PCT", "5.0"))
MAIN_LOOP_SLEEP_SEC          = 10
CANDLE_WAIT_SEC              = 15
MARKET_DATA_STALE_SEC        = int(os.getenv("MARKET_DATA_STALE_SEC", "1800"))

# TradingAgents H1分析サイクル間隔 (秒)
TA_ANALYSIS_INTERVAL_SEC = int(os.getenv("TA_ANALYSIS_INTERVAL_SEC", "3600"))

# 同銘柄の再エントリークールダウン (M15バー数)
SYMBOL_REENTRY_COOLDOWN_BARS = max(0, int(os.getenv("SYMBOL_REENTRY_COOLDOWN_BARS", "1")))

# 積み増し時のトレンドフィルター (ADX + EMAクロス)
REENTRY_TREND_FILTER  = os.getenv("REENTRY_TREND_FILTER",  "true").lower() == "true"
REENTRY_ADX_PERIOD    = int(os.getenv("REENTRY_ADX_PERIOD",    "14"))
REENTRY_ADX_THRESHOLD = float(os.getenv("REENTRY_ADX_THRESHOLD", "25"))
REENTRY_EMA_FAST      = int(os.getenv("REENTRY_EMA_FAST",  "5"))
REENTRY_EMA_SLOW      = int(os.getenv("REENTRY_EMA_SLOW", "20"))

# Overweight 買い増し時のロット倍率 (通常エントリーに対する割合)
OVERWEIGHT_LOT_MULT = float(os.getenv("OVERWEIGHT_LOT_MULT", "0.5"))
