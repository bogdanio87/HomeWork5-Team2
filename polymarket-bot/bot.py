"""
Polymarket Trading Bot — Main Runner.

Combines multiple trading strategies (arbitrage, momentum, market-making)
with risk management to find and execute profitable trades.

Usage:
    python bot.py                   # Run with default settings
    python bot.py --strategy momentum  # Run specific strategy
    python bot.py --dry-run         # Simulate without placing orders
"""

import argparse
import signal
import sys
import time
from datetime import datetime, timedelta

from config.settings import (
    INITIAL_BALANCE, MAX_DAILY_LOSS, RISK, STRATEGY,
    MARKET_SCAN_INTERVAL, ORDER_CHECK_INTERVAL, PORTFOLIO_UPDATE_INTERVAL,
)
from utils.logger import log
from utils.polymarket_api import PolymarketAPI
from utils.risk_manager import RiskManager
from strategies.arbitrage import ArbitrageStrategy
from strategies.momentum import MomentumStrategy
from strategies.market_making import MarketMakingStrategy


class PolymarketBot:
    """Main trading bot that orchestrates strategies and risk management."""

    def __init__(self, strategy: str = "combined", dry_run: bool = False,
                 api_class=None):
        self.api = (api_class or PolymarketAPI)()
        self.risk_manager = RiskManager(RISK, INITIAL_BALANCE, MAX_DAILY_LOSS)
        self.dry_run = dry_run
        self.running = False
        self.strategy_name = strategy

        # Initialize strategies
        self.strategies = {}
        if strategy in ("arbitrage", "combined"):
            self.strategies["arbitrage"] = ArbitrageStrategy(self.api, RISK)
        if strategy in ("momentum", "combined"):
            self.strategies["momentum"] = MomentumStrategy(self.api, RISK)
        if strategy in ("market_making", "combined"):
            self.strategies["market_making"] = MarketMakingStrategy(self.api, RISK)

        self.last_scan_time = 0
        self.last_order_check_time = 0
        self.last_portfolio_update_time = 0
        self.daily_reset_date = datetime.now().date()

        log.info(f"Bot initialized | Strategy: {strategy} | "
                 f"Dry run: {dry_run} | Balance: ${INITIAL_BALANCE}")
        log.info(f"Risk profile: {RISK}")

    def scan_markets(self):
        """Scan all active markets for trading opportunities."""
        log.info("Scanning markets for opportunities...")

        # Fetch active, high-volume markets
        markets = self.api.find_high_volume_markets(min_volume=5000, limit=50)
        if not markets:
            log.warning("No high-volume markets found, fetching all active markets")
            markets = self.api.get_markets(limit=30, active=True)

        if not markets:
            log.error("Failed to fetch any markets")
            return

        log.info(f"Analyzing {len(markets)} markets...")

        all_opportunities = []

        for name, strategy in self.strategies.items():
            try:
                opportunities = strategy.scan(markets)
                for opp in opportunities:
                    opp["strategy_name"] = name
                all_opportunities.extend(opportunities)
                log.info(f"  [{name.upper()}] found {len(opportunities)} opportunities")
            except Exception as e:
                log.error(f"  [{name.upper()}] scan error: {e}")

        if not all_opportunities:
            log.info("No trading opportunities found this cycle")
            return

        # Sort by strength/edge
        all_opportunities.sort(
            key=lambda x: x.get("strength", x.get("edge", x.get("spread_pct", 0))),
            reverse=True,
        )

        # Execute top opportunities
        for opp in all_opportunities[:5]:  # Max 5 per cycle
            self.execute_opportunity(opp)

    def execute_opportunity(self, opportunity: dict):
        """Generate and execute orders for an opportunity."""
        strategy_name = opportunity.get("strategy_name", "unknown")
        strategy = self.strategies.get(strategy_name)

        if not strategy:
            return

        balance = self.risk_manager.total_equity
        orders = strategy.generate_orders(opportunity, balance)

        if not orders:
            return

        for order in orders:
            # Resize order to fit current risk limits (with 2% buffer for
            # float rounding and equity fluctuations between checks)
            max_pos = self.risk_manager.total_equity * self.risk_manager.risk["max_position_pct"] * 0.98
            cost = order["size"] * order["price"]
            if cost > max_pos and order["price"] > 0:
                order["size"] = int(max_pos / order["price"])
                if order["size"] < 1:
                    continue

            # On Polymarket you can only buy tokens (YES or NO), not sell
            # without holding inventory. Skip any SELL orders from strategies.
            if order["side"] == "SELL":
                log.debug(f"Skipping SELL order — no inventory to sell on Polymarket")
                continue

            # Risk check
            can_trade, reason = self.risk_manager.can_open_position(
                order["size"], order["price"]
            )
            if not can_trade:
                log.info(f"Skipping order — risk limit: {reason}")
                continue

            if self.dry_run:
                log.info(f"[DRY RUN] Would place: {order['side']} {order['size']} "
                         f"@ ${order['price']:.3f} ({strategy_name})")
                # Simulate order fill for dry run (skip_risk_check=True
                # because we already verified above)
                self.risk_manager.open_position(
                    token_id=order["token_id"],
                    market_name=opportunity.get("market", "Unknown"),
                    side=order["side"],
                    entry_price=order["price"],
                    size=order["size"],
                    strategy=strategy_name,
                    stop_loss=order.get("stop_loss", 0),
                    take_profit=order.get("take_profit", 0),
                    skip_risk_check=True,
                )
            else:
                result = self.api.place_order(order)
                if result:
                    order_id = result.get("orderID", "")
                    self.risk_manager.open_position(
                        token_id=order["token_id"],
                        market_name=opportunity.get("market", "Unknown"),
                        side=order["side"],
                        entry_price=order["price"],
                        size=order["size"],
                        strategy=strategy_name,
                        stop_loss=order.get("stop_loss", 0),
                        take_profit=order.get("take_profit", 0),
                        order_id=order_id,
                        skip_risk_check=True,
                    )
                else:
                    log.error(f"Failed to place order for {opportunity.get('market', '')[:40]}")

    def check_positions(self):
        """Check stop-losses and take-profits for open positions."""
        if not self.risk_manager.portfolio.positions:
            return

        tokens_to_close = self.risk_manager.check_stop_losses(self.api)

        for token_id, trigger_price in tokens_to_close:
            position = self.risk_manager.portfolio.positions.get(token_id)
            if not position:
                continue

            if self.dry_run:
                log.info(f"[DRY RUN] Would close position {token_id[:16]} "
                         f"@ ${trigger_price:.3f}")
                self.risk_manager.close_position(token_id, trigger_price)
            else:
                # Place market order to close
                close_order = {
                    "token_id": token_id,
                    "price": trigger_price,
                    "size": position.size,
                    "side": "SELL" if position.side == "BUY" else "BUY",
                    "type": "FOK",
                }
                result = self.api.place_order(close_order)
                if result:
                    self.risk_manager.close_position(token_id, trigger_price)

    def daily_reset(self):
        """Check if we need to reset daily P&L counters."""
        today = datetime.now().date()
        if today > self.daily_reset_date:
            self.risk_manager.reset_daily_pnl()
            self.daily_reset_date = today

    def run(self):
        """Main trading loop."""
        self.running = True

        # Handle graceful shutdown
        def signal_handler(sig, frame):
            log.info("Shutdown signal received, closing positions...")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        log.info("=" * 60)
        log.info("  POLYMARKET TRADING BOT STARTED")
        log.info(f"  Strategy:  {self.strategy_name}")
        log.info(f"  Mode:      {'DRY RUN' if self.dry_run else 'LIVE'}")
        log.info(f"  Balance:   ${INITIAL_BALANCE}")
        log.info(f"  Risk:      {self.risk_manager.risk}")
        log.info("=" * 60)

        cycle = 0
        while self.running:
            try:
                now = time.time()
                cycle += 1

                # Daily reset check
                self.daily_reset()

                # Scan for opportunities
                if now - self.last_scan_time >= MARKET_SCAN_INTERVAL:
                    self.scan_markets()
                    self.last_scan_time = now

                # Check positions for stop-loss / take-profit
                if now - self.last_order_check_time >= ORDER_CHECK_INTERVAL:
                    self.check_positions()
                    self.last_order_check_time = now

                # Portfolio status update
                if now - self.last_portfolio_update_time >= PORTFOLIO_UPDATE_INTERVAL:
                    self.risk_manager.print_status()
                    self.last_portfolio_update_time = now

                    # Check if target reached (informational)
                    status = self.risk_manager.get_status()
                    if status["total_equity"] >= INITIAL_BALANCE * 14:
                        log.info("TARGET REACHED! Total equity >= 14x initial balance!")

                time.sleep(1)

            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error(f"Error in main loop: {e}")
                time.sleep(5)

        # Shutdown
        log.info("Bot shutting down...")
        if not self.dry_run:
            self.api.cancel_all_orders()
        self.risk_manager.print_status()
        log.info("Bot stopped.")


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument("--strategy", type=str, default=STRATEGY,
                        choices=["arbitrage", "momentum", "market_making", "combined"],
                        help="Trading strategy to use")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run in simulation mode without placing real orders")
    parser.add_argument("--demo", action="store_true",
                        help="Run with mock market data (no API needed)")
    parser.add_argument("--balance", type=float, default=INITIAL_BALANCE,
                        help="Starting balance in USDC")
    args = parser.parse_args()

    if args.balance != INITIAL_BALANCE:
        import config.settings as settings
        settings.INITIAL_BALANCE = args.balance

    api_class = None
    if args.demo:
        args.dry_run = True
        from utils.mock_api import MockPolymarketAPI
        api_class = MockPolymarketAPI
        log.info("DEMO MODE: Using mock market data")

    bot = PolymarketBot(strategy=args.strategy, dry_run=args.dry_run,
                        api_class=api_class)
    bot.run()


if __name__ == "__main__":
    main()
