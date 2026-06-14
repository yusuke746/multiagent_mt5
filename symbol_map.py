"""MT5 シンボル → yfinance ティッカー マッピング

TradingAgents は yfinance でデータを取得するため、
MT5のシンボル名から yfinance のティッカーへの変換が必要。

将来的に銘柄を追加する場合はここに追加するだけでよい。
"""

# MT5シンボル名 (suffix無し) → yfinance ティッカー
_SYMBOL_TO_YF: dict[str, str] = {
    # 貴金属 (先物GC=FはTradingAgents内で"GC=GC=F"と二重化するバグあり → ETFを使用)
    "GOLD":      "GLD",       # SPDR Gold Shares ETF (金価格連動)
    "XAUUSD":    "GLD",
    "SILVER":    "SLV",       # iShares Silver Trust ETF

    # FX
    "USDJPY":    "USDJPY=X",
    "EURUSD":    "EURUSD=X",
    "GBPUSD":    "GBPUSD=X",
    "AUDUSD":    "AUDUSD=X",
    "USDCHF":    "USDCHF=X",
    "USDCAD":    "USDCAD=X",
    "NZDUSD":    "NZDUSD=X",
    "EURJPY":    "EURJPY=X",
    "GBPJPY":    "GBPJPY=X",

    # 株式インデックス (先物ティッカーは=バグ対象 → ETFを使用)
    "US100Cash": "QQQ",       # Invesco QQQ (NASDAQ 100 ETF)
    "US30Cash":  "DIA",       # SPDR Dow Jones ETF
    "US500Cash": "SPY",       # SPDR S&P 500 ETF
    "UK100Cash": "^FTSE",
    "GER40Cash": "^GDAXI",
    "JPN225Cash": "^N225",

    # エネルギー
    "OILCash":   "USO",       # United States Oil Fund ETF
    "BRENTCash": "BNO",       # United States Brent Oil Fund ETF
    "NGASCash":  "UNG",       # United States Natural Gas Fund ETF

    # 暗号資産
    "BTCUSD":    "BTC-USD",
    "ETHUSD":    "ETH-USD",

    # 米国株 個別銘柄 (XM Trading の MT5シンボル名 = 会社名形式)
    "Apple":               "AAPL",
    "AdvMicroDev":         "AMD",
    "Arm Holdings":        "ARM",
    "Broadcom":            "AVGO",
    "Coinbase":            "COIN",
    "Salesforce":          "CRM",
    "Crowdstrike":         "CRWD",
    "Google":              "GOOGL",
    "Facebook":            "META",
    "Microsoft":           "MSFT",
    "Netflix":             "NFLX",
    "Nvidia":              "NVDA",
    "Palantir":            "PLTR",
    "Super Micro Computer":"SMCI",
    "Taiwan-Semiconductor":"TSM",
    # Shopify は XMでの存在未確認のためコメントアウト
    # "Shopify":           "SHOP",
}

# yfinance ティッカー の asset_type
# "stock" = 株/ETF/インデックス, "crypto" = 暗号資産
_YF_ASSET_TYPE: dict[str, str] = {
    "GLD":      "stock",
    "SLV":      "stock",
    "USDJPY=X": "stock",
    "EURUSD=X": "stock",
    "GBPUSD=X": "stock",
    "AUDUSD=X": "stock",
    "USDCHF=X": "stock",
    "USDCAD=X": "stock",
    "NZDUSD=X": "stock",
    "EURJPY=X": "stock",
    "GBPJPY=X": "stock",
    "QQQ":      "stock",
    "DIA":      "stock",
    "SPY":      "stock",
    "^FTSE":    "stock",
    "^GDAXI":   "stock",
    "^N225":    "stock",
    "USO":      "stock",
    "BNO":      "stock",
    "UNG":      "stock",
    "BTC-USD":  "crypto",
    "ETH-USD":  "crypto",

    # 米国株 個別銘柄
    "NVDA":   "stock",
    "AMD":    "stock",
    "MSFT":   "stock",
    "AAPL":   "stock",
    "GOOGL":  "stock",
    "META":   "stock",
    "CRM":    "stock",
    "PLTR":   "stock",
    "ARM":    "stock",
    "AVGO":   "stock",
    "TSM":    "stock",
    "COIN":   "stock",
    "SHOP":   "stock",
    "CRWD":   "stock",
    "NFLX":   "stock",
    "SMCI":   "stock",
}

# yfinance ティッカー → MT5シンボル名 (逆引き)
# weekly_ta_screener から multiagent_mt5 の SYMBOLS を更新する際に使用
YF_TO_MT5: dict[str, str] = {v: k for k, v in _SYMBOL_TO_YF.items()}


def get_yf_ticker(mt5_symbol: str) -> str | None:
    """MT5シンボル名 (suffix付きでも可) を yfinance ティッカーに変換する。

    Args:
        mt5_symbol: "GOLD", "GOLD#", "USDJPY", etc.

    Returns:
        yfinance ティッカー文字列 (未対応時は None)
    """
    # suffix (#, .) を除去して正規化
    base = mt5_symbol.rstrip("#.")
    return _SYMBOL_TO_YF.get(base) or _SYMBOL_TO_YF.get(mt5_symbol)


def get_asset_type(yf_ticker: str) -> str:
    """yfinance ティッカーの asset_type を返す。デフォルトは "stock"。"""
    return _YF_ASSET_TYPE.get(yf_ticker, "stock")


def get_yf_ticker_or_raise(mt5_symbol: str) -> str:
    """get_yf_ticker の例外送出版 (未対応シンボルは ValueError)。"""
    ticker = get_yf_ticker(mt5_symbol)
    if ticker is None:
        raise ValueError(
            f"MT5シンボル '{mt5_symbol}' の yfinance ティッカーが未定義です。"
            "symbol_map.py の _SYMBOL_TO_YF に追加してください。"
        )
    return ticker
