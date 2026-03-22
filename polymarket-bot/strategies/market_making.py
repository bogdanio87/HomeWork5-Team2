"""
Market Making Strategy for Polymarket.

Places limit orders on both sides of the spread to capture the bid-ask spread.
Profits come from the difference between buy and sell prices.

Key features:
- Dynamic spread calculation based on volatility
- Inventory management to avoid accumulating too much of one side
- Quick order cancellation when market moves against us
"""

from typing import Optional
from utils.logger import log


class MarketMakingStrategy:
    """Provides liquidity and captures bid-ask spread."""

    def __init__(self, api, risk_config: dict):
        self.api = api
        self.risk = risk_config
        self.min_spread = 0.008  # Minimum 0.8% spread to be profitable
        self.max_inventory_imbalance = 0.7  # Max 70% of position on one side
        self.order_layers = 3  # Number of price levels to quote

    def analyze_spread(self, token_id: str,
                        market_info: dict) -> Optional[dict]:
        """
        Analyze the current spread for market-making viability.

        Uses the /spread and /midpoint endpoints (public, no auth needed)
        combined with order book data filtered around midpoint.
        """
        # Use public /spread endpoint for reliable spread data
        spread_data = self.api.get_spread(token_id)
        midpoint_val = self.api.get_midpoint(token_id)

        if not spread_data or midpoint_val is None:
            return None

        spread = float(spread_data.get("spread", 0))
        midpoint = midpoint_val

        if midpoint <= 0 or spread <= 0:
            return None

        spread_pct = spread / midpoint if midpoint > 0 else 0

        if spread_pct < self.min_spread:
            return None

        best_bid = midpoint - spread / 2
        best_ask = midpoint + spread / 2

        # Try to get book depth info (order book is public)
        bid_depth = 0
        ask_depth = 0
        depth_imbalance = 0
        book = self.api.get_order_book(token_id)
        if book:
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            # Filter to orders near midpoint (within 20% of midpoint)
            range_low = midpoint * 0.8
            range_high = midpoint * 1.2
            near_bids = [b for b in bids if float(b.get("price", 0)) >= range_low]
            near_asks = [a for a in asks if float(a.get("price", 0)) <= range_high]
            bid_depth = sum(float(b.get("size", 0)) for b in near_bids[:5])
            ask_depth = sum(float(a.get("size", 0)) for a in near_asks[:5])
            total_depth = bid_depth + ask_depth
            depth_imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0

        opportunity = {
            "type": "market_making",
            "token_id": token_id,
            "market": market_info.get("question", "Unknown"),
            "condition_id": market_info.get("condition_id", ""),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "midpoint": midpoint,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "depth_imbalance": depth_imbalance,
            "estimated_profit_per_round": spread * 0.5,
        }

        log.info(f"[MM] Spread opportunity: {market_info.get('question', '')[:50]} "
                 f"mid={midpoint:.3f} spread={spread:.3f} ({spread_pct:.1%})")

        return opportunity

    def scan(self, markets: list[dict]) -> list[dict]:
        """Scan markets for market-making opportunities.

        For binary markets only scan the first token (complement is
        derived automatically), cutting API calls in half.
        """
        opportunities = []

        for market in markets:
            tokens = market.get("tokens", [])
            # Build a token_id -> complement mapping for binary markets
            complement_map = {}
            if len(tokens) == 2:
                complement_map[tokens[0].get("token_id", "")] = tokens[1].get("token_id", "")
                complement_map[tokens[1].get("token_id", "")] = tokens[0].get("token_id", "")

            # For binary markets scan only the first token
            candidates = tokens if len(tokens) != 2 else tokens[:1]

            for token in candidates:
                token_id = token.get("token_id", "")
                price = float(token.get("price", 0))

                # Best for tokens in the middle range
                if price < 0.10 or price > 0.90:
                    continue

                opp = self.analyze_spread(token_id, market)
                if opp:
                    opp["complement_token_id"] = complement_map.get(token_id, "")
                    opportunities.append(opp)

        return sorted(opportunities,
                      key=lambda x: x["spread_pct"], reverse=True)

    def generate_orders(self, opportunity: dict,
                        available_balance: float,
                        current_inventory: dict = None) -> list[dict]:
        """
        Generate market-making orders using both sides of a binary market.

        On Polymarket you can't sell tokens you don't hold, so instead of
        placing ask orders on the same token, we place BUY orders on the
        complementary token (e.g., buy NO when we'd normally sell YES).
        """
        orders = []
        max_position = available_balance * self.risk["max_position_pct"]

        midpoint = opportunity["midpoint"]
        half_spread = opportunity["spread"] / 2
        complement_id = opportunity.get("complement_token_id", "")

        # Adjust for depth imbalance
        imbalance = opportunity["depth_imbalance"]
        skew = imbalance * 0.005  # Slight skew towards stronger side

        # Inventory adjustment
        inventory_skew = 0
        if current_inventory:
            inv = current_inventory.get(opportunity["token_id"], 0)
            max_inv = max_position / midpoint
            if max_inv > 0:
                inventory_ratio = inv / max_inv
                inventory_skew = inventory_ratio * 0.01  # Adjust quotes

        per_layer_size = max_position / (self.order_layers * 2 * midpoint)
        per_layer_size = int(per_layer_size)

        if per_layer_size < 1:
            return []

        for i in range(self.order_layers):
            layer_offset = half_spread * (0.3 + 0.3 * i)

            # Bid (buy) orders — below midpoint for this token
            bid_price = midpoint - layer_offset + skew - inventory_skew
            bid_price = max(0.01, round(bid_price, 3))

            orders.append({
                "token_id": opportunity["token_id"],
                "price": bid_price,
                "size": per_layer_size,
                "side": "BUY",
                "type": "GTC",
                "strategy": "market_making",
                "layer": i,
            })

            # Instead of selling this token (requires inventory), buy the
            # complementary token at a discounted price.
            # If YES midpoint is M, complement (NO) fair value ~ 1-M.
            if complement_id:
                comp_fair = 1.0 - midpoint
                comp_bid = comp_fair - layer_offset - skew + inventory_skew
                comp_bid = max(0.01, round(comp_bid, 3))

                orders.append({
                    "token_id": complement_id,
                    "price": comp_bid,
                    "size": per_layer_size,
                    "side": "BUY",
                    "type": "GTC",
                    "strategy": "market_making",
                    "layer": i,
                })

        return orders
