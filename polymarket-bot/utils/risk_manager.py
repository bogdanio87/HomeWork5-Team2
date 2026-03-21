"""
Risk Management Module.

Controls position sizing, enforces stop-losses and take-profits,
tracks daily P&L, and prevents catastrophic losses.

Uses Kelly Criterion for optimal position sizing and enforces
hard limits on exposure.
"""

import time
from dataclasses import dataclass, field
from utils.logger import log


@dataclass
class Position:
    token_id: str
    market_name: str
    side: str  # BUY or SELL
    entry_price: float
    size: float
    strategy: str
    stop_loss: float = 0.0
    take_profit: float = 0.0
    entry_time: float = 0.0
    unrealized_pnl: float = 0.0
    order_id: str = ""

    def update_pnl(self, current_price: float):
        if self.side == "BUY":
            self.unrealized_pnl = (current_price - self.entry_price) * self.size
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.size


@dataclass
class PortfolioState:
    balance: float = 0.0
    initial_balance: float = 0.0
    positions: dict = field(default_factory=dict)  # token_id -> Position
    realized_pnl: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_reset_time: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    peak_balance: float = 0.0


class RiskManager:
    """Manages portfolio risk and enforces trading limits."""

    def __init__(self, risk_config: dict, initial_balance: float,
                 max_daily_loss: float):
        self.risk = risk_config
        self.max_daily_loss = max_daily_loss
        self.portfolio = PortfolioState(
            balance=initial_balance,
            initial_balance=initial_balance,
            peak_balance=initial_balance,
            daily_pnl_reset_time=time.time(),
        )

    @property
    def total_equity(self) -> float:
        """Current total equity including unrealized P&L."""
        unrealized = sum(p.unrealized_pnl for p in self.portfolio.positions.values())
        return self.portfolio.balance + unrealized

    @property
    def open_position_count(self) -> int:
        return len(self.portfolio.positions)

    @property
    def win_rate(self) -> float:
        total = self.portfolio.winning_trades + self.portfolio.losing_trades
        return self.portfolio.winning_trades / total if total > 0 else 0.0

    def can_open_position(self, size: float, price: float) -> tuple[bool, str]:
        """Check if a new position can be opened within risk limits."""
        cost = size * price

        # Check max open positions
        if self.open_position_count >= self.risk["max_open_positions"]:
            return False, f"Max positions reached ({self.risk['max_open_positions']})"

        # Check position size limit
        max_pos = self.total_equity * self.risk["max_position_pct"]
        if cost > max_pos:
            return False, f"Position too large: ${cost:.2f} > max ${max_pos:.2f}"

        # Check available balance
        if cost > self.portfolio.balance:
            return False, f"Insufficient balance: ${self.portfolio.balance:.2f}"

        # Check daily loss limit
        if self.portfolio.daily_pnl <= -self.max_daily_loss:
            return False, f"Daily loss limit reached: ${self.portfolio.daily_pnl:.2f}"

        # Max drawdown check (25% from peak)
        drawdown = (self.portfolio.peak_balance - self.total_equity) / self.portfolio.peak_balance
        if drawdown > 0.25:
            return False, f"Max drawdown reached: {drawdown:.1%}"

        return True, "OK"

    def calculate_position_size(self, price: float, edge: float,
                                 win_probability: float = 0.55) -> int:
        """
        Calculate optimal position size using Kelly Criterion.

        Kelly fraction = (bp - q) / b
        where b = odds, p = win prob, q = loss prob
        """
        if price <= 0 or edge <= 0:
            return 0

        b = edge / self.risk["stop_loss"] if self.risk["stop_loss"] > 0 else 1.0
        p = win_probability
        q = 1 - p

        kelly = (b * p - q) / b if b > 0 else 0
        kelly = max(0, min(kelly, 1.0))

        # Apply kelly fraction (fractional Kelly for safety)
        kelly_adjusted = kelly * self.risk["kelly_fraction"]

        max_position_value = self.total_equity * self.risk["max_position_pct"]
        kelly_position_value = self.total_equity * kelly_adjusted

        position_value = min(max_position_value, kelly_position_value)
        num_shares = int(position_value / price)

        return max(0, num_shares)

    def open_position(self, token_id: str, market_name: str, side: str,
                       entry_price: float, size: int, strategy: str,
                       stop_loss: float = 0, take_profit: float = 0,
                       order_id: str = "") -> bool:
        """Record a new open position."""
        can_open, reason = self.can_open_position(size, entry_price)
        if not can_open:
            log.warning(f"Cannot open position: {reason}")
            return False

        cost = size * entry_price
        if side == "BUY":
            self.portfolio.balance -= cost

        # Default stop/take-profit if not specified
        if stop_loss == 0:
            stop_loss = (entry_price * (1 - self.risk["stop_loss"]) if side == "BUY"
                         else entry_price * (1 + self.risk["stop_loss"]))
        if take_profit == 0:
            take_profit = (entry_price * (1 + self.risk["take_profit"]) if side == "BUY"
                           else entry_price * (1 - self.risk["take_profit"]))

        position = Position(
            token_id=token_id,
            market_name=market_name,
            side=side,
            entry_price=entry_price,
            size=size,
            strategy=strategy,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_time=time.time(),
            order_id=order_id,
        )

        self.portfolio.positions[token_id] = position
        self.portfolio.total_trades += 1

        log.info(f"[RISK] Opened {side} position: {size} shares @ ${entry_price:.3f} "
                 f"({strategy}) SL={stop_loss:.3f} TP={take_profit:.3f}")

        return True

    def close_position(self, token_id: str, exit_price: float) -> float:
        """Close a position and record P&L."""
        if token_id not in self.portfolio.positions:
            return 0.0

        position = self.portfolio.positions[token_id]
        position.update_pnl(exit_price)
        pnl = position.unrealized_pnl

        # Update balance
        if position.side == "BUY":
            self.portfolio.balance += position.size * exit_price
        else:
            self.portfolio.balance += pnl

        # Track P&L
        self.portfolio.realized_pnl += pnl
        self.portfolio.daily_pnl += pnl

        if pnl > 0:
            self.portfolio.winning_trades += 1
        else:
            self.portfolio.losing_trades += 1

        # Update peak balance
        if self.total_equity > self.portfolio.peak_balance:
            self.portfolio.peak_balance = self.total_equity

        log.info(f"[RISK] Closed position {token_id[:16]}: PnL=${pnl:.2f} "
                 f"(entry={position.entry_price:.3f}, exit={exit_price:.3f})")

        del self.portfolio.positions[token_id]
        return pnl

    def check_stop_losses(self, api) -> list[tuple[str, float]]:
        """Check all positions for stop-loss or take-profit triggers.

        Returns list of (token_id, trigger_price) tuples so the caller
        can close at the same price that triggered the stop.
        """
        to_close = []

        for token_id, position in list(self.portfolio.positions.items()):
            current_price = api.get_midpoint(token_id)
            if current_price is None:
                continue

            position.update_pnl(current_price)

            should_close = False
            reason = ""

            if position.side == "BUY":
                if current_price <= position.stop_loss:
                    should_close = True
                    reason = "STOP_LOSS"
                elif current_price >= position.take_profit:
                    should_close = True
                    reason = "TAKE_PROFIT"
            else:
                if current_price >= position.stop_loss:
                    should_close = True
                    reason = "STOP_LOSS"
                elif current_price <= position.take_profit:
                    should_close = True
                    reason = "TAKE_PROFIT"

            if should_close:
                log.info(f"[RISK] {reason} triggered for {position.market_name[:40]}: "
                         f"price={current_price:.3f}")
                to_close.append((token_id, current_price))

        return to_close

    def reset_daily_pnl(self):
        """Reset daily P&L counter (call at start of each trading day)."""
        log.info(f"[RISK] Daily PnL reset. Yesterday: ${self.portfolio.daily_pnl:.2f}")
        self.portfolio.daily_pnl = 0.0
        self.portfolio.daily_pnl_reset_time = time.time()

    def get_status(self) -> dict:
        """Get current portfolio status summary."""
        unrealized = sum(p.unrealized_pnl for p in self.portfolio.positions.values())
        return {
            "balance": self.portfolio.balance,
            "total_equity": self.total_equity,
            "unrealized_pnl": unrealized,
            "realized_pnl": self.portfolio.realized_pnl,
            "daily_pnl": self.portfolio.daily_pnl,
            "open_positions": self.open_position_count,
            "total_trades": self.portfolio.total_trades,
            "win_rate": self.win_rate,
            "peak_balance": self.portfolio.peak_balance,
            "drawdown": (self.portfolio.peak_balance - self.total_equity) / self.portfolio.peak_balance
            if self.portfolio.peak_balance > 0 else 0,
            "return_pct": (self.total_equity - self.portfolio.initial_balance)
            / self.portfolio.initial_balance * 100,
        }

    def print_status(self):
        """Log current portfolio status."""
        s = self.get_status()
        log.info("=" * 60)
        log.info(f"  PORTFOLIO STATUS")
        log.info(f"  Balance:       ${s['balance']:.2f}")
        log.info(f"  Total Equity:  ${s['total_equity']:.2f}")
        log.info(f"  Unrealized:    ${s['unrealized_pnl']:.2f}")
        log.info(f"  Realized PnL:  ${s['realized_pnl']:.2f}")
        log.info(f"  Daily PnL:     ${s['daily_pnl']:.2f}")
        log.info(f"  Positions:     {s['open_positions']}")
        log.info(f"  Win Rate:      {s['win_rate']:.1%}")
        log.info(f"  Return:        {s['return_pct']:.1f}%")
        log.info(f"  Drawdown:      {s['drawdown']:.1%}")
        log.info("=" * 60)
