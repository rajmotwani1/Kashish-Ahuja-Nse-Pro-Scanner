# NIFTY 500 RSI + EMA Signal Desk (Light UI)

## Important: token handling
This app intentionally has NO token textbox. It reads `UPSTOX_ACCESS_TOKEN` from Streamlit Secrets (same pattern as the existing Blue Whale NSE scanner) or the environment variable.

### Local Windows setup
1. Extract this folder.
2. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`.
3. Paste the same Upstox token already used by your other scanner into `UPSTOX_ACCESS_TOKEN`.
4. Run `START.bat`.

### Streamlit Cloud
Open this app's **Settings → Secrets** and add `UPSTOX_ACCESS_TOKEN = "your-token"`. The token is never displayed in the scanner UI. A different Streamlit app/deployment cannot automatically read another app's private secrets, so configure it once in this app's own Secrets setting too.

## Features
- One app with RSI and EMA scanner modes.
- NIFTY 500 sector classification and sector filter.
- RSI threshold-cross signals and configurable RSI period/levels.
- Fast/slow EMA crossover with optional EMA 50 and EMA 200 confirmation.
- 5m, 15m, 30m, 1H, 2H, 4H, Daily and Weekly timeframes.
- Completed-candle filtering for intraday intervals.
- Latest 30-day signal events, latest indicator snapshot, chart view and CSV exports.
- Light readable dashboard with sector distribution and connection status.

Historical win rate is a simplified forward-close comparison, not a broker-executed backtest. It excludes fees, slippage, stops and targets.
