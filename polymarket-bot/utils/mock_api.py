"""
Mock Polymarket API for demo mode.

Provides realistic market data so the bot can demonstrate all strategies
without needing a connection to the real Polymarket API.
"""

import random
import time
from utils.logger import log


# Realistic mock markets based on common Polymarket event types
MOCK_MARKETS = [
    {
        "condition_id": "0xdemo0001",
        "question": "Will Bitcoin exceed $150,000 by June 2026?",
        "active": True,
        "volume": "1250000",
        "tokens": [
            {"token_id": "tok_btc150k_yes", "outcome": "Yes", "price": "0.42"},
            {"token_id": "tok_btc150k_no", "outcome": "No", "price": "0.55"},
        ],
    },
    {
        "condition_id": "0xdemo0002",
        "question": "Will the Fed cut rates in Q2 2026?",
        "active": True,
        "volume": "890000",
        "tokens": [
            {"token_id": "tok_fedcut_yes", "outcome": "Yes", "price": "0.63"},
            {"token_id": "tok_fedcut_no", "outcome": "No", "price": "0.38"},
        ],
    },
    {
        "condition_id": "0xdemo0003",
        "question": "Will Ethereum flip Bitcoin market cap by 2027?",
        "active": True,
        "volume": "430000",
        "tokens": [
            {"token_id": "tok_ethflip_yes", "outcome": "Yes", "price": "0.08"},
            {"token_id": "tok_ethflip_no", "outcome": "No", "price": "0.91"},
        ],
    },
    {
        "condition_id": "0xdemo0004",
        "question": "Will S&P 500 reach 6500 by end of 2026?",
        "active": True,
        "volume": "670000",
        "tokens": [
            {"token_id": "tok_sp6500_yes", "outcome": "Yes", "price": "0.51"},
            {"token_id": "tok_sp6500_no", "outcome": "No", "price": "0.47"},
        ],
    },
    {
        "condition_id": "0xdemo0005",
        "question": "Will Tesla stock be above $400 on April 1, 2026?",
        "active": True,
        "volume": "320000",
        "tokens": [
            {"token_id": "tok_tsla400_yes", "outcome": "Yes", "price": "0.35"},
            {"token_id": "tok_tsla400_no", "outcome": "No", "price": "0.62"},
        ],
    },
    {
        "condition_id": "0xdemo0006",
        "question": "Will Ukraine-Russia ceasefire be reached by July 2026?",
        "active": True,
        "volume": "1100000",
        "tokens": [
            {"token_id": "tok_ceasefire_yes", "outcome": "Yes", "price": "0.28"},
            {"token_id": "tok_ceasefire_no", "outcome": "No", "price": "0.70"},
        ],
    },
    {
        "condition_id": "0xdemo0007",
        "question": "Will Apple announce AR glasses in 2026?",
        "active": True,
        "volume": "540000",
        "tokens": [
            {"token_id": "tok_applear_yes", "outcome": "Yes", "price": "0.22"},
            {"token_id": "tok_applear_no", "outcome": "No", "price": "0.76"},
        ],
    },
    {
        "condition_id": "0xdemo0008",
        "question": "Who will win the 2026 FIFA World Cup?",
        "active": True,
        "volume": "2100000",
        "tokens": [
            {"token_id": "tok_wc_brazil", "outcome": "Brazil", "price": "0.18"},
            {"token_id": "tok_wc_france", "outcome": "France", "price": "0.15"},
            {"token_id": "tok_wc_argentina", "outcome": "Argentina", "price": "0.22"},
            {"token_id": "tok_wc_other", "outcome": "Other", "price": "0.40"},
        ],
    },
]


def _jitter(price, pct=0.02):
    """Add small random price jitter to simulate live market movement."""
    delta = price * random.uniform(-pct, pct)
    return round(max(0.01, min(0.99, price + delta)), 3)


class MockPolymarketAPI:
    """Drop-in replacement for PolymarketAPI that returns mock data."""

    def __init__(self):
        self._rate_limit_delay = 0
        self._trade_counter = 0
        self._price_drift = {}  # token_id -> cumulative drift
        self._start_time = time.time()
        log.info("MockPolymarketAPI initialized with %d demo markets",
                 len(MOCK_MARKETS))

    def _get_drifted_price(self, token_id, base_price):
        """Simulate realistic price drift over time using random walk."""
        now = time.time()
        if token_id not in self._price_drift:
            self._price_drift[token_id] = 0.0

        # Random walk step each call — sized to trigger stop-losses
        step = random.gauss(0, 0.015)
        # Occasional larger moves (news events) — 10% chance
        if random.random() < 0.10:
            step += random.choice([-1, 1]) * random.uniform(0.04, 0.12)

        self._price_drift[token_id] += step

        # Gentle mean-reversion to avoid extreme drift
        self._price_drift[token_id] *= 0.998

        drifted = base_price + self._price_drift[token_id]
        return round(max(0.01, min(0.99, drifted)), 3)

    # ── Market Data ──────────────────────────────────────────

    def get_markets(self, limit=100, active=True, closed=False,
                    order="volume"):
        """Return mock markets."""
        markets = [m for m in MOCK_MARKETS if m.get("active", False)]
        if order == "volume":
            markets.sort(key=lambda m: float(m.get("volume", 0)),
                         reverse=True)
        return markets[:limit]

    def get_market(self, condition_id):
        for m in MOCK_MARKETS:
            if m["condition_id"] == condition_id:
                return m
        return None

    def get_order_book(self, token_id):
        """Generate a realistic order book around the token's price."""
        price = self._find_token_price(token_id)
        if price is None:
            return None
        price = self._get_drifted_price(token_id, price)

        bids = []
        asks = []
        for i in range(5):
            offset = 0.01 * (i + 1) + random.uniform(0, 0.005)
            bids.append({
                "price": str(round(max(0.01, price - offset), 3)),
                "size": str(random.randint(50, 500)),
            })
            asks.append({
                "price": str(round(min(0.99, price + offset), 3)),
                "size": str(random.randint(50, 500)),
            })

        return {"bids": bids, "asks": asks}

    def get_midpoint(self, token_id):
        price = self._find_token_price(token_id)
        if price is None:
            return None
        return self._get_drifted_price(token_id, price)

    def get_price(self, token_id, side="buy"):
        price = self._find_token_price(token_id)
        if price is None:
            return None
        drifted = self._get_drifted_price(token_id, price)
        if side == "buy":
            return round(min(0.99, drifted + 0.005), 3)
        return round(max(0.01, drifted - 0.005), 3)

    def get_spread(self, token_id):
        price = self._find_token_price(token_id)
        if price is None:
            return None
        drifted = self._get_drifted_price(token_id, price)
        spread = round(random.uniform(0.01, 0.04), 3)
        return {"spread": str(spread), "mid": str(drifted)}

    def get_last_trade_price(self, token_id):
        price = self._find_token_price(token_id)
        if price is None:
            return None
        return self._get_drifted_price(token_id, price)

    # ── Trading ──────────────────────────────────────────────

    def get_trades(self, token_id, limit=50):
        """Generate mock trade history with a trend for momentum detection."""
        price = self._find_token_price(token_id)
        if price is None:
            return []

        # Use drifted price as current price for trade history
        current_price = self._get_drifted_price(token_id, price)

        # Create a realistic trade history with a trend
        trades = []
        trend = random.choice([-1, 1]) * random.uniform(0.001, 0.004)
        now = int(time.time())

        for i in range(min(limit, 20)):
            t_price = current_price - trend * i + random.uniform(-0.005, 0.005)
            t_price = round(max(0.01, min(0.99, t_price)), 3)
            trades.append({
                "price": str(t_price),
                "size": str(random.randint(10, 200)),
                "timestamp": str(now - i * 60),
                "side": random.choice(["BUY", "SELL"]),
            })

        return trades

    def place_order(self, order):
        self._trade_counter += 1
        log.info("[MOCK] Order placed: %s %s @ $%.3f (id: mock_%d)",
                 order.get("side"), order.get("size"),
                 order.get("price", 0), self._trade_counter)
        return {"orderID": f"mock_{self._trade_counter}", "status": "MATCHED"}

    def cancel_order(self, order_id):
        log.info("[MOCK] Order cancelled: %s", order_id)
        return {"status": "CANCELLED"}

    def cancel_all_orders(self):
        log.info("[MOCK] All orders cancelled")
        return {"status": "OK"}

    def get_open_orders(self):
        return []

    # ── Portfolio ─────────────────────────────────────────────

    def get_positions(self):
        return []

    def get_balance(self):
        return 1000.0

    # ── Market Analysis Helpers ───────────────────────────────

    def get_market_with_book(self, token_id):
        return {
            "token_id": token_id,
            "midpoint": self.get_midpoint(token_id),
            "spread": self.get_spread(token_id),
            "order_book": self.get_order_book(token_id),
            "last_price": self.get_last_trade_price(token_id),
        }

    def find_high_volume_markets(self, min_volume=10000, limit=50):
        markets = self.get_markets(limit=limit, order="volume")
        return [m for m in markets
                if float(m.get("volume", 0)) >= min_volume
                and m.get("active", False)]

    def find_volatile_markets(self, limit=50, price_range=(0.15, 0.85)):
        markets = self.get_markets(limit=limit)
        volatile = []
        for market in markets:
            for token in market.get("tokens", []):
                price = float(token.get("price", 0))
                if price_range[0] <= price <= price_range[1]:
                    volatile.append(market)
                    break
        return volatile

    # ── Internal helpers ──────────────────────────────────────

    def _find_token_price(self, token_id):
        """Look up base price for a token_id from mock data."""
        for market in MOCK_MARKETS:
            for token in market.get("tokens", []):
                if token["token_id"] == token_id:
                    return float(token["price"])
        return None
