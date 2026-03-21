# Polymarket Trading Bot

Automated trading bot for Polymarket prediction markets that combines multiple strategies to find and execute profitable trades.

## ⚠️ DISCLAIMER

**This bot is for educational purposes only.** Trading on prediction markets involves significant risk. Past performance does not guarantee future results. You can lose your entire investment. Never trade with money you cannot afford to lose.

**Guaranteed returns do not exist.** Any claim of guaranteed profits (e.g., "1000% in 3 days") is unrealistic.

## Architecture

```
polymarket-bot/
├── bot.py                      # Main bot runner
├── config/
│   └── settings.py             # Configuration & risk profiles
├── strategies/
│   ├── arbitrage.py            # Binary & multi-outcome arbitrage
│   ├── momentum.py             # Trend-following with volume analysis
│   └── market_making.py        # Spread capture with layered orders
├── utils/
│   ├── polymarket_api.py       # Polymarket CLOB & Gamma API client
│   ├── risk_manager.py         # Position sizing, stop-loss, portfolio tracking
│   └── logger.py               # Logging setup
├── .env.example                # Environment variables template
└── requirements.txt            # Python dependencies
```

## Strategies

### 1. Arbitrage (`arbitrage`)
- **Binary arbitrage**: Detects when YES + NO token prices don't sum to $1.00
- **Multi-outcome arbitrage**: Finds mispricing across markets with 3+ outcomes
- **Lowest risk**, profits from pricing inefficiencies

### 2. Momentum (`momentum`)
- Tracks price velocity, volume surges, and trend consistency
- Uses Kelly Criterion for position sizing
- Rides trends with stop-losses and take-profits

### 3. Market Making (`market_making`)
- Places layered bid/ask orders to capture the spread
- Dynamic spread calculation based on order book depth
- Inventory management to avoid directional exposure

### 4. Combined (`combined`) — Default
- Runs all three strategies simultaneously
- Ranks opportunities across strategies by strength
- Executes the top 5 opportunities per scan cycle

## Setup

### 1. Install dependencies
```bash
cd polymarket-bot
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your Polymarket API credentials
```

### 3. Get Polymarket API Keys
1. Go to https://polymarket.com
2. Connect your wallet
3. Navigate to Settings → API Keys
4. Generate API Key, Secret, and Passphrase

### 4. Run the bot

```bash
# Dry run (simulation mode — no real orders)
python bot.py --dry-run

# Run with specific strategy
python bot.py --strategy momentum --dry-run

# Live trading (USE WITH CAUTION)
python bot.py --strategy combined

# Custom starting balance
python bot.py --balance 500 --dry-run
```

## Risk Management

The bot includes built-in risk controls:

| Parameter | Conservative | Moderate | Aggressive |
|-----------|-------------|----------|------------|
| Max position size | 5% | 10% | 20% |
| Max open positions | 3 | 5 | 8 |
| Min edge required | 8% | 5% | 3% |
| Stop-loss | 5% | 8% | 10% |
| Take-profit | 15% | 25% | 35% |
| Kelly fraction | 0.25 | 0.50 | 0.75 |

Additional protections:
- **Daily loss limit**: Stops trading when daily losses exceed threshold
- **Max drawdown**: Halts at 25% drawdown from peak equity
- **Position size limits**: No single trade exceeds configured max %

## Configuration

All settings can be configured via `.env` file or environment variables:

```env
STRATEGY=combined          # arbitrage, momentum, market_making, combined
RISK_LEVEL=aggressive      # conservative, moderate, aggressive
INITIAL_BALANCE=1000       # Starting balance in USDC
MAX_POSITION_SIZE=200      # Maximum single position size
STOP_LOSS_PCT=0.10         # Default stop-loss percentage
TAKE_PROFIT_PCT=0.30       # Default take-profit percentage
MAX_DAILY_LOSS=150         # Maximum daily loss before stopping
```

## How It Works

1. **Market Scanning** (every 30s): Fetches active high-volume markets from Polymarket
2. **Strategy Analysis**: Each strategy analyzes markets for opportunities
3. **Risk Check**: Validates orders against risk limits before execution
4. **Order Execution**: Places orders via Polymarket CLOB API
5. **Position Monitoring** (every 10s): Checks stop-losses and take-profits
6. **Portfolio Updates** (every 60s): Logs portfolio status

## Tips for Best Results

1. **Start with dry-run** to understand how the bot trades
2. **Use conservative risk** profile initially
3. **Focus on high-volume markets** — they have better liquidity
4. **Monitor the bot** — don't leave it completely unattended
5. **Set realistic expectations** — consistent small gains compound over time
