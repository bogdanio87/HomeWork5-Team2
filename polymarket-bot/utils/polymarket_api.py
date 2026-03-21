"""
Polymarket API client for interacting with CLOB and Gamma APIs.
Handles market data retrieval, order placement, and position management.
"""

import time
import requests
from typing import Optional
from config.settings import CLOB_API_URL, GAMMA_API_URL, POLY_API_KEY, POLY_API_SECRET, POLY_PASSPHRASE, PROXY_URL
from utils.logger import log


class PolymarketAPI:
    """Client for Polymarket CLOB API and Gamma API."""

    def __init__(self):
        self.clob_url = CLOB_API_URL
        self.gamma_url = GAMMA_API_URL
        self.session = requests.Session()
        if PROXY_URL:
            self.session.proxies = {
                "http": PROXY_URL,
                "https": PROXY_URL,
            }
            log.info(f"Using proxy: {PROXY_URL.split('@')[-1] if '@' in PROXY_URL else PROXY_URL}")
        self.session.headers.update({
            "Content-Type": "application/json",
        })
        if POLY_API_KEY:
            self.session.headers.update({
                "POLY_API_KEY": POLY_API_KEY,
                "POLY_API_SECRET": POLY_API_SECRET,
                "POLY_PASSPHRASE": POLY_PASSPHRASE,
            })
        self._rate_limit_delay = 0.2  # 200ms between requests

    def _request(self, method: str, url: str, **kwargs) -> Optional[dict]:
        """Make a rate-limited API request with retry logic."""
        for attempt in range(3):
            try:
                time.sleep(self._rate_limit_delay)
                resp = self.session.request(method, url, timeout=15, **kwargs)
                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    log.warning(f"Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                log.error(f"API request failed (attempt {attempt + 1}): {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None

    # ── Market Data ──────────────────────────────────────────

    def get_markets(self, limit: int = 100, active: bool = True,
                    closed: bool = False, order: str = "volume") -> list[dict]:
        """Fetch markets from Gamma API with filtering."""
        params = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
        }
        data = self._request("GET", f"{self.gamma_url}/markets", params=params)
        return data if isinstance(data, list) else []

    def get_market(self, condition_id: str) -> Optional[dict]:
        """Fetch a single market by condition ID."""
        return self._request("GET", f"{self.gamma_url}/markets/{condition_id}")

    def get_order_book(self, token_id: str) -> Optional[dict]:
        """Fetch the order book for a specific token."""
        params = {"token_id": token_id}
        return self._request("GET", f"{self.clob_url}/book", params=params)

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get the midpoint price for a token."""
        data = self._request("GET", f"{self.clob_url}/midpoint",
                             params={"token_id": token_id})
        if data and "mid" in data:
            return float(data["mid"])
        return None

    def get_price(self, token_id: str, side: str = "buy") -> Optional[float]:
        """Get the best price for a token on a given side."""
        data = self._request("GET", f"{self.clob_url}/price",
                             params={"token_id": token_id, "side": side})
        if data and "price" in data:
            return float(data["price"])
        return None

    def get_spread(self, token_id: str) -> Optional[dict]:
        """Get the spread for a token."""
        return self._request("GET", f"{self.clob_url}/spread",
                             params={"token_id": token_id})

    def get_last_trade_price(self, token_id: str) -> Optional[float]:
        """Get the last trade price for a token."""
        data = self._request("GET", f"{self.clob_url}/last-trade-price",
                             params={"token_id": token_id})
        if data and "price" in data:
            return float(data["price"])
        return None

    # ── Trading ──────────────────────────────────────────────

    def get_trades(self, token_id: str, limit: int = 50) -> list[dict]:
        """Fetch recent trades for a token."""
        params = {"asset_id": token_id, "limit": limit}
        data = self._request("GET", f"{self.clob_url}/trades", params=params)
        return data if isinstance(data, list) else []

    def place_order(self, order: dict) -> Optional[dict]:
        """Place an order on Polymarket CLOB.

        Order dict should contain:
        - token_id: str
        - price: float (0-1)
        - size: float
        - side: 'BUY' or 'SELL'
        - type: 'GTC' (Good Till Cancelled) or 'FOK' (Fill or Kill)
        """
        log.info(f"Placing order: {order['side']} {order['size']} @ {order['price']} "
                 f"for token {order['token_id'][:16]}...")
        return self._request("POST", f"{self.clob_url}/order", json=order)

    def cancel_order(self, order_id: str) -> Optional[dict]:
        """Cancel an existing order."""
        log.info(f"Cancelling order {order_id}")
        return self._request("DELETE", f"{self.clob_url}/order/{order_id}")

    def cancel_all_orders(self) -> Optional[dict]:
        """Cancel all open orders."""
        log.info("Cancelling all open orders")
        return self._request("DELETE", f"{self.clob_url}/orders")

    def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        data = self._request("GET", f"{self.clob_url}/orders")
        return data if isinstance(data, list) else []

    # ── Portfolio ─────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        """Get current positions."""
        data = self._request("GET", f"{self.clob_url}/positions")
        return data if isinstance(data, list) else []

    def get_balance(self) -> Optional[float]:
        """Get USDC balance."""
        data = self._request("GET", f"{self.clob_url}/balance")
        if data and "balance" in data:
            return float(data["balance"])
        return None

    # ── Market Analysis Helpers ───────────────────────────────

    def get_market_with_book(self, token_id: str) -> dict:
        """Get combined market data with order book for analysis."""
        midpoint = self.get_midpoint(token_id)
        spread = self.get_spread(token_id)
        book = self.get_order_book(token_id)
        last_price = self.get_last_trade_price(token_id)

        return {
            "token_id": token_id,
            "midpoint": midpoint,
            "spread": spread,
            "order_book": book,
            "last_price": last_price,
        }

    def find_high_volume_markets(self, min_volume: float = 10000,
                                  limit: int = 50) -> list[dict]:
        """Find active markets with high trading volume."""
        markets = self.get_markets(limit=limit, order="volume")
        return [m for m in markets
                if float(m.get("volume", 0)) >= min_volume
                and m.get("active", False)]

    def find_volatile_markets(self, limit: int = 50,
                               price_range: tuple = (0.15, 0.85)) -> list[dict]:
        """Find markets with prices in a volatile range (not near 0 or 1)."""
        markets = self.get_markets(limit=limit)
        volatile = []
        for market in markets:
            tokens = market.get("tokens", [])
            for token in tokens:
                price = float(token.get("price", 0))
                if price_range[0] <= price <= price_range[1]:
                    volatile.append(market)
                    break
        return volatile
