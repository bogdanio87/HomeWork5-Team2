"""
Polymarket API client for interacting with CLOB and Gamma APIs.
Handles market data retrieval, order placement, and position management.

Uses py-clob-client for authenticated operations (order placement)
and direct HTTP for public endpoints (market data, order books).
"""

import os
import time
import sys
import requests

# Shim: py-clob-client expects 'eip712_structs' but some installs only have
# 'poly_eip712_structs'.  Map the latter into sys.modules so the import works.
if "eip712_structs" not in sys.modules:
    try:
        import poly_eip712_structs as _eip712
        sys.modules["eip712_structs"] = _eip712
    except ImportError:
        pass
from typing import Optional
from config.settings import (
    CLOB_API_URL, GAMMA_API_URL,
    POLY_API_KEY, POLY_API_SECRET, POLY_PASSPHRASE, PRIVATE_KEY,
    PROXY_URL,
)
from utils.logger import log

# Polygon mainnet chain ID
POLYGON_CHAIN_ID = 137


class PolymarketAPI:
    """Client for Polymarket CLOB API and Gamma API."""

    def __init__(self):
        self.clob_url = CLOB_API_URL
        self.gamma_url = GAMMA_API_URL
        self.session = requests.Session()
        if PROXY_URL:
            # Clear any system/environment proxy settings so our proxy takes effect
            for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                        "no_proxy", "NO_PROXY"):
                os.environ.pop(var, None)
            self.session.proxies = {
                "http": PROXY_URL,
                "https": PROXY_URL,
            }
            self.session.trust_env = False  # Ignore env proxy vars
            log.info(f"Using proxy: {PROXY_URL.split('@')[-1] if '@' in PROXY_URL else PROXY_URL}")
        self.session.headers.update({
            "Content-Type": "application/json",
        })
        self._rate_limit_delay = 0.05  # 50ms between requests
        self._proxy_disabled = False

        # py-clob-client uses httpx internally, which can have compatibility
        # issues with some environments. Monkey-patch its HTTP helpers to use
        # our requests.Session instead (also needed for proxy support).
        if True:
            try:
                import py_clob_client.http_helpers.helpers as _helpers
                _session = self.session  # capture for closure

                _api = self  # capture for closure

                def _patched_request(endpoint, method, headers=None, data=None):
                    """Route py-clob-client HTTP calls through our proxied session."""
                    if headers is None:
                        headers = {}
                    # Only set defaults — do NOT overwrite auth headers
                    headers.setdefault("User-Agent", "py_clob_client")
                    headers.setdefault("Accept", "*/*")
                    headers.setdefault("Connection", "keep-alive")
                    headers.setdefault("Content-Type", "application/json")
                    try:
                        if isinstance(data, str):
                            resp = _session.request(
                                method, endpoint, headers=headers,
                                data=data.encode("utf-8"), timeout=30,
                            )
                        else:
                            resp = _session.request(
                                method, endpoint, headers=headers,
                                json=data, timeout=30,
                            )
                    except requests.exceptions.ProxyError:
                        _api._disable_proxy()
                        if isinstance(data, str):
                            resp = _session.request(
                                method, endpoint, headers=headers,
                                data=data.encode("utf-8"), timeout=30,
                            )
                        else:
                            resp = _session.request(
                                method, endpoint, headers=headers,
                                json=data, timeout=30,
                            )
                    if resp.status_code == 407 and not _api._proxy_disabled:
                        _api._disable_proxy()
                        return _patched_request(endpoint, method, headers, data)
                    if resp.status_code != 200:
                        from py_clob_client.exceptions import PolyApiException
                        raise PolyApiException(resp)
                    try:
                        return resp.json()
                    except ValueError:
                        return resp.text

                _helpers.request = _patched_request
                _helpers.post = lambda ep, headers=None, data=None: _patched_request(ep, "POST", headers, data)
                _helpers.get = lambda ep, headers=None, data=None: _patched_request(ep, "GET", headers, data)
                _helpers.delete = lambda ep, headers=None, data=None: _patched_request(ep, "DELETE", headers, data)
                _helpers.put = lambda ep, headers=None, data=None: _patched_request(ep, "PUT", headers, data)
                log.info("Patched py-clob-client to use requests.Session")
            except Exception as e:
                log.warning(f"Could not patch py-clob-client HTTP helpers: {e}")

        # Initialize py-clob-client for authenticated order operations
        self._clob_client = None
        if PRIVATE_KEY and not PRIVATE_KEY.startswith("your_"):
            try:
                from py_clob_client.client import ClobClient
                from py_clob_client.clob_types import ApiCreds

                # Convert mnemonic phrase to hex private key if needed
                hex_key = PRIVATE_KEY
                if " " in PRIVATE_KEY:
                    from eth_account import Account
                    Account.enable_unaudited_hdwallet_features()
                    acct = Account.from_mnemonic(PRIVATE_KEY)
                    hex_key = acct.key.hex()
                    log.info(f"Wallet address: {acct.address}")

                self._clob_client = ClobClient(
                    CLOB_API_URL,
                    chain_id=POLYGON_CHAIN_ID,
                    key=hex_key,
                )

                # If we have API creds, set them; otherwise derive them
                if (POLY_API_KEY and not POLY_API_KEY.startswith("your_")
                        and POLY_API_SECRET and POLY_PASSPHRASE):
                    creds = ApiCreds(
                        api_key=POLY_API_KEY,
                        api_secret=POLY_API_SECRET,
                        api_passphrase=POLY_PASSPHRASE,
                    )
                    self._clob_client.set_api_creds(creds)
                else:
                    try:
                        creds = self._clob_client.create_or_derive_api_creds()
                        self._clob_client.set_api_creds(creds)
                    except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
                        log.warning(f"Proxy error during API cred derivation: {e}")
                        self._disable_proxy()
                        # Retry without proxy
                        creds = self._clob_client.create_or_derive_api_creds()
                        self._clob_client.set_api_creds(creds)

                log.info("Authenticated CLOB client initialized (py-clob-client)")
            except Exception as e:
                log.error(f"Could not initialize CLOB client: {e}", exc_info=True)
                self._clob_client = None
        else:
            log.warning("No valid PRIVATE_KEY — orders will fail. "
                        "Set PRIVATE_KEY in .env to enable trading.")

    def _disable_proxy(self):
        """Disable proxy and switch to direct connection."""
        if self.session.proxies:
            log.warning("Disabling proxy — switching to direct connection")
            self.session.proxies = {}
            self.session.trust_env = False
            self._proxy_disabled = True

    def _request(self, method: str, url: str, **kwargs) -> Optional[dict]:
        """Make a rate-limited API request with retry logic."""
        for attempt in range(3):
            try:
                time.sleep(self._rate_limit_delay)
                resp = self.session.request(method, url, timeout=15, **kwargs)
                if resp.status_code == 407:
                    log.error("Proxy returned 407 Auth Required — credentials may be expired")
                    self._disable_proxy()
                    continue
                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    log.warning(f"Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.ProxyError as e:
                log.error(f"Proxy error (attempt {attempt + 1}): {e}")
                self._disable_proxy()
                continue
            except requests.exceptions.RequestException as e:
                log.error(f"API request failed (attempt {attempt + 1}): {e}")
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None

    # ── Market Data ──────────────────────────────────────────

    def get_markets(self, limit: int = 100, active: bool = True,
                    closed: bool = False, order: str = "-volume") -> list[dict]:
        """Fetch markets from Gamma API with filtering."""
        params = {
            "limit": limit,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
        }
        data = self._request("GET", f"{self.gamma_url}/markets", params=params)
        if not isinstance(data, list):
            return []

        # Gamma API returns prices in outcomePrices/clobTokenIds fields,
        # not in a nested 'tokens' list. Normalize into the format
        # that strategies expect: tokens=[{outcome, price, token_id}, ...]
        for market in data:
            tokens = market.get("tokens")
            if tokens:
                continue  # already has token data

            outcomes = market.get("outcomes") or []
            raw_prices = market.get("outcomePrices") or []
            raw_ids = market.get("clobTokenIds") or []

            # Parse JSON strings if needed (API returns these as strings)
            import json as _json
            if isinstance(outcomes, str):
                try:
                    outcomes = _json.loads(outcomes)
                except (ValueError, TypeError):
                    outcomes = []
            if isinstance(raw_prices, str):
                try:
                    raw_prices = _json.loads(raw_prices)
                except (ValueError, TypeError):
                    raw_prices = []
            if isinstance(raw_ids, str):
                try:
                    raw_ids = _json.loads(raw_ids)
                except (ValueError, TypeError):
                    raw_ids = []

            built_tokens = []
            for i, outcome in enumerate(outcomes):
                price = float(raw_prices[i]) if i < len(raw_prices) else 0
                token_id = raw_ids[i] if i < len(raw_ids) else ""
                built_tokens.append({
                    "outcome": outcome,
                    "price": price,
                    "token_id": token_id,
                })
            market["tokens"] = built_tokens

        return data

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
        """Place an order on Polymarket CLOB using py-clob-client.

        Order dict should contain:
        - token_id: str
        - price: float (0-1)
        - size: float
        - side: 'BUY' or 'SELL'
        - type: 'GTC' (Good Till Cancelled) or 'FOK' (Fill or Kill)
        """
        log.info(f"Placing order: {order['side']} {order['size']} @ {order['price']:.4f} "
                 f"for token {order['token_id'][:16]}...")

        if not self._clob_client:
            log.error("Cannot place order — no authenticated CLOB client. "
                      "Set PRIVATE_KEY in .env")
            return None

        try:
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY, SELL

            side = BUY if order["side"].upper() == "BUY" else SELL

            # Round price to valid tick size (Polymarket uses 0.001 ticks)
            price = round(order["price"], 3)
            # Ensure price is within valid range
            price = max(0.001, min(0.999, price))

            order_args = OrderArgs(
                token_id=order["token_id"],
                price=price,
                size=float(order["size"]),
                side=side,
            )

            result = self._clob_client.create_and_post_order(order_args)
            if result:
                log.info(f"Order placed: {result}")
            return result
        except Exception as e:
            log.error(f"Failed to place order: {e}")
            # Log full traceback for debugging connection issues
            import traceback
            log.debug(traceback.format_exc())
            return None

    def cancel_order(self, order_id: str) -> Optional[dict]:
        """Cancel an existing order."""
        log.info(f"Cancelling order {order_id}")
        if self._clob_client:
            try:
                return self._clob_client.cancel_orders([order_id])
            except Exception as e:
                log.error(f"Failed to cancel order: {e}")
                return None
        return self._request("DELETE", f"{self.clob_url}/order/{order_id}")

    def cancel_all_orders(self) -> Optional[dict]:
        """Cancel all open orders."""
        log.info("Cancelling all open orders")
        if self._clob_client:
            try:
                return self._clob_client.cancel_orders()
            except Exception as e:
                log.error(f"Failed to cancel all orders: {e}")
                return None
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
        markets = self.get_markets(limit=limit, order="-volume")
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
