"""保有ポジション状態の管理（最重要）。

MT5 から現在の保有状態を取得し、TradingAgents への注入パッチが使える形式へ変換する。
ヘッジ口座（margin_mode=2）のため、同一シンボルに複数チケットが並存しうる。
シンボル単位で total_lots を集約し、avg_entry_price は加重平均で算出する。
initial_lots / scale_count は MT5 から取れないため position_meta.json から補う。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

from core import config_loader as cfg
from core import persistence
from logger import get_logger

log = get_logger("portfolio_state")


@dataclass
class TicketInfo:
    ticket: int
    lots: float
    entry_price: float
    sl: float


@dataclass
class PositionInfo:
    ticker: str               # 例: "NVDA"（TradingAgents に渡す名前）
    mt5_symbol: str           # 例: "Nvidia"（MT5 実シンボル名＝会社名）
    direction: str            # "BUY" のみ（v3.2 はロングのみ）
    total_lots: float
    avg_entry_price: float
    current_sl: float         # チケット群のうち最も低い（＝最も未保護な）SL
    unrealized_pnl: float     # 口座通貨（JPY）
    initial_lots: float
    scale_count: int
    entry_date: str           # YYYY-MM-DD
    sector: str = ""
    tickets: list[TicketInfo] = field(default_factory=list)


@dataclass
class PortfolioState:
    positions: list[PositionInfo] = field(default_factory=list)
    account_balance: float = 0.0
    account_equity: float = 0.0
    margin_level_pct: float = 0.0
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    snapshot_time: str = ""
    max_positions: int = 6

    def get_position(self, ticker: str) -> "PositionInfo | None":
        return next((p for p in self.positions if p.ticker == ticker), None)

    def sector_count(self, sector: str) -> int:
        return sum(1 for p in self.positions if p.sector == sector)

    def available_slots(self) -> int:
        return self.max_positions - len(self.positions)

    def to_llm_context(self) -> str:
        """注入パッチがプロンプトへ差し込むポートフォリオ全体テキスト。"""
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
        lines.append(
            f"Available new position slots: {self.available_slots()}/{self.max_positions}"
        )
        lines.append(f"Today's P&L: ¥{self.daily_pnl:+.0f}")
        lines.append(f"Weekly P&L: ¥{self.weekly_pnl:+.0f}")
        lines.append("=================================")
        return "\n".join(lines)


def _week_start_utc(now: datetime) -> datetime:
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _realized_pnl_since(since: datetime, now: datetime) -> float:
    """指定期間に約定した決済 deal の損益合計（profit + swap + commission）。"""
    deals = mt5.history_deals_get(since, now)
    if deals is None:
        return 0.0
    total = 0.0
    for d in deals:
        # entry=1（DEAL_ENTRY_OUT / 決済）のみを実現損益として集計
        if getattr(d, "entry", None) == mt5.DEAL_ENTRY_OUT:
            total += float(d.profit) + float(d.swap) + float(d.commission)
    return total


def sync_from_mt5() -> PortfolioState:
    """MT5 から保有状態を取得し PortfolioState を構築する。

    取得失敗時は例外を raise する（中途半端に処理しないため scheduler 側で当日スキップ）。
    """
    account = mt5.account_info()
    if account is None:
        raise RuntimeError(f"account_info() failed: {mt5.last_error()}")

    risk = cfg.load_risk()
    max_positions = int(risk["portfolio"]["max_positions"])

    raw_positions = mt5.positions_get()
    if raw_positions is None:
        raise RuntimeError(f"positions_get() failed: {mt5.last_error()}")

    meta = persistence.load_position_meta()

    # mt5_symbol 単位で集約（ヘッジ口座対応）
    grouped: dict[str, list] = {}
    for pos in raw_positions:
        if pos.type != mt5.POSITION_TYPE_BUY:
            # v3.2 はロングのみ。売りポジションは想定外だが記録して除外する。
            log.warning("Non-BUY position detected and ignored: %s ticket=%s", pos.symbol, pos.ticket)
            continue
        grouped.setdefault(pos.symbol, []).append(pos)

    positions: list[PositionInfo] = []
    for mt5_symbol, group in grouped.items():
        spec = cfg.spec_for_mt5(mt5_symbol)
        if spec is None:
            log.warning("Position on unknown symbol (not in symbols.yaml): %s", mt5_symbol)
            continue

        total_lots = sum(g.volume for g in group)
        weighted = sum(g.price_open * g.volume for g in group)
        avg_entry = weighted / total_lots if total_lots else 0.0
        unrealized = sum(g.profit for g in group)
        # 最も低い SL（0=SL未設定は除外して評価。全て0なら0）
        sls = [g.sl for g in group if g.sl > 0]
        current_sl = min(sls) if sls else 0.0

        tickets = [
            TicketInfo(ticket=g.ticket, lots=float(g.volume),
                       entry_price=float(g.price_open), sl=float(g.sl))
            for g in group
        ]

        m = meta.get(mt5_symbol)
        if m is None:
            log.warning(
                "%s: no position_meta entry. Assuming initial_lots=%.2f scale_count=0.",
                mt5_symbol, total_lots,
            )
            initial_lots = total_lots
            scale_count = 0
            entry_date = datetime.fromtimestamp(min(g.time for g in group)).strftime("%Y-%m-%d")
        else:
            initial_lots = float(m.get("initial_lots", total_lots))
            scale_count = int(m.get("scale_count", 0))
            entry_date = m.get("entry_date", "")

        positions.append(
            PositionInfo(
                ticker=spec.ticker,
                mt5_symbol=mt5_symbol,
                direction="BUY",
                total_lots=round(total_lots, 2),
                avg_entry_price=avg_entry,
                current_sl=current_sl,
                unrealized_pnl=unrealized,
                initial_lots=initial_lots,
                scale_count=scale_count,
                entry_date=entry_date,
                sector=spec.sector,
                tickets=tickets,
            )
        )

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = _week_start_utc(now)
    daily_pnl = _realized_pnl_since(day_start, now)
    weekly_pnl = _realized_pnl_since(week_start, now)

    state = PortfolioState(
        positions=positions,
        account_balance=float(account.balance),
        account_equity=float(account.equity),
        margin_level_pct=float(account.margin_level) if account.margin > 0 else float("inf"),
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        snapshot_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        max_positions=max_positions,
    )
    log.info("Synced %d positions from MT5 (hedging).", len(positions))
    return state
