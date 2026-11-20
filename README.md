# NIFTY 500 RSI + EMA Signal Desk

A standalone Streamlit stock scanner for NIFTY 500 NSE cash equities using Upstox historical candle data.

## Features
- One application with RSI and EMA strategy modes.
- NIFTY 500 universe grouped into sector buckets.
- Filters for sector, symbols, timeframe and signal side.
- RSI period and threshold controls; EMA fast/slow controls and optional EMA 50 trend confirmation.
- Completed-candle signal calculations (forming candle is excluded).
- Last 7/15/30 days signal history, historical outcome estimate, sector distribution and CSV exports.
- Interactive candlestick detail chart with EMA overlays.
- Upstox token is entered at runtime and is not written to a local config file.

## Requirements
- Windows 10/11
- Python 3.10 or newer (add Python to PATH during installation)
- Upstox API access token with NSE historical candle permissions
- Internet access to NIFTY Indices constituent CSV and Upstox endpoints

## Run
1. Extract the ZIP to a normal folder (avoid running from inside the ZIP).
2. Double-click `START.bat`.
3. Wait for dependency installation and browser launch.
4. Paste your Upstox access token in the sidebar.
5. Select RSI or EMA, timeframe and sector; click `RUN SCANNER`.

The first launch may take a few minutes because Python packages are installed. The scan may take additional time depending on the number of stocks, timeframe, network and Upstox API rate limits.

## Signal rules
- RSI BUY: RSI crosses above the selected oversold threshold.
- RSI SELL: RSI crosses below the selected overbought threshold.
- EMA BUY/SELL: selected fast EMA crosses selected slow EMA.
- Optional EMA 50 trend confirmation filters EMA signals.

## Notes and limitations
- Intraday historical lookback is capped conservatively in the app: 30 days for 5m/15m, 90 days for 30m–4H. Daily/weekly lookback is user-selectable.
- Historical win-rate estimate uses next candle open as entry and selected candle-horizon close as exit. It excludes brokerage, taxes, slippage, liquidity, stop-loss and target. It is not a guarantee of future performance.
- Public index constituent and instrument-master formats may change. API access and rate limits depend on Upstox account permissions.
- This is a research/alert tool, not investment advice. Verify signals before trading.
