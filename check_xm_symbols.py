"""
XMTrading MT5 で利用可能な US 株シンボルを一覧表示するスクリプト。

XMTrading は会社名形式 (例: AdvMicroDev) を使用し、
description に実際のティッカー "(AMD.OQ)" が含まれる。
このスクリプトは description からティッカーを逆引きして
MT5シンボル名 → yfinanceティッカーのマッピングを自動提案する。

実行:
  python check_xm_symbols.py
  python check_xm_symbols.py --all       全US株を出力
  python check_xm_symbols.py --update    symbol_map.py を自動更新
"""

import re
import sys
import argparse

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 パッケージが必要です: pip install MetaTrader5")
    sys.exit(1)

import config

# 当システムで使いたい候補 (yfinanceティッカー)
WANTED: dict[str, str] = {
    # yfinance ticker : 会社名キーワード (部分一致検索用)
    "NVDA":  "nvidia",
    "AMD":   "advanced micro",
    "MSFT":  "microsoft",
    "AAPL":  "apple",
    "GOOGL": "alphabet",
    "META":  "meta platforms",
    "CRM":   "salesforce",
    "PLTR":  "palantir",
    "ARM":   "arm hold",
    "AVGO":  "broadcom",
    "TSM":   "taiwan semiconductor",
    "COIN":  "coinbase",
    "SHOP":  "shopify",
    "CRWD":  "crowdstrike",
    "NFLX":  "netflix",
    "SMCI":  "super micro",
}


def extract_ticker_from_desc(description: str) -> str | None:
    """description の "(TICKER.EXCHANGE)" 部分からティッカーを抽出する。"""
    m = re.search(r'\(([A-Z0-9]+)\.[A-Z]+\)', description)
    return m.group(1) if m else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all",    action="store_true", help="全US株を表示")
    parser.add_argument("--update", action="store_true", help="symbol_map.py を自動更新")
    args = parser.parse_args()

    if not mt5.initialize(
        path=config.MT5_PATH,
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER,
    ):
        print(f"MT5 初期化失敗: {mt5.last_error()}")
        sys.exit(1)

    all_symbols = mt5.symbols_get()
    us_stocks = [
        s for s in (all_symbols or [])
        if "stock" in s.path.lower() and "us" in s.path.lower()
    ]
    mt5.shutdown()

    print("=" * 65)
    print(f"XMTrading MT5 - US株 ({len(us_stocks)} 銘柄)")
    print("=" * 65)

    # description の exchange ticker → MT5シンボル名 の逆引き辞書
    ticker_to_mt5: dict[str, str] = {}
    for s in us_stocks:
        t = extract_ticker_from_desc(s.description)
        if t:
            ticker_to_mt5[t] = s.name

    # 会社名キーワードでも検索 (descriptionの ticker が一致しない場合のフォールバック)
    def find_by_keyword(keyword: str) -> tuple[str, str] | None:
        kw = keyword.lower()
        for s in us_stocks:
            if kw in s.description.lower():
                t = extract_ticker_from_desc(s.description)
                return (s.name, t or "?")
        return None

    print("\n【ウォッチリスト候補 マッチング結果】\n")
    found:     list[tuple[str, str, str]] = []   # (yf_ticker, mt5_name, desc_ticker)
    not_found: list[str] = []

    for yf_ticker, keyword in WANTED.items():
        # 1) description の exchange ticker で直接検索
        mt5_name = ticker_to_mt5.get(yf_ticker)
        if mt5_name:
            found.append((yf_ticker, mt5_name, yf_ticker))
            continue
        # 2) 会社名キーワードで検索
        result = find_by_keyword(keyword)
        if result:
            mt5_name, desc_ticker = result
            found.append((yf_ticker, mt5_name, desc_ticker))
        else:
            not_found.append(yf_ticker)

    for yf_ticker, mt5_name, desc_ticker in sorted(found):
        match_mark = "✅" if yf_ticker == desc_ticker else "⚠️ "
        print(f"  {match_mark} {yf_ticker:6s} → MT5: {mt5_name:25s} (exchange ticker: {desc_ticker})")

    if not_found:
        print(f"\n  ❌ XMに存在しない可能性:")
        for t in not_found:
            print(f"     {t}")

    # symbol_map.py 更新用コード提案
    print("\n\n【symbol_map.py への追加コード (提案)】\n")
    for yf_ticker, mt5_name, desc_ticker in sorted(found):
        mt5_base = mt5_name.rstrip("#.")
        print(f'    "{mt5_base}": "{yf_ticker}",')

    if args.update:
        _update_symbol_map(found)

    if args.all:
        print(f"\n\n【全US株シンボル一覧】\n")
        for s in sorted(us_stocks, key=lambda x: x.name):
            t = extract_ticker_from_desc(s.description) or ""
            print(f"  {s.name:30s} {t:8s} {s.description}")


def _update_symbol_map(found: list[tuple[str, str, str]]) -> None:
    """symbol_map.py の _SYMBOL_TO_YF に見つかった銘柄を追記する。"""
    import os
    map_path = os.path.join(os.path.dirname(__file__), "symbol_map.py")
    with open(map_path, encoding="utf-8") as f:
        src = f.read()

    insert_lines = []
    for yf_ticker, mt5_name, _ in sorted(found):
        mt5_base = mt5_name.rstrip("#.")
        entry = f'    "{mt5_base}": "{yf_ticker}",'
        if mt5_base not in src:
            insert_lines.append(entry)

    if not insert_lines:
        print("\n[update] 追加すべき新規エントリなし")
        return

    anchor = '    # 米国株 個別銘柄 (XM Trading は suffix なし)'
    if anchor in src:
        new_block = anchor + "\n" + "\n".join(insert_lines)
        new_src = src.replace(anchor, new_block)
        with open(map_path, "w", encoding="utf-8") as f:
            f.write(new_src)
        print(f"\n[update] symbol_map.py に {len(insert_lines)} 件追加しました:")
        for line in insert_lines:
            print(f"  {line}")
    else:
        print("\n[update] アンカーが見つかりません。手動で追加してください。")


if __name__ == "__main__":
    main()
