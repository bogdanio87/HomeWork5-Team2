"""
Arbitrage Strategy for Polymarket.

Exploits pricing inefficiencies between YES/NO token pairs.
In a binary market, YES + NO should always equal $1.00.
When YES + NO < 1.00, there's an arbitrage opportunity (buy both).
When YES + NO > 1.00, there may be a mispricing to exploit.

Also detects cross-market arbitrage on correlated events.
"""

from typing import Optional
from utils.logger import log


class ArbitrageStrategy:
    """Finds and exploits arbitrage opportunities on Polymarket."""

    def __init__(self, api, risk_config: dict):
        self.api = api
        self.risk = risk_config
        self.min_edge = risk_config["min_edge"]

    def scan_binary_arbitrage(self, markets: list[dict]) -> list[dict]:
        """
        Scan binary markets for YES+NO pricing that doesn't sum to 1.0.

        If YES price + NO price < 1.0, buying both guarantees profit.
        If YES price + NO price > 1.0, one side is overpriced.
        """
        opportunities = []

        for market in markets:
            tokens = market.get("tokens", [])
            if len(tokens) != 2:
                continue

            yes_token = None
            no_token = None
            for token in tokens:
                outcome = token.get("outcome", "").upper()
                if outcome == "YES":
                    yes_token = token
                elif outcome == "NO":
                    no_token = token

            if not yes_token or not no_token:
                continue

            yes_price = float(yes_token.get("price", 0))
            no_price = float(no_token.get("price", 0))

            if yes_price <= 0 or no_price <= 0:
                continue

            total = yes_price + no_price
            edge = abs(1.0 - total)

            if edge >= self.min_edge:
                opp = {
                    "type": "binary_arbitrage",
                    "market": market.get("question", "Unknown"),
                    "condition_id": market.get("condition_id", ""),
                    "yes_token_id": yes_token.get("token_id", ""),
                    "no_token_id": no_token.get("token_id", ""),
                    "yes_price": yes_price,
                    "no_price": no_price,
                    "total": total,
                    "edge": edge,
                    "direction": "under" if total < 1.0 else "over",
                }

                if total < 1.0:
                    # Buy both YES and NO — guaranteed profit on resolution
                    opp["action"] = "buy_both"
                    opp["expected_profit_pct"] = (1.0 - total) / total
                else:
                    # One side is overpriced — sell the overpriced one
                    opp["action"] = "sell_overpriced"
                    if yes_price > (1.0 - no_price + self.min_edge):
                        opp["sell_side"] = "YES"
                        opp["fair_price"] = 1.0 - no_price
                    else:
                        opp["sell_side"] = "NO"
                        opp["fair_price"] = 1.0 - yes_price

                log.info(f"[ARB] Found {opp['direction']} opportunity: "
                         f"YES={yes_price:.3f} NO={no_price:.3f} "
                         f"Total={total:.3f} Edge={edge:.3f}")
                opportunities.append(opp)

        return sorted(opportunities, key=lambda x: x["edge"], reverse=True)

    def scan_multioutcome_arbitrage(self, markets: list[dict]) -> list[dict]:
        """
        Scan multi-outcome markets where probabilities should sum to 1.0.

        For markets with multiple outcomes (e.g., "Who will win?"),
        all outcome prices should sum to 1.0.
        """
        opportunities = []

        for market in markets:
            tokens = market.get("tokens", [])
            if len(tokens) <= 2:
                continue

            prices = []
            for token in tokens:
                price = float(token.get("price", 0))
                if price > 0:
                    prices.append(price)

            if len(prices) < 2:
                continue

            total = sum(prices)
            edge = abs(1.0 - total)

            if edge >= self.min_edge:
                opp = {
                    "type": "multi_arbitrage",
                    "market": market.get("question", "Unknown"),
                    "condition_id": market.get("condition_id", ""),
                    "tokens": tokens,
                    "total": total,
                    "edge": edge,
                    "direction": "under" if total < 1.0 else "over",
                    "num_outcomes": len(tokens),
                }

                if total < 1.0:
                    opp["action"] = "buy_all"
                    opp["expected_profit_pct"] = (1.0 - total) / total
                else:
                    opp["action"] = "sell_overpriced"
                    max_token = max(tokens, key=lambda t: float(t.get("price", 0)))
                    opp["sell_token_id"] = max_token.get("token_id", "")
                    opp["sell_price"] = float(max_token.get("price", 0))

                log.info(f"[ARB-MULTI] {opp['direction']}: {len(tokens)} outcomes, "
                         f"total={total:.3f}, edge={edge:.3f}")
                opportunities.append(opp)

        return sorted(opportunities, key=lambda x: x["edge"], reverse=True)

    def generate_orders(self, opportunity: dict,
                        available_balance: float) -> list[dict]:
        """Generate orders for an arbitrage opportunity."""
        orders = []
        max_position = available_balance * self.risk["max_position_pct"]

        if opportunity["type"] == "binary_arbitrage" and opportunity["action"] == "buy_both":
            cost_per_pair = opportunity["yes_price"] + opportunity["no_price"]
            num_pairs = int(max_position / cost_per_pair)
            if num_pairs < 1:
                return []

            orders.append({
                "token_id": opportunity["yes_token_id"],
                "price": opportunity["yes_price"],
                "size": num_pairs,
                "side": "BUY",
                "type": "GTC",
                "strategy": "arbitrage",
            })
            orders.append({
                "token_id": opportunity["no_token_id"],
                "price": opportunity["no_price"],
                "size": num_pairs,
                "side": "BUY",
                "type": "GTC",
                "strategy": "arbitrage",
            })

        elif opportunity.get("action") == "sell_overpriced":
            sell_token = opportunity.get("sell_token_id") or (
                opportunity["yes_token_id"]
                if opportunity.get("sell_side") == "YES"
                else opportunity.get("no_token_id", "")
            )
            # Use the correct price for the side being sold
            if opportunity.get("sell_price"):
                sell_price = opportunity["sell_price"]
            elif opportunity.get("sell_side") == "YES":
                sell_price = opportunity.get("yes_price", 0)
            else:
                sell_price = opportunity.get("no_price", 0)
            size = int(max_position / sell_price) if sell_price > 0 else 0

            if size >= 1 and sell_token:
                orders.append({
                    "token_id": sell_token,
                    "price": sell_price,
                    "size": size,
                    "side": "SELL",
                    "type": "GTC",
                    "strategy": "arbitrage",
                })

        return orders

    def scan(self, markets: list[dict]) -> list[dict]:
        """Run all arbitrage scans and return opportunities."""
        opps = []
        opps.extend(self.scan_binary_arbitrage(markets))
        opps.extend(self.scan_multioutcome_arbitrage(markets))
        return opps
