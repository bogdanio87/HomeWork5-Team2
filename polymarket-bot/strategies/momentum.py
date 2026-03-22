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
        self.min_price_velocity = 0.005  # 0.5% move minimum

    def analyze_momentum(self, token_id: str,
                          market_info: dict) -> Optional[dict]:
        """
        Analyze price momentum for a single token.

        Uses midpoint and last-trade-price (public endpoints) to detect
        price deviation that indicates momentum.
        """
        # Use public endpoints instead of /trades (requires auth)
        midpoint = self.api.get_midpoint(token_id)
        last_price = self.api.get_last_trade_price(token_id)
        spread_data = self.api.get_spread(token_id)

        if midpoint is None or last_price is None:
            return None

        if midpoint <= 0 or last_price <= 0:
            return None

        # Use token's listed price as the "reference" price
        listed_price = float(market_info.get("tokens", [{}])[0].get("price", 0))
        for token in market_info.get("tokens", []):
            if token.get("token_id", "") == token_id:
                listed_price = float(token.get("price", 0))
                break

        if listed_price <= 0:
            listed_price = midpoint

        # Price velocity: difference between last trade and listed/midpoint price
        price_velocity = (last_price - listed_price) / listed_price if listed_price > 0 else 0

        # Also check midpoint vs listed price for broader momentum
        mid_velocity = (midpoint - listed_price) / listed_price if listed_price > 0 else 0

        # Use the stronger signal
        if abs(mid_velocity) > abs(price_velocity):
            price_velocity = mid_velocity

        abs_velocity = abs(price_velocity)
        if abs_velocity < self.min_price_velocity:
            return None

        # Spread as a proxy for volume/activity
        spread = float(spread_data.get("spread", 0.1)) if spread_data else 0.1
        # Tighter spread = more active market = higher volume score
        volume_ratio = max(0.01 / spread, 0.5) if spread > 0 else 1.0

        # Trend score based on direction consistency
        trend_strength = 0.6 if abs_velocity > self.min_price_velocity * 2 else 0.4

        # Strength score (0-1)
        velocity_score = min(abs_velocity / 0.10, 1.0)
        volume_score = min(volume_ratio / self.volume_surge_multiplier, 1.0)
        trend_score = trend_strength

        strength = (velocity_score * 0.4 + volume_score * 0.3 + trend_score * 0.3)

        if strength < 0.15:
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
            "current_price": midpoint,
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
                 f"velocity={price_velocity:.3f}, strength={strength:.2f}")

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
