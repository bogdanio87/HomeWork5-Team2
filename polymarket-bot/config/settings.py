import os
from dotenv import load_dotenv

load_dotenv()

# API credentials
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
POLY_API_SECRET = os.getenv("POLY_API_SECRET", "")
POLY_PASSPHRASE = os.getenv("POLY_PASSPHRASE", "")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")

# Polymarket API endpoints
CLOB_API_URL = "https://clob.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Trading parameters
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "1000"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "200"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.10"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.30"))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "150"))

# Strategy selection
STRATEGY = os.getenv("STRATEGY", "combined")

# Risk levels configuration
RISK_PROFILES = {
    "conservative": {
        "max_position_pct": 0.05,
        "max_open_positions": 3,
        "min_edge": 0.08,
        "stop_loss": 0.05,
        "take_profit": 0.15,
        "kelly_fraction": 0.25,
    },
    "moderate": {
        "max_position_pct": 0.10,
        "max_open_positions": 5,
        "min_edge": 0.05,
        "stop_loss": 0.08,
        "take_profit": 0.25,
        "kelly_fraction": 0.50,
    },
    "aggressive": {
        "max_position_pct": 0.20,
        "max_open_positions": 8,
        "min_edge": 0.03,
        "stop_loss": 0.10,
        "take_profit": 0.35,
        "kelly_fraction": 0.75,
    },
}

RISK_LEVEL = os.getenv("RISK_LEVEL", "aggressive")
RISK = RISK_PROFILES[RISK_LEVEL]

# Polling intervals (seconds)
MARKET_SCAN_INTERVAL = 30
ORDER_CHECK_INTERVAL = 10
PORTFOLIO_UPDATE_INTERVAL = 60
