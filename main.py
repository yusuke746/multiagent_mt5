"""Multi-Agent MT5 Trading System メインループ

サイクル構成:
  ■ H1サイクル (バックグラウンドスレッド)
      TradingAgents を実行して銘柄ごとの方向シグナルをキャッシュ更新。

  ■ M15サイクル (メインスレッド)
      1. 保有ポジションのエグジットチェック (機械的)
      2. 各銘柄の新規エントリーチェック
         - キャッシュシグナルが BUY/SELL → ATRベースSL/TP で発注
"""

import sys
import time
import logging
import threading
from datetime import UTC, datetime, timedelta, timezone

_JST = timezone(timedelta(hours=9))

import config
import mt5_connector
import lot_calculator
import risk_manager
import discord_notifier
import trade_logger
import market_stress
import symbol_map
import weekly_screener
from ta_analyzer import TAAnalyzer, TASignal

# ── ログ設定 ────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            f"{config.LOG_DIR}/trading_{datetime.now():%Y%m%d}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

# ── グローバル状態 ─────────────────────────────────────

# シングルトン TAAnalyzer
_ta: TAAnalyzer | None = None

# 銘柄ごとの最終エントリーM15バー番号 (クールダウン管理)
_last_entry_bar: dict[str, int] = {}

# H1分析スレッドの制御フラグ
_stop_event = threading.Event()
_h1_thread: threading.Thread | None = None

# H1分析中フラグ (銘柄ごと)
_analysis_in_progress: dict[str, bool] = {}
_analysis_lock = threading.Lock()

# シグナルキャッシュ時点のMT5参照価格 (乖離フィルター用)
_signal_ref_price: dict[str, float] = {}

# Underweight 縮小を同一H1シグナルで多重実行しないための記録
_last_underweight_reduction_at: dict[str, datetime] = {}

# Overweight 買い増しを同一H1シグナルで多重実行しないための記録
_last_overweight_add_at: dict[str, datetime] = {}

# 週次スクリーニング実行済のISO週番号 (None = 未実行)
_last_screening_isoweek: int | None = None


# ── H1 バックグラウンドスレッド ──────────

def _h1_analysis_loop():
    """H1 ごとに TradingAgents 分析を実行するバックグラウンドスレッド。"""
    last_run: dict[str, datetime] = {}

    cache_cleared_on_close = False  # クローズ時に1回だけクリアするフラグ

    while not _stop_event.is_set():
        now = datetime.now()

        # 土日など市場クローズ中はAI分析をスキップ (APIコスト削減)
        if not mt5_connector.is_fx_market_open():
            if not cache_cleared_on_close:
                # 金曜終値時点のシグナルで週明けにエントリーしないようキャッシュを全消去
                if _ta is not None:
                    _ta.clear_cache()
                _signal_ref_price.clear()
                _last_entry_bar.clear()
                _last_underweight_reduction_at.clear()
                _last_overweight_add_at.clear()
                logger.info("[H1] 市場クローズ: TAシグナルキャッシュ・基準価格・クールダウンをクリア")
                cache_cleared_on_close = True
            _stop_event.wait(timeout=300)  # 5分待機してから再チェック
            continue
        # 市場再開時にフラグをリセット
        cache_cleared_on_close = False

        for symbol in config.SYMBOLS:
            # 前回実行から TA_ANALYSIS_INTERVAL_SEC 秒経過しているか
            last = last_run.get(symbol)
            if last and (now - last).total_seconds() < config.TA_ANALYSIS_INTERVAL_SEC:
                continue

            yf_ticker = symbol_map.get_yf_ticker(symbol)
            if not yf_ticker:
                logger.warning("[H1] %s: yfinanceティッカー未定義 → スキップ", symbol)
                continue

            with _analysis_lock:
                if _analysis_in_progress.get(symbol):
                    continue
                _analysis_in_progress[symbol] = True

            try:
                trade_date = now.strftime("%Y-%m-%d")
                asset_type = symbol_map.get_asset_type(yf_ticker)

                if _ta is None:
                    continue

                signal = _ta.run_analysis(symbol, yf_ticker, trade_date, asset_type)
                last_run[symbol] = now

                # 分析完了時点のMT5価格を基準価格として保存 (乖離フィルター用)
                signal_rating = str(signal.rating).strip().lower() if signal else ""
                if signal and signal_rating in ("buy", "sell", "overweight"):
                    ref_info = mt5_connector.get_current_price(symbol)
                    if ref_info:
                        mid = (ref_info["bid"] + ref_info["ask"]) / 2
                        _signal_ref_price[symbol] = mid
                        logger.debug("[H1] %s: 基準価格キャッシュ %.5f", symbol, mid)

                if signal:
                    # DBに記録
                    trade_logger.insert_ta_log(
                        symbol=symbol,
                        yf_ticker=yf_ticker,
                        direction=signal.direction,
                        rating=signal.rating,
                        reasoning=signal.reasoning,
                        analysts=signal.analysts_used,
                    )
                    # Discord 通知
                    discord_notifier.send_analysis(
                        symbol=symbol,
                        direction=signal.direction,
                        rating=signal.rating,
                        reasoning=signal.reasoning[:600],
                    )
            except Exception as e:
                logger.error("[H1] %s 分析例外: %s", symbol, e, exc_info=True)
            finally:
                with _analysis_lock:
                    _analysis_in_progress[symbol] = False

        _stop_event.wait(timeout=60)  # 1分ごとにチェック


# ── エグジットチェック ─────────────────────────────────

def _check_exits():
    """保有ポジションのエグジットを機械的に判定する。"""
    positions = mt5_connector.get_positions()
    for pos in positions:
        try:
            _process_exit(pos)
        except Exception as e:
            logger.error("[Exit] %s ticket=%s 例外: %s", pos["symbol"], pos["ticket"], e, exc_info=True)


def _process_exit(pos: dict):
    symbol = pos["symbol"]
    ticket = pos["ticket"]
    direction = pos["type"]
    entry_price = pos["price_open"]
    current_price = pos["price_current"]
    profit = pos["profit"]

    # DB トレード情報取得
    db_trade = trade_logger.get_trade_by_ticket(ticket)

    # ── 利益保護: SL引き上げ ──
    if config.PROFIT_PROTECTION_ENABLED and db_trade:
        _apply_profit_protection(pos, db_trade)
    elif config.PROFIT_PROTECTION_ENABLED and db_trade is None:
        logger.warning("[ProfitProtect] %s ticket=%s: DB未登録 → SL管理スキップ", symbol, ticket)

    # ── 市場クローズ前強制手仕舞い ──
    if _should_session_close(symbol):
        _close_and_log(pos, "SESSION_CLOSE", db_trade)
        return

    # ── 緊急ATRエグジット ──
    if config.EMERGENCY_EXIT_ENABLED and _should_emergency_exit(pos):
        _close_and_log(pos, "EMERGENCY_ATR_EXIT", db_trade)
        return

    # ── シグナルフリップエグジット ──
    if _should_signal_flip_exit(pos):
        _close_and_log(pos, "SIGNAL_FLIP", db_trade)
        return


def _should_session_close(symbol: str) -> bool:
    """市場クローズ前のリードタイム内かを判定する。"""
    if not config.FLAT_BEFORE_WEEKEND_CLOSE_ENABLED and not config.FLAT_BEFORE_MARKET_CLOSE_ENABLED:
        return False
    srv = mt5_connector.get_server_datetime()
    if srv is None:
        return False

    # 週末クローズ: 金曜22:30(サーバー時刻)以降
    if config.FLAT_BEFORE_WEEKEND_CLOSE_ENABLED:
        if srv.weekday() == 4:  # 金曜
            close_time = srv.replace(hour=22, minute=59, second=0, microsecond=0)
            lead = timedelta(minutes=config.FLAT_BEFORE_WEEKEND_CLOSE_LEAD_MINUTES)
            if srv >= close_time - lead:
                return True

    # 日次クローズ
    if config.FLAT_BEFORE_MARKET_CLOSE_ENABLED:
        close_h = config.FLAT_BEFORE_MARKET_CLOSE_HOUR
        close_m = config.FLAT_BEFORE_MARKET_CLOSE_MINUTE
        if close_h == 0 and close_m == 0:
            return False
        close_time = srv.replace(hour=close_h, minute=close_m, second=0, microsecond=0)
        lead = timedelta(minutes=config.FLAT_BEFORE_MARKET_CLOSE_LEAD_MINUTES)
        if srv >= close_time - lead:
            return True

    return False


def _should_emergency_exit(pos: dict) -> bool:
    """ATR急変による緊急エグジット判定。"""
    symbol = pos["symbol"]
    df_m15 = mt5_connector.get_rates(symbol, "M15", 60)
    if df_m15 is None or len(df_m15) < 20:
        return False

    atr = mt5_connector.calculate_atr(df_m15, config.ATR_PERIOD)
    if atr <= 0:
        return False

    threshold = config.EMERGENCY_EXIT_ADVERSE_ATR_BY_SYMBOL.get(
        symbol.rstrip("#."),
        config.EMERGENCY_EXIT_ADVERSE_ATR,
    )

    direction  = pos["type"]
    entry_price = pos["price_open"]
    current    = pos["price_current"]

    if direction == "BUY":
        adverse_move = entry_price - current
    else:
        adverse_move = current - entry_price

    if adverse_move >= atr * threshold:
        logger.warning(
            "[EmergencyExit] %s ticket=%s adverse=%.5f >= ATR×%.1f=%.5f",
            symbol, pos["ticket"], adverse_move, threshold, atr * threshold,
        )
        return True

    # ATRスパイク判定
    spike_mult = config.EMERGENCY_EXIT_ATR_SPIKE_MULTIPLIER
    lookback   = config.EMERGENCY_EXIT_ATR_SPIKE_LOOKBACK
    if len(df_m15) >= lookback:
        recent_atr = mt5_connector.calculate_atr(df_m15.iloc[-lookback:], config.ATR_PERIOD)
        if recent_atr > 0:
            prev_atr = mt5_connector.calculate_atr(df_m15.iloc[:-lookback], config.ATR_PERIOD)
            if prev_atr > 0 and recent_atr > prev_atr * spike_mult:
                min_adverse = config.EMERGENCY_EXIT_ATR_SPIKE_MIN_ADVERSE
                if adverse_move >= atr * min_adverse:
                    logger.warning(
                        "[EmergencyExit] %s ticket=%s ATRスパイク検知 ATR%.5f→%.5f",
                        symbol, pos["ticket"], prev_atr, recent_atr,
                    )
                    return True
    return False


def _should_signal_flip_exit(pos: dict) -> bool:
    """TradingAgentsのシグナルが反転/中立化した場合のエグジット判定。"""
    if _ta is None:
        return False
    symbol    = pos["symbol"].rstrip("#.")   # KIWAMI口座など "#" suffix を除去してキャッシュキーと統一
    direction = pos["type"]  # "BUY" or "SELL"
    signal = _ta.get_cached_signal(symbol)
    if signal is None:
        return False

    if config.TA_EXIT_ON_SIGNAL_FLIP:
        if direction == "BUY" and signal.direction == "SELL":
            logger.info("[SignalFlip] %s ticket=%s: BUY→SELL反転 → exit", symbol, pos["ticket"])
            return True
        if direction == "SELL" and signal.direction == "BUY":
            logger.info("[SignalFlip] %s ticket=%s: SELL→BUY反転 → exit", symbol, pos["ticket"])
            return True
    return False


def _apply_profit_protection(pos: dict, db_trade: dict):
    """利益保護: BreakEven / LockProfit の段階的SL引き上げ。"""
    ticket      = pos["ticket"]
    direction   = pos["type"]
    entry_price = pos["price_open"]
    current_sl  = pos["sl"]
    sl_price    = db_trade.get("sl_price")
    tp_price    = db_trade.get("tp_price")

    if sl_price is None or sl_price <= 0:
        return
    sl_dist = abs(entry_price - sl_price)
    if sl_dist <= 0:
        return

    # シンボル精度取得 (丸め誤差対策)
    sym_info = mt5_connector.get_symbol_info(pos["symbol"])
    digits   = sym_info["digits"] if sym_info else 5
    tick_size = 10 ** (-digits)  # 比較閾値として使用

    def calc_new_sl(lock_r: float) -> float:
        raw = entry_price + sl_dist * lock_r if direction == "BUY" else entry_price - sl_dist * lock_r
        return round(raw, digits)

    current_price = pos["price_current"]
    if direction == "BUY":
        progress_r = (current_price - entry_price) / sl_dist
    else:
        progress_r = (entry_price - current_price) / sl_dist

    logger.info("[ProfitProtect] %s ticket=%s dir=%s progress_r=%.3f current_sl=%.5f entry=%.5f sl_dist=%.5f",
                pos["symbol"], pos["ticket"], direction, progress_r, current_sl, entry_price, sl_dist)

    new_sl: float | None = None

    if progress_r >= config.LOCK_PROFIT_3_TRIGGER_R:
        candidate = calc_new_sl(config.LOCK_PROFIT_3_R)
        if direction == "BUY" and candidate > current_sl + tick_size:
            new_sl = candidate
        elif direction == "SELL" and candidate < current_sl - tick_size:
            new_sl = candidate
    elif progress_r >= config.LOCK_PROFIT_2_TRIGGER_R:
        candidate = calc_new_sl(config.LOCK_PROFIT_2_R)
        if direction == "BUY" and candidate > current_sl + tick_size:
            new_sl = candidate
        elif direction == "SELL" and candidate < current_sl - tick_size:
            new_sl = candidate
    elif progress_r >= config.LOCK_PROFIT_1_TRIGGER_R:
        candidate = calc_new_sl(config.LOCK_PROFIT_1_R)
        if direction == "BUY" and candidate > current_sl + tick_size:
            new_sl = candidate
        elif direction == "SELL" and candidate < current_sl - tick_size:
            new_sl = candidate
    elif progress_r >= config.BREAKEVEN_R:
        be_sl = calc_new_sl(config.BREAKEVEN_BUFFER_R if direction == "BUY" else -config.BREAKEVEN_BUFFER_R)
        # BUYはBEをSELL方向に: entry + buffer。SELLはentry - buffer
        be_sl = round(entry_price + sl_dist * config.BREAKEVEN_BUFFER_R if direction == "BUY"
                      else entry_price - sl_dist * config.BREAKEVEN_BUFFER_R, digits)
        if direction == "BUY" and be_sl > current_sl + tick_size:
            new_sl = be_sl
        elif direction == "SELL" and be_sl < current_sl - tick_size:
            new_sl = be_sl

    if new_sl is not None:
        if mt5_connector.modify_position_sl(ticket, new_sl):
            logger.info("[ProfitProtect] %s ticket=%s: SL %.5f → %.5f",
                        pos["symbol"], ticket, current_sl, new_sl)


def _close_and_log(pos: dict, exit_reason: str, db_trade: dict | None):
    """ポジションをクローズしてDBを更新し、Discord 通知する。"""
    symbol  = pos["symbol"]
    ticket  = pos["ticket"]

    ok = mt5_connector.close_position(ticket)
    if not ok:
        return

    # クローズ後のdeal情報を取得
    deal = mt5_connector.get_closed_deal_by_ticket(ticket)
    exit_price = deal["exit_price"] if deal else pos["price_current"]
    profit     = deal["profit"]     if deal else pos["profit"]

    # DB更新
    if db_trade:
        sl_dist = abs(pos["price_open"] - db_trade.get("sl_price", pos["price_open"]))
        pips = (exit_price - pos["price_open"]) * (1 if pos["type"] == "BUY" else -1)
        trade_logger.close_trade(db_trade["id"], exit_price, pips, profit, exit_reason)

    discord_notifier.send_exit(
        symbol=symbol,
        direction=pos["type"],
        exit_price=exit_price,
        profit=profit,
        exit_reason=exit_reason,
    )
    logger.info("[Exit] %s %s ticket=%s reason=%s profit=%.2f",
                symbol, pos["type"], ticket, exit_reason, profit)


# ── 孤立トレード照合 ──────────────────────

def _reconcile_orphaned_db_trades():
    """MT5でTP/SL自動決済されたがDBがOPENのままのトレードを照合・更新する。"""
    db_trades = trade_logger.get_open_trades()
    mt5_tickets = {p["ticket"] for p in mt5_connector.get_positions()}
    for trade in db_trades:
        ticket = trade.get("mt5_ticket")
        if not ticket or ticket in mt5_tickets:
            continue
        deal = mt5_connector.get_closed_deal_by_ticket(ticket)
        if deal is None:
            continue
        pips = (deal["exit_price"] - trade["entry_price"]) * (
            1 if trade["direction"] == "BUY" else -1
        )
        trade_logger.close_trade(
            trade["id"], deal["exit_price"], pips, deal["profit"],
            deal.get("exit_reason", "MT5_AUTO"),
        )
        logger.info("[Reconcile] ticket=%s closed by MT5: reason=%s profit=%.2f",
                    ticket, deal.get("exit_reason"), deal["profit"])


# ── エントリーチェック ─────────────────────────────────

def _check_entries(m15_bar_index: int):
    """各銘柄のエントリー可否をチェックする。"""
    for symbol in config.SYMBOLS:
        try:
            _check_entry(symbol, m15_bar_index)
        except Exception as e:
            logger.error("[Entry] %s 例外: %s", symbol, e, exc_info=True)
            discord_notifier.send_error(f"エントリーチェック例外: {symbol}", str(e))


def _normalize_ta_rating(rating: str) -> str:
    return str(rating).strip().lower()


def _get_symbol_positions(symbol: str) -> list[dict]:
    base_symbol = symbol.rstrip("#.")
    return [
        p for p in mt5_connector.get_positions()
        if p["symbol"].rstrip("#.") == base_symbol
    ]


def _resolve_entry_direction(signal: TASignal, symbol_positions: list[dict]) -> str | None:
    """レーティングと保有状況からエントリー方向を決定する (ロング専用モード)。

    Buy        : ロングなし → 新規エントリー / ロングあり → 買い増し (H1シグナルごと1回)
    Overweight : 既存ロングへの追加 (H1シグナルごと1回)
    Hold       : 維持のみ → None
    Underweight: 縮小対象 → None (別途 _maybe_reduce_underweight で処理)
    Sell       : 全クローズ → None (エグジット側 / _should_signal_flip_exit で処理)
    """
    rating = _normalize_ta_rating(signal.rating)
    long_positions = [p for p in symbol_positions if p["type"] == "BUY"]
    if rating == "buy":
        return "BUY"  # 既存なし→新規 / 既存あり→買い増し (throttleは_check_entry側で管理)
    if rating == "overweight":
        if long_positions:
            return "BUY"
    return None


def _maybe_reduce_underweight(symbol: str, signal: TASignal, symbol_positions: list[dict]) -> None:
    if _normalize_ta_rating(signal.rating) != "underweight":
        return

    last_handled = _last_underweight_reduction_at.get(symbol)
    if last_handled is not None and last_handled >= signal.timestamp:
        return

    if len(symbol_positions) <= 1:
        logger.info("[Underweight] %s: 単一ポジション以下のため縮小せず維持", symbol)
        _last_underweight_reduction_at[symbol] = signal.timestamp
        return

    existing_dirs = {p["type"] for p in symbol_positions}
    if len(existing_dirs) != 1:
        logger.info("[Underweight] %s: 保有方向が混在しているため縮小スキップ", symbol)
        _last_underweight_reduction_at[symbol] = signal.timestamp
        return

    reduce_pos = max(symbol_positions, key=lambda p: p["ticket"])
    db_trade = trade_logger.get_trade_by_ticket(reduce_pos["ticket"])
    logger.info(
        "[Underweight] %s: %s ticket=%s を1本縮小",
        symbol, reduce_pos["type"], reduce_pos["ticket"],
    )
    _close_and_log(reduce_pos, "UNDERWEIGHT_REDUCE", db_trade)

    remaining_tickets = {p["ticket"] for p in _get_symbol_positions(symbol)}
    if reduce_pos["ticket"] not in remaining_tickets:
        _last_underweight_reduction_at[symbol] = signal.timestamp


def _check_entry(symbol: str, m15_bar_index: int):
    # ── 事前チェック: 市場・TA分析可否 ──
    if not mt5_connector.is_symbol_market_active(symbol):
        logger.info("[Entry] %s: 市場非アクティブ → スキップ", symbol)
        return

    if not mt5_connector.is_fx_market_open():
        logger.info("[Entry] %s: FX市場クローズ中 → スキップ", symbol)
        return

    # SESSION_CLOSE直後の即再エントリーを防ぐ
    if _should_session_close(symbol):
        logger.info("[Entry] %s: セッションクローズ期間中 → スキップ", symbol)
        return

    # キャッシュシグナル取得
    if _ta is None or not _ta.is_available:
        logger.debug("[Entry] TA未初期化: %s スキップ", symbol)
        return

    signal = _ta.get_cached_signal(symbol)
    if signal is None:
        logger.info("[Entry] %s: TAキャッシュなし / 期限切れ → スキップ", symbol)
        return

    symbol_positions = _get_symbol_positions(symbol)
    _maybe_reduce_underweight(symbol, signal, symbol_positions)
    symbol_positions = _get_symbol_positions(symbol)

    direction = _resolve_entry_direction(signal, symbol_positions)
    if direction is None:
        rating = _normalize_ta_rating(signal.rating)
        if rating == "hold":
            logger.info("[Entry] %s: Hold → 維持のみ / 新規なし", symbol)
        elif rating == "underweight":
            logger.info("[Entry] %s: Underweight → 縮小/見送り", symbol)
        elif rating == "overweight":
            logger.info("[Entry] %s: Overweight だが既存ロングなし → 新規なし", symbol)
        elif rating == "sell":
            logger.info("[Entry] %s: Sell → ロング全クローズ (エグジット側で処理)", symbol)
        else:
            logger.info("[Entry] %s: 非エントリー評価(%s) → スキップ", symbol, signal.rating)
        return

    # ── 買い増しゲート: Buy(既存あり) / Overweight → 同一H1シグナルで1回のみ ──
    rating = _normalize_ta_rating(signal.rating)
    existing_long_positions = [p for p in symbol_positions if p["type"] == "BUY"]
    is_pyramid_add = (rating == "overweight") or (rating == "buy" and len(existing_long_positions) > 0)
    if is_pyramid_add:
        last_ow = _last_overweight_add_at.get(symbol)
        if last_ow is not None and last_ow >= signal.timestamp:
            logger.info(
                "[Entry] %s: 買い増し(%s)は今回H1シグナル(%s)で実施済み → スキップ",
                symbol, signal.rating, signal.timestamp.strftime("%H:%M"),
            )
            return

    # ── クールダウン ──
    last_bar = _last_entry_bar.get(symbol, -999)
    if m15_bar_index - last_bar < config.SYMBOL_REENTRY_COOLDOWN_BARS:
        logger.info("[Entry] %s: クールダウン中 (%d/%d本)", symbol,
                     m15_bar_index - last_bar, config.SYMBOL_REENTRY_COOLDOWN_BARS)
        return

    # ── 積み増し ADX+EMAトレンドフィルター ──
    # 同方向ポジションが既にある場合（積み増し）: ADX閾値以上かつEMAクロスが合致している場合のみ許可
    if config.REENTRY_TREND_FILTER:
        same_dir_positions = [p for p in symbol_positions if p["type"] == direction]
        if same_dir_positions:
            needed = max(config.REENTRY_ADX_PERIOD, config.REENTRY_EMA_SLOW) * 3
            df_trend = mt5_connector.get_rates(symbol, "M15", needed)
            if df_trend is None or len(df_trend) < needed // 2:
                logger.info("[Entry] %s: 積み増しフィルター用データ不足 → スキップ", symbol)
                return
            adx_series  = mt5_connector.calculate_adx(df_trend, config.REENTRY_ADX_PERIOD)
            ema_fast    = mt5_connector.calculate_ema(df_trend, config.REENTRY_EMA_FAST)
            ema_slow    = mt5_connector.calculate_ema(df_trend, config.REENTRY_EMA_SLOW)
            adx_val     = float(adx_series.iloc[-1])
            ema_cross_ok = (
                ema_fast.iloc[-1] > ema_slow.iloc[-1] if direction == "BUY"
                else ema_fast.iloc[-1] < ema_slow.iloc[-1]
            )
            if adx_val < config.REENTRY_ADX_THRESHOLD or not ema_cross_ok:
                logger.info(
                    "[Entry] %s: 積み増しトレンド不足 ADX=%.1f(要%.0f) EMA%d/EMA%dクロス=%s → スキップ",
                    symbol, adx_val, config.REENTRY_ADX_THRESHOLD,
                    config.REENTRY_EMA_FAST, config.REENTRY_EMA_SLOW, ema_cross_ok,
                )
                return

    # ── リスクチェック ──
    ok, reason = risk_manager.can_open_position(symbol)
    if not ok:
        discord_notifier.send_skip(symbol, reason)
        return


    # ── 市場ストレスチェック ──
    stress = market_stress.get_stress_state(symbol)
    if stress is not None:
        discord_notifier.send_skip(symbol, f"市場ストレス中: {stress.summary[:100]}")
        return

    # ── SL/TP 計算 ──
    df_m15 = mt5_connector.get_rates(symbol, "M15", 50)
    if df_m15 is None or len(df_m15) < 15:
        logger.warning("[Entry] %s: M15データ不足", symbol)
        return

    atr = mt5_connector.calculate_atr(df_m15, config.ATR_PERIOD)
    if atr <= 0:
        return

    # 市場ストレス更新
    sym_info = mt5_connector.get_symbol_info(symbol)
    if sym_info:
        atr_sma = mt5_connector.calculate_atr_sma(df_m15, config.ATR_PERIOD, 50)
        market_stress.check_and_update(symbol, sym_info["spread"], atr, atr_sma)

    price_info = mt5_connector.get_current_price(symbol)
    if price_info is None:
        return

    # ── 乖離フィルター ──
    # シグナルキャッシュ時点の価格から現在価格が ATR×N 以上離れていたらスキップ
    if config.ENTRY_DRIFT_FILTER_ATR_MULT > 0:
        ref_price = _signal_ref_price.get(symbol.rstrip("#."))
        if ref_price is not None:
            current_mid = (price_info["bid"] + price_info["ask"]) / 2
            drift = abs(current_mid - ref_price)
            drift_limit = atr * config.ENTRY_DRIFT_FILTER_ATR_MULT
            if drift > drift_limit:
                logger.info(
                    "[Entry] %s: 乖離フィルター発動 drift=%.5f > ATR×%.1f=%.5f → スキップ",
                    symbol, drift, config.ENTRY_DRIFT_FILTER_ATR_MULT, drift_limit,
                )
                discord_notifier.send_skip(symbol, f"乖離過大: {drift:.5f} > ATR×{config.ENTRY_DRIFT_FILTER_ATR_MULT}")
                return

    # ── 直近足方向フィルター ──
    # 直前確定M15足がシグナルと同方向 (BUY=陽線 / SELL=陰線) のときのみ発注
    if config.ENTRY_CANDLE_DIR_FILTER:
        last_candle = df_m15.iloc[-2]  # -1は現在形成中のため-2が直近確定足
        candle_bullish = float(last_candle["close"]) > float(last_candle["open"])
        if direction == "BUY" and not candle_bullish:
            logger.info("[Entry] %s: 直近足陰線 → BUYスキップ", symbol)
            return
        if direction == "SELL" and candle_bullish:
            logger.info("[Entry] %s: 直近足陽線 → SELLスキップ", symbol)
            return

    sl_mult = config.ENTRY_SL_ATR_MULT_BY_SYMBOL.get(
        symbol.rstrip("#."),
        config.ENTRY_SL_ATR_MULT,
    )
    sl_dist = atr * sl_mult
    tp_dist = sl_dist * config.ENTRY_TP_R

    if direction == "BUY":
        entry_price = price_info["ask"]
        sl = round(entry_price - sl_dist, 5)
        tp = round(entry_price + tp_dist, 5)
    else:
        entry_price = price_info["bid"]
        sl = round(entry_price + sl_dist, 5)
        tp = round(entry_price - tp_dist, 5)

    # ── ロット計算 ──
    lot = lot_calculator.calculate_lot(symbol, sl_dist)
    if lot is None or lot <= 0:
        logger.warning("[Entry] %s: ロット計算失敗", symbol)
        return

    # 復帰後慎重モードのロット縮小
    lot_mult = market_stress.get_lot_multiplier(symbol)
    if lot_mult < 1.0:
        vol_min = (mt5_connector.get_symbol_info(symbol) or {}).get("volume_min", 0.01)
        lot = max(vol_min, round(lot * lot_mult, 2))
        market_stress.consume_post_recovery_trade(symbol)
        logger.info("[Entry] %s: 復帰後慎重モード → lot縮小 (×%.1f)", symbol, lot_mult)

    # 買い増し(Buy満起およびOverweight)はロットを縮小 (OVERWEIGHT_LOT_MULT 倍)
    if is_pyramid_add:
        vol_min = (mt5_connector.get_symbol_info(symbol) or {}).get("volume_min", 0.01)
        lot = max(vol_min, round(lot * config.OVERWEIGHT_LOT_MULT, 2))
        logger.info("[Entry] %s: Overweight買い増し → lot縮小 (×%.1f → %.2f)", symbol, config.OVERWEIGHT_LOT_MULT, lot)

    # ── 注文発行 ──
    logger.info("[Entry] %s %s: entry=%.5f SL=%.5f TP=%.5f lot=%.2f ATR=%.5f rating=%s",
                symbol, direction, entry_price, sl, tp, lot, atr, signal.rating)

    ticket = mt5_connector.place_order(symbol, direction, lot, sl, tp)
    if ticket is None:
        return

    # ── DB / 通知 ──
    trade_logger.insert_trade(
        symbol=symbol,
        direction=direction,
        entry_price=entry_price,
        lot_size=lot,
        sl_price=sl,
        tp_price=tp,
        ta_rating=signal.rating,
        ta_direction=signal.direction,
        ta_reasoning=signal.reasoning[:500],
        mt5_ticket=ticket,
    )
    discord_notifier.send_entry(
        symbol=symbol,
        direction=direction,
        lot=lot,
        entry_price=entry_price,
        sl=sl,
        tp=tp,
        rating=signal.rating,
    )
    _last_entry_bar[symbol] = m15_bar_index
    # エントリー成功時は常に _last_overweight_add_at を更新する
    # (Buy新規含む: 同一H1サイクル内で次のM15に再びエントリーしないよう封鎖)
    _last_overweight_add_at[symbol] = signal.timestamp


# ── DB メンテナンス ──────────────────────

def _run_db_maintenance(full_vacuum: bool = False):
    try:
        trade_logger.run_maintenance(full_vacuum)
    except Exception as e:
        logger.error("DBメンテナンス例外: %s", e)


# ── Heartbeat ──────────────────────────

def _send_heartbeat():
    account = mt5_connector.get_account_info()
    if account is None:
        return
    balance = account["balance"]
    equity  = account["equity"]
    dd_pct  = max(0.0, (balance - equity) / balance * 100) if balance > 0 else 0.0
    open_positions = len(mt5_connector.get_positions())
    if dd_pct >= config.HEARTBEAT_NOTIFY_DRAWDOWN_PCT:
        discord_notifier.send_heartbeat(balance, equity, open_positions, dd_pct)
    logger.info("[Heartbeat] balance=%.0f equity=%.0f positions=%d DD=%.1f%%",
                balance, equity, open_positions, dd_pct)


# ── 週次スクリーニング ────────────────────

def _maybe_run_weekly_screening() -> None:
    """月曜日に週次スクリーニングを実行する (1週間に1回)。"""
    global _last_screening_isoweek

    if not config.WEEKLY_SCREENING_ENABLED:
        return

    now = datetime.now()
    # WEEKLY_SCREENING_DOW: 0=月曜 … 6=日曜 (Python weekday)
    if now.weekday() != config.WEEKLY_SCREENING_DOW:
        return

    current_week = now.isocalendar()[1]
    if _last_screening_isoweek == current_week:
        return  # 今週は実行済み

    if _ta is None:
        logger.warning("[WeeklyScreening] TAAnalyzerが未初期化のためスキップ")
        return

    logger.info("[WeeklyScreening] 週次スクリーニング開始 (week=%d)", current_week)
    try:
        selected = weekly_screener.run_weekly_screening(_ta)
        _last_screening_isoweek = current_week
        if selected:
            # シンボル変更に伴うキャッシュ・状態をリセット
            _ta.clear_cache()
            _signal_ref_price.clear()
            _last_entry_bar.clear()
            _last_underweight_reduction_at.clear()
            _last_overweight_add_at.clear()
            logger.info("[WeeklyScreening] 監視銘柄変更 → キャッシュリセット完了")
    except Exception as e:
        logger.error("[WeeklyScreening] 例外: %s", e, exc_info=True)


# ── 起動時チェック ──────────────────────

def _startup_check():
    if mt5_connector.is_fx_market_open():
        return
    positions = mt5_connector.get_positions()
    if positions:
        symbols = [p["symbol"] for p in positions]
        msg = f"[警告] 週末跨ぎポジション検出: {symbols}"
        logger.warning(msg)
        discord_notifier.send_skip("SYSTEM", msg, notify=True)


# ── メインループ ────────────────────────

def main():
    global _ta, _h1_thread

    logger.info("=" * 60)
    logger.info("Multi-Agent MT5 Trading System 起動")
    logger.info("監視銘柄: %s", config.SYMBOLS)
    logger.info("TA アナリスト: market=%s news=%s social=%s fundamentals=%s",
                config.TA_USE_MARKET, config.TA_USE_NEWS, config.TA_USE_SOCIAL, config.TA_USE_FUNDAMENTALS)
    logger.info("=" * 60)

    # DB 初期化
    trade_logger.init_db()
    # 起動直後はinit_dbのコネクションと競合するためメンテナンスをスキップ
    # (1時間ごとのループ内でメンテナンスを実行)

    # MT5 接続
    if not mt5_connector.initialize():
        logger.critical("MT5接続失敗 → 終了")
        return

    # TradingAgents 初期化
    _ta = TAAnalyzer()
    if not _ta.is_available:
        logger.critical("TradingAgents初期化失敗 → 終了")
        return

    # H1 分析スレッド起動
    _stop_event.clear()
    _h1_thread = threading.Thread(target=_h1_analysis_loop, daemon=True, name="H1-Analysis")
    _h1_thread.start()
    logger.info("H1分析スレッド起動完了")

    # 起動通知
    account = mt5_connector.get_account_info()
    if account:
        discord_notifier.send_heartbeat(
            account["balance"], account["equity"],
            len(mt5_connector.get_positions()),
        )

    _startup_check()

    # 週次スクリーニング (月曜起動時)
    _maybe_run_weekly_screening()

    last_heartbeat    = datetime.now()
    last_db_maint     = datetime.now()
    last_full_vacuum  = datetime.now()
    last_cycle_minute = -1
    m15_bar_counter   = 0

    try:
        while True:
            now = datetime.now()

            # ── Heartbeat (1時間ごと) ──
            if (now - last_heartbeat).total_seconds() >= config.HEARTBEAT_INTERVAL_SEC:
                _send_heartbeat()
                _maybe_run_weekly_screening()  # 月曜の毎時チェック
                last_heartbeat = now

            # ── DB メンテナンス ──
            if (now - last_db_maint).total_seconds() >= config.DB_MAINTENANCE_INTERVAL_SEC:
                do_vacuum = (now - last_full_vacuum).total_seconds() >= config.DB_FULL_VACUUM_INTERVAL_SEC
                _run_db_maintenance(full_vacuum=do_vacuum)
                last_db_maint = now
                if do_vacuum:
                    last_full_vacuum = now

            # ── 市場クローズ中: ポジションなし時はスリープ ──
            if not mt5_connector.is_fx_market_open():
                if not mt5_connector.get_positions():
                    time.sleep(300)
                    continue

            # ── M15 足確定タイミング (00, 15, 30, 45分) ──
            if now.minute % 15 == 0 and now.minute != last_cycle_minute:
                time.sleep(config.CANDLE_WAIT_SEC)
                last_cycle_minute = now.minute
                m15_bar_counter   += 1
                logger.info("── M15サイクル #%d (%s) ──", m15_bar_counter,
                            now.strftime("%H:%M"))

                if not mt5_connector.ensure_connected():
                    continue

                try:
                    _reconcile_orphaned_db_trades()
                    _check_exits()
                    _check_entries(m15_bar_counter)
                except Exception as e:
                    logger.error("M15サイクル例外: %s", e, exc_info=True)
                    discord_notifier.send_error("M15サイクル例外", str(e))

            time.sleep(config.MAIN_LOOP_SLEEP_SEC)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt → シャットダウン")
    except Exception as e:
        logger.critical("予期せぬエラー: %s", e, exc_info=True)
        discord_notifier.send_error("致命的エラー", str(e))
    finally:
        _stop_event.set()
        if _h1_thread:
            _h1_thread.join(timeout=10)
        mt5_connector.shutdown()
        logger.info("システム終了")


if __name__ == "__main__":
    main()
