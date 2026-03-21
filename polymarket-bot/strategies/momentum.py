"""
Momentum / Trend-Following Strategy for Polymarket.

Identifies markets where prices are moving strongly in one direction,
indicating new information or sentiment shifts. Rides the trend
with tight stop-losses.

Key signals:
- Price velocity (rate of change over recent trades)
- Volume surge detection
- Breakout from consolidation ranges
"""

import time
from typing import Optional
from utils.logger import log


class MomentumStrategy:
    """Identifies and trades momentum opportunities."""

    def __init__(self, api, risk_config: dict):
        self.api = api
        self.risk = risk_config
        self.min_edge = risk_config["min_edge"]
        self.lookback_trades = 20
        self.volume_surge_multiplier = 2.5
        self.min_price_velocity = 0.02  # 2% move minimum

    def analyze_momentum(self, token_id: str,
                          market_info: dict) -> Optional[dict]:
        """
        Analyze price momentum for a single token.

        Returns a signal dict if momentum is strong enough.
        """
        trades = self.api.get_trades(token_id, limit=self.lookback_trades)
        if len(trades) < 5:
            return None

        # Sort trades by timestamp descending (newest first)
        for trade in trades:
            ts = trade.get("timestamp", 0)
            if isinstance(ts, str):
                trade["_ts"] = int(ts) if ts.isdigit() else 0
            else:
                trade["_ts"] = int(ts)
        trades.sort(key=lambda t: t["_ts"], reverse=True)

        prices = []
        volumes = []
        timestamps = []

        for trade in trades:
            prices.append(float(trade.get("price", 0)))
            volumes.append(float(trade.get("size", 0)))
            timestamps.append(trade["_ts"])

        if not prices or max(prices) == 0:
            return None

        current_price = prices[0]  # Most recent trade
        oldest_price = prices[-1]

        # Price velocity
        price_change = current_price - oldest_price
        price_velocity = price_change / oldest_price if oldest_price > 0 else 0

        # Volume analysis
        recent_volume = sum(volumes[:5]) if len(volumes) >= 5 else sum(volumes)
        older_volume = sum(volumes[5:]) if len(volumes) > 5 else recent_volume
        avg_older = older_volume / max(len(volumes) - 5, 1)
        avg_recent = recent_volume / min(5, len(volumes))
        volume_ratio = avg_recent / avg_older if avg_older > 0 else 1.0

        # Trend consistency — how many recent trades moved in same direction
        up_moves = sum(1 for i in range(len(prices) - 1) if prices[i] > prices[i + 1])
        down_moves = sum(1 for i in range(len(prices) - 1) if prices[i] < prices[i + 1])
        total_moves = up_moves + down_moves
        trend_strength = max(up_moves, down_moves) / total_moves if total_moves > 0 else 0

        # Check if momentum is strong enough
        abs_velocity = abs(price_velocity)
        if abs_velocity < self.min_price_velocity:
            return None

        # Strength score (0-1)
        velocity_score = min(abs_velocity / 0.10, 1.0)
        volume_score = min(volume_ratio / self.volume_surge_multiplier, 1.0)
        trend_score = trend_strength

        strength = (velocity_score * 0.4 + volume_score * 0.3 + trend_score * 0.3)

        if strength < 0.4:
            return None

        direction = "UP" if price_velocity > 0 else "DOWN"

        # Find the complementary token for binary markets
        complement_token_id = ""
        complement_price = 0.0
        tokens = market_info.get("tokens", [])
        if len(tokens) == 2:
            for token in tokens:
                if token.get("token_id", "") != token_id:
                    complement_token_id = token.get("token_id", "")
                    complement_price = float(token.get("price", 0))
                    break

        signal = {
            "type": "momentum",
            "token_id": token_id,
            "complement_token_id": complement_token_id,
            "complement_price": complement_price,
            "market": market_info.get("question", "Unknown"),
            "condition_id": market_info.get("condition_id", ""),
            "current_price": current_price,
            "price_velocity": price_velocity,
            "volume_ratio": volume_ratio,
            "trend_strength": trend_strength,
            "direction": direction,
            "strength": strength,
            "velocity_score": velocity_score,
            "volume_score": volume_score,
            "trend_score": trend_score,
        }

        log.info(f"[MOM] {direction} signal on '{market_info.get('question', '')[:50]}': "
                 f"velocity={price_velocity:.3f}, vol_ratio={volume_ratio:.1f}, "
                 f"strength={strength:.2f}")

        return signal

    def scan(self, markets: list[dict]) -> list[dict]:
        """Scan markets for momentum signals."""
        signals = []

        for market in markets:
            tokens = market.get("tokens", [])
            for token in tokens:
                token_id = token.get("token_id", "")
                price = float(token.get("price", 0))

                # Skip tokens near extremes (little room to move)
                if price < 0.05 or price > 0.95:
                    continue

                signal = self.analyze_momentum(token_id, market)
                if signal:
                    signals.append(signal)

        return sorted(signals, key=lambda x: x["strength"], reverse=True)

    def generate_orders(self, signal: dict,
                        available_balance: float) -> list[dict]:
        """Generate orders based on a momentum signal."""
        orders = []
        max_position = available_balance * self.risk["max_position_pct"]

        price = signal["current_price"]
        direction = signal["direction"]

        # Position sizing based on Kelly criterion
        win_rate = 0.5 + (signal["strength"] * 0.15)  # Estimated win rate
        avg_win = signal["strength"] * self.risk["take_profit"]
        avg_loss = self.risk["stop_loss"]

        kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win if avg_win > 0 else 0
        kelly = max(0, min(kelly, 1.0))
        kelly_adjusted = kelly * self.risk["kelly_fraction"]

        position_size = min(max_position, available_balance * kelly_adjusted)
        num_shares = int(position_size / price) if price > 0 else 0

        if num_shares < 1:
            return []

        if direction == "UP":
            # Buy the token — ride the upward momentum
            orders.append({
                "token_id": signal["token_id"],
                "price": min(price * 1.005, 0.99),  # Slight premium for fill
                "size": num_shares,
                "side": "BUY",
                "type": "GTC",
                "strategy": "momentum",
                "stop_loss": max(0.01, price * (1 - self.risk["stop_loss"])),
                "take_profit": min(0.99, price * (1 + self.risk["take_profit"])),
            })
        else:
            # Price going down — buy the complementary token (e.g., NO if YES drops)
            # On Polymarket you can't sell tokens you don't hold
            complement_id = signal.get("complement_token_id", "")
            complement_price = signal.get("complement_price", 0)
            if not complement_id or complement_price <= 0:
                return []

            comp_shares = int(position_size / complement_price) if complement_price > 0 else 0
            if comp_shares < 1:
                return []

            orders.append({
                "token_id": complement_id,
                "price": min(complement_price * 1.005, 0.99),
                "size": comp_shares,
                "side": "BUY",
                "type": "GTC",
                "strategy": "momentum",
                "stop_loss": max(0.01, complement_price * (1 - self.risk["stop_loss"])),
                "take_profit": min(0.99, complement_price * (1 + self.risk["take_profit"])),
            })

        return orders
