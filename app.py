import io
import json
import gzip
import re
import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go

# -----------------------------------------------------------------------------
# Page + visual system
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="NIFTY 500 | Signal Desk",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
:root { --ink:#e8eef8; --muted:#93a4bb; --line:rgba(148,163,184,.18); }
.stApp { background: radial-gradient(ellipse at 12% 0%, rgba(30,64,175,.18), transparent 34%), #080f1c; color:var(--ink); }
[data-testid="stHeader"] { background:rgba(8,15,28,.82); }
[data-testid="stSidebar"] { background:linear-gradient(180deg,#101c30 0%,#0b1423 100%); border-right:1px solid var(--line); }
.block-container { padding-top:1.35rem; padding-bottom:2rem; max-width:1600px; }
.hero { padding:24px 26px; border:1px solid rgba(96,165,250,.28); border-radius:20px; background:linear-gradient(115deg,rgba(30,64,175,.28),rgba(15,23,42,.78) 55%,rgba(6,95,70,.18)); box-shadow:0 14px 45px rgba(0,0,0,.22); margin-bottom:18px; }
.hero-kicker { color:#7dd3fc; font-size:11px; font-weight:800; letter-spacing:2px; text-transform:uppercase; }
.hero-title { color:#f8fafc; font-size:30px; font-weight:850; line-height:1.15; margin:7px 0 6px; }
.hero-sub { color:#a9bad0; font-size:13px; }
.section-label { color:#8fb5e8; font-size:11px; font-weight:800; letter-spacing:1.4px; text-transform:uppercase; margin:8px 0 10px; }
.metric-card { background:linear-gradient(145deg,rgba(24,39,62,.96),rgba(13,24,41,.96)); border:1px solid var(--line); border-radius:15px; padding:15px 17px; min-height:92px; }
.metric-label { color:#9aacc3; font-size:11px; text-transform:uppercase; letter-spacing:1px; font-weight:700; }
.metric-value { color:#f8fafc; font-size:25px; font-weight:800; margin-top:5px; }
.metric-note { color:#8095ae; font-size:11px; margin-top:1px; }
.signal-buy { color:#34d399; font-weight:800; }
.signal-sell { color:#fb7185; font-weight:800; }
.signal-neutral { color:#cbd5e1; font-weight:700; }
div[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:12px; overflow:hidden; }
.stButton>button[kind="primary"] { border:0; background:linear-gradient(90deg,#2563eb,#0d9488); color:white; font-weight:800; border-radius:11px; min-height:46px; }
.stButton>button { border-radius:10px; }
[data-testid="stTabs"] button { font-weight:750; }
div[data-testid="stExpander"] { border:1px solid var(--line); border-radius:12px; }
.small-note { color:#8fa2ba; font-size:11px; }
</style>
""", unsafe_allow_html=True)

INDEX_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
INSTRUMENT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
HIST_BASE = "https://api.upstox.com/v3/historical-candle"
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
TIMEFRAMES = ["5m", "15m", "30m", "1H", "2H", "4H", "Daily", "Weekly"]

# Broad, user-friendly sector buckets based on NIFTY constituent Industry values.
def sector_bucket(industry):
    s = str(industry or "").strip().lower()
    rules = [
        ("Banking & Financial Services", r"bank|financial|insurance|capital market|asset management|finance|housing finance|investment"),
        ("IT & Software", r"information technology|software|it services|technology"),
        ("Pharma & Healthcare", r"pharma|healthcare|hospital|diagnostic|biotechnology|medical"),
        ("Automobile", r"automobile|auto components|tyre|two wheel|passenger cars|commercial vehicles"),
        ("FMCG", r"fmcg|consumer food|personal care|household products|packaged foods|tobacco"),
        ("Metals & Mining", r"metal|mining|steel|aluminium|copper"),
        ("Energy & Power", r"oil|gas|power|energy|petroleum|refining|electric utilit"),
        ("Infrastructure & Capital Goods", r"construction|engineering|capital goods|infrastructure|industrial products|electrical equipment"),
        ("Realty", r"real estate|realty"),
        ("Chemicals", r"chemical|fertilizer|agrochemical|specialty chemical"),
        ("Telecom", r"telecom"),
        ("Consumer Durables", r"consumer durables|home appliance|electronics|jewellery|apparel|textile|footwear"),
        ("Services & Others", r"services|logistics|transport|tourism|media|entertainment|retail|consulting"),
    ]
    for label, pattern in rules:
        if re.search(pattern, s):
            return label
    return "Others / Unclassified" if not s or s == "nan" else str(industry).strip()


@st.cache_data(ttl=3600, show_spinner=False)
def load_nifty500():
    response = requests.get(INDEX_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    raw = pd.read_csv(io.BytesIO(response.content))
    raw.columns = [str(c).strip() for c in raw.columns]
    cols = {c.lower(): c for c in raw.columns}
    symbol_col = cols.get("symbol") or cols.get("ticker")
    industry_col = cols.get("industry") or cols.get("sector")
    name_col = cols.get("company name") or cols.get("companyname") or cols.get("name")
    if not symbol_col:
        raise ValueError("NIFTY 500 constituent file mein Symbol column nahi mila.")
    out = pd.DataFrame({"Symbol": raw[symbol_col].astype(str).str.strip()})
    out["Company"] = raw[name_col].astype(str).str.strip() if name_col else out.Symbol
    out["Industry"] = raw[industry_col].astype(str).str.strip() if industry_col else "Unclassified"
    out["Sector"] = out["Industry"].apply(sector_bucket)
    out = out[out.Symbol.ne("") & out.Symbol.ne("NAN")].drop_duplicates("Symbol")
    return out.sort_values("Symbol").reset_index(drop=True)


@st.cache_data(ttl=86400, show_spinner=False)
def load_instruments():
    response = requests.get(INSTRUMENT_URL, headers=HEADERS, timeout=60)
    response.raise_for_status()
    content = response.content
    try:
        decoded = gzip.decompress(content).decode("utf-8")
    except OSError:
        decoded = content.decode("utf-8")
    data = json.loads(decoded)
    df = pd.DataFrame(data)
    required = {"segment", "instrument_type", "trading_symbol", "instrument_key"}
    if not required.issubset(df.columns):
        raise ValueError("Upstox instrument master ka format expected format se alag hai.")
    df = df[(df.segment == "NSE_EQ") & (df.instrument_type == "EQ")]
    df = df.drop_duplicates("trading_symbol")
    return dict(zip(df.trading_symbol.astype(str), df.instrument_key.astype(str)))


@st.cache_data(ttl=180, show_spinner=False)
def get_candles(instrument_key, token, timeframe, lookback_days):
    intraday = {"5m": ("minutes", 5), "15m": ("minutes", 15), "30m": ("minutes", 30),
                "1H": ("minutes", 60), "2H": ("minutes", 120), "4H": ("minutes", 240)}
    if timeframe in intraday:
        unit, interval = intraday[timeframe]
    elif timeframe == "Daily":
        unit, interval = "days", 1
    else:
        unit, interval = "weeks", 1
    end = date.today()
    start = end - timedelta(days=lookback_days)
    url = f"{HIST_BASE}/{quote(instrument_key, safe='')}/{unit}/{interval}/{end.isoformat()}/{start.isoformat()}"
    headers = {**HEADERS, "Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers, timeout=25)
    if response.status_code != 200:
        message = response.text[:150].replace("\n", " ")
        raise RuntimeError(f"Upstox API {response.status_code}: {message}")
    candles = response.json().get("data", {}).get("candles", [])
    if not candles:
        return pd.DataFrame()
    names = ["Datetime", "Open", "High", "Low", "Close", "Volume", "OI"][:len(candles[0])]
    df = pd.DataFrame(candles, columns=names)
    df["Datetime"] = pd.to_datetime(df["Datetime"], errors="coerce")
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Datetime", "Open", "High", "Low", "Close"]).sort_values("Datetime").drop_duplicates("Datetime").reset_index(drop=True)


def drop_incomplete_last_candle(df, timeframe):
    """Avoid using a still-forming latest candle for signals."""
    if df.empty:
        return df
    last = df.iloc[-1]["Datetime"]
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    if last.tzinfo is None:
        last = last.tz_localize("Asia/Kolkata")
    else:
        last = last.tz_convert("Asia/Kolkata")
    mins = {"5m": 5, "15m": 15, "30m": 30, "1H": 60, "2H": 120, "4H": 240}.get(timeframe)
    if mins and last + pd.Timedelta(minutes=mins) > now:
        return df.iloc[:-1].copy()
    if timeframe == "Daily" and last.date() == now.date():
        return df.iloc[:-1].copy()
    if timeframe == "Weekly" and last.isocalendar().week == now.isocalendar().week and last.year == now.year:
        return df.iloc[:-1].copy()
    return df


def calculate_rsi(close, period=14):
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + rs))
    result = result.mask((avg_loss == 0) & (avg_gain > 0), 100)
    result = result.mask((avg_gain == 0) & (avg_loss > 0), 0)
    return result


def enrich(df, rsi_period, oversold, overbought, fast_ema, slow_ema):
    d = df.copy()
    d["RSI"] = calculate_rsi(d.Close, rsi_period)
    for period in sorted(set([fast_ema, slow_ema, 50, 200])):
        d[f"EMA{period}"] = d.Close.ewm(span=period, adjust=False, min_periods=period).mean()
    d["RSI_BUY"] = (d.RSI.shift(1) <= oversold) & (d.RSI > oversold)
    d["RSI_SELL"] = (d.RSI.shift(1) >= overbought) & (d.RSI < overbought)
    d["EMA_BUY"] = (d[f"EMA{fast_ema}"].shift(1) <= d[f"EMA{slow_ema}"].shift(1)) & (d[f"EMA{fast_ema}"] > d[f"EMA{slow_ema}"])
    d["EMA_SELL"] = (d[f"EMA{fast_ema}"].shift(1) >= d[f"EMA{slow_ema}"].shift(1)) & (d[f"EMA{fast_ema}"] < d[f"EMA{slow_ema}"])
    d["Trend"] = "Neutral"
    d.loc[(d.Close > d.EMA50) & (d.EMA50 > d.EMA200), "Trend"] = "Bullish"
    d.loc[(d.Close < d.EMA50) & (d.EMA50 < d.EMA200), "Trend"] = "Bearish"
    return d


def make_signal_history(d, buy_col, sell_col, horizon, history_days=30):
    cutoff = pd.Timestamp.now(tz="Asia/Kolkata") - pd.Timedelta(days=history_days)
    times = pd.to_datetime(d.Datetime, errors="coerce")
    if times.dt.tz is None:
        local_times = times.dt.tz_localize("Asia/Kolkata", ambiguous="NaT", nonexistent="NaT")
    else:
        local_times = times.dt.tz_convert("Asia/Kolkata")
    rows = []
    for signal_col, side in [(buy_col, "BUY"), (sell_col, "SELL")]:
        signal_indices = list(d.index[d[signal_col].fillna(False)])
        for idx in signal_indices:
            if local_times.iloc[idx] < cutoff:
                continue
            row = d.iloc[idx]
            next_idx = idx + 1
            entry = float(d.iloc[next_idx].Open) if next_idx < len(d) else float(row.Close)
            exit_idx = idx + horizon
            exit_price = float(d.iloc[exit_idx].Close) if exit_idx < len(d) else None
            outcome = "Pending"
            pnl_pct = None
            if exit_price is not None:
                pnl_pct = ((exit_price - entry) / entry * 100) * (1 if side == "BUY" else -1)
                outcome = "Win" if pnl_pct > 0 else "Loss" if pnl_pct < 0 else "Flat"
            rows.append({
                "Signal Time": local_times.iloc[idx].strftime("%Y-%m-%d %H:%M"),
                "Signal": side,
                "Signal Close": round(float(row.Close), 2),
                "Entry Price": round(entry, 2),
                "RSI": round(float(row.RSI), 2) if pd.notna(row.RSI) else None,
                "Trend": row.Trend,
                "Exit Price": round(exit_price, 2) if exit_price is not None else None,
                "Outcome": outcome,
                "Return %": round(pnl_pct, 2) if pnl_pct is not None else None,
            })
    return rows


def stats_for_history(rows):
    evaluated = [r for r in rows if r["Outcome"] in ("Win", "Loss", "Flat")]
    wins = sum(r["Outcome"] == "Win" for r in evaluated)
    return len(rows), len(evaluated), wins, (wins / len(evaluated) * 100 if evaluated else None)


def scan_one(stock, company, sector, instrument_key, token, timeframe, days, mode,
             horizon, trend_filter, rsi_period, oversold, overbought, fast_ema, slow_ema, history_days):
    try:
        raw = get_candles(instrument_key, token, timeframe, days)
        raw = drop_incomplete_last_candle(raw, timeframe)
        minimum = max(205, slow_ema + 5, rsi_period + 5) if mode == "EMA" else rsi_period + 5
        if len(raw) < minimum:
            return None, f"{stock}: candles kam hain ({len(raw)}; required {minimum})"
        d = enrich(raw, rsi_period, oversold, overbought, fast_ema, slow_ema)
        buy_col, sell_col = ("RSI_BUY", "RSI_SELL") if mode == "RSI" else ("EMA_BUY", "EMA_SELL")
        if mode == "EMA" and trend_filter:
            d[buy_col] = d[buy_col] & (d.Close > d.EMA50)
            d[sell_col] = d[sell_col] & (d.Close < d.EMA50)
        history = make_signal_history(d, buy_col, sell_col, horizon, history_days)
        n_all, n_eval, wins, win_rate = stats_for_history(history)
        latest = d.iloc[-1]
        latest_signal = "WAIT"
        if bool(latest[buy_col]):
            latest_signal = "BUY"
        elif bool(latest[sell_col]):
            latest_signal = "SELL"
        # Last historical signal is shown separately from a fresh signal on latest completed candle.
        past = [r for r in history if r["Signal"] in ("BUY", "SELL")]
        last_signal = past[-1] if past else None
        summary = {
            "Symbol": stock, "Company": company, "Sector": sector,
            "Price": round(float(latest.Close), 2), "Signal": latest_signal,
            "RSI": round(float(latest.RSI), 2) if pd.notna(latest.RSI) else None,
            "Trend": latest.Trend,
            f"EMA {fast_ema}": round(float(latest[f"EMA{fast_ema}"]), 2) if pd.notna(latest[f"EMA{fast_ema}"]) else None,
            f"EMA {slow_ema}": round(float(latest[f"EMA{slow_ema}"]), 2) if pd.notna(latest[f"EMA{slow_ema}"]) else None,
            "History Signals": n_all, "Evaluated": n_eval, "Wins": wins,
            "Win Rate %": round(win_rate, 1) if win_rate is not None else None,
            "Last Signal Time": last_signal["Signal Time"] if last_signal else "—",
            "Last Signal": last_signal["Signal"] if last_signal else "—",
        }
        for row in history:
            row.update({"Symbol": stock, "Company": company, "Sector": sector})
        return {"summary": summary, "history": history, "chart": d.tail(180)}, None
    except Exception as exc:
        return None, f"{stock}: {str(exc)[:170]}"


def metric_card(label, value, note=""):
    st.markdown(f'<div class="metric-card"><div class="metric-label">{label}</div><div class="metric-value">{value}</div><div class="metric-note">{note}</div></div>', unsafe_allow_html=True)


# -----------------------------------------------------------------------------
# Header
# -----------------------------------------------------------------------------
st.markdown("""
<div class="hero">
  <div class="hero-kicker">INDIA EQUITY • NIFTY 500 • UPSTOX DATA</div>
  <div class="hero-title">SIGNAL DESK <span style="color:#60a5fa">/</span> RSI + EMA</div>
  <div class="hero-sub">Sector-wise market scanner · Completed-candle signals · 30-day signal history · Historical performance</div>
</div>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# Sidebar controls
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ⚙️ Scanner controls")
    token = st.text_input("Upstox Access Token", type="password", help="Token sirf is app session mein use hota hai; ise kisi ke saath share na karein.")
    st.markdown("---")
    mode = st.radio("Strategy", ["RSI", "EMA"], horizontal=True, help="Ek waqt mein selected strategy scan hogi.")
    timeframe = st.selectbox("Timeframe", TIMEFRAMES, index=1)
    lookback = st.selectbox("Data lookback", [30, 60, 90, 180, 365], index=2, format_func=lambda n: f"{n} calendar days")
    history_days = st.selectbox("Signal history", [7, 15, 30], index=2, format_func=lambda n: f"Last {n} days")
    horizon = st.selectbox("Performance check after", [3, 5, 10, 15, 20], index=1, format_func=lambda n: f"{n} candles")
    st.markdown("---")
    st.markdown("**RSI settings**")
    rsi_period = st.number_input("RSI period", min_value=2, max_value=50, value=14, step=1)
    c1, c2 = st.columns(2)
    oversold = c1.number_input("BUY cross above", min_value=5, max_value=49, value=30, step=1)
    overbought = c2.number_input("SELL cross below", min_value=51, max_value=95, value=70, step=1)
    st.markdown("**EMA settings**")
    c1, c2 = st.columns(2)
    fast_ema = c1.number_input("Fast EMA", min_value=2, max_value=100, value=9, step=1)
    slow_ema = c2.number_input("Slow EMA", min_value=3, max_value=200, value=21, step=1)
    trend_filter = st.checkbox("EMA 50 trend confirmation", value=False, disabled=(mode != "EMA"))
    st.markdown("---")
    max_stocks = st.selectbox("Maximum stocks per scan", [25, 50, 100, 200, 300, 500], index=5)
    workers = st.select_slider("Concurrent API requests", options=[1, 2, 3, 4, 5], value=3)
    run_scan = st.button("▶  RUN SCANNER", type="primary", use_container_width=True)
    st.caption("API speed and rate limits depend on your Upstox account and network.")

if oversold >= overbought:
    st.error("RSI BUY threshold ko SELL threshold se chhota rakho.")
    st.stop()
if fast_ema >= slow_ema:
    st.warning("Fast EMA normally Slow EMA se chhota hona chahiye. Settings check kar lo.")

# Load universe and instrument map.
try:
    with st.spinner("NIFTY 500 universe aur Upstox instrument master load ho raha hai…"):
        universe = load_nifty500()
        instrument_map = load_instruments()
    universe["Instrument Key"] = universe.Symbol.map(instrument_map)
    matched_universe = universe.dropna(subset=["Instrument Key"]).copy()
    missing_count = len(universe) - len(matched_universe)
except Exception as exc:
    st.error(f"Stock list / Upstox instrument master load nahi hua: {exc}")
    st.info("Internet connection check karo. Upstox instrument master aur NIFTY Indices constituent CSV accessible hone chahiye.")
    st.stop()

# Top-level universe snapshot.
top1, top2, top3, top4 = st.columns(4)
with top1: metric_card("NIFTY 500 constituents", f"{len(universe)}", "Official constituent CSV")
with top2: metric_card("Upstox matched", f"{len(matched_universe)}", "NSE cash instruments")
with top3: metric_card("Sectors", f"{matched_universe.Sector.nunique()}", "Grouped industry buckets")
with top4: metric_card("Unmatched symbols", f"{missing_count}", "Not available in instrument master")

st.markdown('<div class="section-label">01 / UNIVERSE FILTERS</div>', unsafe_allow_html=True)
f1, f2, f3 = st.columns([1.2, 1.2, 2])
with f1:
    sector_options = ["All Sectors"] + sorted(matched_universe.Sector.dropna().unique().tolist())
    selected_sector = st.selectbox("Sector", sector_options)
with f2:
    signal_filter = st.selectbox("Signal filter", ["All", "BUY only", "SELL only", "WAIT only"])
with f3:
    scoped_for_search = matched_universe if selected_sector == "All Sectors" else matched_universe[matched_universe.Sector == selected_sector]
    selected_symbols = st.multiselect("Specific stocks (optional)", sorted(scoped_for_search.Symbol.tolist()), placeholder="Search symbols; blank means all stocks in selected sector")
scoped = scoped_for_search.copy()
if selected_symbols:
    scoped = scoped[scoped.Symbol.isin(selected_symbols)]

if timeframe in ("5m", "15m"):
    effective_days = min(int(lookback), 30)
elif timeframe in ("30m", "1H", "2H", "4H"):
    effective_days = min(int(lookback), 90)
else:
    effective_days = int(lookback)

if effective_days != lookback:
    st.caption(f"ℹ️ {timeframe} timeframe par Upstox historical API range ko {effective_days} days tak cap kiya gaya hai.")

if run_scan:
    if not token.strip():
        st.error("Pehle Upstox Access Token enter karo.")
    elif fast_ema >= slow_ema:
        st.error("Fast EMA ko Slow EMA se chhota rakho.")
    elif scoped.empty:
        st.warning("Is filter ke liye stocks available nahi hain.")
    else:
        work = scoped.sort_values("Symbol").head(int(max_stocks))
        st.markdown('<div class="section-label">02 / LIVE SCAN</div>', unsafe_allow_html=True)
        st.write(f"**{len(work)} stocks** · **{selected_sector}** · **{mode} strategy** · **{timeframe}** · **{effective_days} days data**")
        progress = st.progress(0)
        status = st.empty()
        results, errors = [], []
        jobs = list(work[["Symbol", "Company", "Sector", "Instrument Key"]].itertuples(index=False, name=None))
        with ThreadPoolExecutor(max_workers=int(workers)) as executor:
            futures = {
                executor.submit(scan_one, symbol, company, sector, key, token.strip(), timeframe,
                                effective_days, mode, int(horizon), bool(trend_filter), int(rsi_period),
                                float(oversold), float(overbought), int(fast_ema), int(slow_ema), int(history_days)): symbol
                for symbol, company, sector, key in jobs
            }
            for done, future in enumerate(as_completed(futures), start=1):
                try:
                    result, error = future.result()
                    if result:
                        results.append(result)
                    if error:
                        errors.append(error)
                except Exception as exc:
                    errors.append(f"{futures[future]}: {str(exc)[:140]}")
                progress.progress(done / len(futures))
                status.write(f"Scanning… {done}/{len(futures)} complete | {len(results)} stocks processed successfully")
        summaries = [r["summary"] for r in results]
        histories = [h for r in results for h in r["history"]]
        chart_data = {r["summary"]["Symbol"]: r["chart"] for r in results}
        summary_df = pd.DataFrame(summaries)
        history_df = pd.DataFrame(histories)
        if not summary_df.empty:
            summary_df["Signal Rank"] = summary_df.Signal.map({"BUY": 0, "SELL": 1, "WAIT": 2}).fillna(3)
            summary_df = summary_df.sort_values(["Signal Rank", "Win Rate %", "History Signals"], ascending=[True, False, False]).drop(columns="Signal Rank")
        st.session_state["desk_summary"] = summary_df
        st.session_state["desk_history"] = history_df
        st.session_state["desk_charts"] = chart_data
        st.session_state["desk_meta"] = {"mode": mode, "sector": selected_sector, "timeframe": timeframe,
                                          "stocks": len(work), "successful": len(results), "errors": len(errors),
                                          "run_at": pd.Timestamp.now(tz="Asia/Kolkata").strftime("%d %b %Y, %I:%M:%S %p IST"),
                                          "horizon": int(horizon), "history_days": int(history_days),
                                          "fast_ema": int(fast_ema), "slow_ema": int(slow_ema)}
        st.session_state["desk_errors"] = errors
        if summary_df.empty:
            st.warning("Kisi stock ka usable candle data nahi mila. API token, permissions aur API errors check karo.")
        else:
            st.success(f"Scan complete — {len(results)} stocks processed. {len(histories)} historical signals found.")
        if errors:
            with st.expander(f"Skipped stocks / API errors ({len(errors)})"):
                st.code("\n".join(errors[:120]))

# -----------------------------------------------------------------------------
# Results desk
# -----------------------------------------------------------------------------
if "desk_summary" in st.session_state:
    summary_df = st.session_state["desk_summary"].copy()
    history_df = st.session_state["desk_history"].copy()
    meta = st.session_state["desk_meta"]
    st.markdown('<div class="section-label">03 / SCAN RESULTS</div>', unsafe_allow_html=True)
    st.caption(f"Last scan: {meta['run_at']} · Strategy: {meta['mode']} · Timeframe: {meta['timeframe']} · Evaluation horizon: {meta['horizon']} candles")
    if not summary_df.empty:
        buy_count = int((summary_df.Signal == "BUY").sum())
        sell_count = int((summary_df.Signal == "SELL").sum())
        wait_count = int((summary_df.Signal == "WAIT").sum())
        valid_rates = summary_df["Win Rate %"].dropna()
        avg_rate = f"{valid_rates.mean():.1f}%" if not valid_rates.empty else "—"
        a, b, c, d = st.columns(4)
        with a: metric_card("Fresh BUY signals", buy_count, "Latest completed candle")
        with b: metric_card("Fresh SELL signals", sell_count, "Latest completed candle")
        with c: metric_card("WAIT / no fresh cross", wait_count, "No new crossover on latest candle")
        with d: metric_card("Average historical win rate", avg_rate, "Evaluated signal outcomes only")
        visible = summary_df.copy()
        if signal_filter == "BUY only": visible = visible[visible.Signal == "BUY"]
        elif signal_filter == "SELL only": visible = visible[visible.Signal == "SELL"]
        elif signal_filter == "WAIT only": visible = visible[visible.Signal == "WAIT"]
        st.markdown("#### Stock signal board")
        display_cols = ["Symbol", "Company", "Sector", "Price", "Signal", "RSI", "Trend", f"EMA {meta['fast_ema']}", f"EMA {meta['slow_ema']}", "History Signals", "Evaluated", "Win Rate %", "Last Signal Time", "Last Signal"]
        display_cols = [c for c in display_cols if c in visible.columns]
        st.dataframe(visible[display_cols], use_container_width=True, hide_index=True, height=430,
                     column_config={"Win Rate %": st.column_config.NumberColumn(format="%.1f%%"),
                                    "Price": st.column_config.NumberColumn(format="₹%.2f"),
                                    "RSI": st.column_config.NumberColumn(format="%.2f")})
        st.download_button("⬇ Download stock signal board (CSV)", visible[display_cols].to_csv(index=False).encode("utf-8"),
                           "nifty500_signal_board.csv", "text/csv")
        st.markdown("#### Sector signal distribution")
        sector_stats = summary_df.groupby("Sector").agg(Stocks=("Symbol", "count"), BUY=("Signal", lambda x: int((x == "BUY").sum())),
                                                          SELL=("Signal", lambda x: int((x == "SELL").sum())), WAIT=("Signal", lambda x: int((x == "WAIT").sum()))).reset_index()
        sector_stats = sector_stats.sort_values(["BUY", "SELL", "Stocks"], ascending=False)
        fig = go.Figure()
        fig.add_trace(go.Bar(name="BUY", x=sector_stats.Sector, y=sector_stats.BUY, marker_color="#10b981"))
        fig.add_trace(go.Bar(name="SELL", x=sector_stats.Sector, y=sector_stats.SELL, marker_color="#fb7185"))
        fig.add_trace(go.Bar(name="WAIT", x=sector_stats.Sector, y=sector_stats.WAIT, marker_color="#64748b"))
        fig.update_layout(barmode="stack", height=340, margin=dict(l=10,r=10,t=20,b=10),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          font_color="#dbeafe", xaxis_title="Sector", yaxis_title="Stocks", legend_title="Signal")
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(sector_stats, use_container_width=True, hide_index=True)

        # Selected-stock detail chart.
        st.markdown("#### Stock detail & indicator chart")
        chart_symbols = sorted(st.session_state["desk_charts"].keys())
        chosen = st.selectbox("Choose a scanned stock", chart_symbols)
        chart_df = st.session_state["desk_charts"].get(chosen)
        if chart_df is not None and not chart_df.empty:
            chart_fig = go.Figure()
            chart_fig.add_trace(go.Candlestick(x=chart_df.Datetime, open=chart_df.Open, high=chart_df.High,
                                               low=chart_df.Low, close=chart_df.Close, name=chosen))
            fast_col, slow_col = f"EMA{meta['fast_ema']}", f"EMA{meta['slow_ema']}"
            if fast_col in chart_df:
                chart_fig.add_trace(go.Scatter(x=chart_df.Datetime, y=chart_df[fast_col], name=fast_col, line=dict(width=1.5, color="#38bdf8")))
            if slow_col in chart_df:
                chart_fig.add_trace(go.Scatter(x=chart_df.Datetime, y=chart_df[slow_col], name=slow_col, line=dict(width=1.5, color="#fbbf24")))
            chart_fig.update_layout(height=470, xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=20,b=10),
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(10,20,35,.7)",
                                    font_color="#dbeafe", legend_orientation="h")
            st.plotly_chart(chart_fig, use_container_width=True)
            if meta["mode"] == "RSI":
                rsi_fig = go.Figure()
                rsi_fig.add_trace(go.Scatter(x=chart_df.Datetime, y=chart_df.RSI, name="RSI", line=dict(width=1.8, color="#a78bfa")))
                rsi_fig.add_hline(y=float(oversold), line_dash="dash", line_color="#10b981", annotation_text=f"BUY {int(oversold)}")
                rsi_fig.add_hline(y=float(overbought), line_dash="dash", line_color="#fb7185", annotation_text=f"SELL {int(overbought)}")
                rsi_fig.update_layout(height=220, margin=dict(l=10,r=10,t=10,b=10), paper_bgcolor="rgba(0,0,0,0)",
                                      plot_bgcolor="rgba(10,20,35,.7)", font_color="#dbeafe", yaxis=dict(range=[0,100], title="RSI"))
                st.plotly_chart(rsi_fig, use_container_width=True)
            stock_history = history_df[history_df.Symbol == chosen] if not history_df.empty else pd.DataFrame()
            if not stock_history.empty:
                st.markdown(f"**{chosen} — last {meta['history_days']} days signal history**")
                st.dataframe(stock_history.sort_values("Signal Time", ascending=False), use_container_width=True, hide_index=True)
            else:
                st.info("Selected stock mein selected history window ke andar koi matching signal nahi mila.")
        if not history_df.empty:
            st.markdown(f"#### Historical signal history — last {meta['history_days']} days")
            st.dataframe(history_df.sort_values("Signal Time", ascending=False), use_container_width=True, hide_index=True, height=360)
            st.download_button("⬇ Download historical signal history (CSV)", history_df.to_csv(index=False).encode("utf-8"),
                               "nifty500_signal_history.csv", "text/csv")
        else:
            st.info("History window mein koi RSI/EMA crossover nahi mila.")
    else:
        st.info("Scan complete hua, lekin display karne ke liye valid result nahi mila.")
else:
    st.markdown('<div class="section-label">02 / SECTOR OVERVIEW</div>', unsafe_allow_html=True)
    sector_counts = matched_universe.groupby("Sector").agg(Stocks=("Symbol", "count")).reset_index().sort_values("Stocks", ascending=False)
    left, right = st.columns([1.15, 1])
    with left:
        st.markdown("#### NIFTY 500 sector coverage")
        st.dataframe(sector_counts, use_container_width=True, hide_index=True, height=390)
    with right:
        fig = go.Figure(go.Pie(labels=sector_counts.Sector, values=sector_counts.Stocks, hole=.62,
                               textinfo="label+percent", marker=dict(line=dict(color="#0b1423", width=2))))
        fig.update_layout(height=390, margin=dict(l=0,r=0,t=20,b=0), paper_bgcolor="rgba(0,0,0,0)",
                          font_color="#dbeafe", showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    st.info("Sidebar mein Upstox token enter karo, strategy/timeframe select karo, sector filter lagao aur RUN SCANNER dabao.")

with st.expander("📘 Strategy rules & performance calculation"):
    st.markdown(f"""
    **RSI strategy**
    - RSI({int(rsi_period)}) BUY: previous completed candle RSI {int(oversold)} ya neeche tha aur latest completed candle par {int(oversold)} ke upar cross hua.
    - RSI({int(rsi_period)}) SELL: previous completed candle RSI {int(overbought)} ya upar tha aur latest completed candle par {int(overbought)} ke neeche cross hua.

    **EMA strategy**
    - BUY: EMA({int(fast_ema)}) ne EMA({int(slow_ema)}) ko neeche se upar cross kiya.
    - SELL: EMA({int(fast_ema)}) ne EMA({int(slow_ema)}) ko upar se neeche cross kiya.
    - Optional EMA 50 filter: BUY ke liye close > EMA50; SELL ke liye close < EMA50.

    **Signal and historical stats**
    - Forming/latest incomplete candle ko signal calculation se exclude kiya jata hai.
    - History table selected last {int(history_days)} days ke signals dikhati hai.
    - Performance estimate: signal candle ke baad next candle OPEN ko entry aur {int(horizon)}-candle horizon ke close ko exit maana gaya hai. BUY mein positive return aur SELL mein positive direction-adjusted return = win.
    - Brokerage, taxes, slippage, stop-loss, target aur liquidity impact included nahi hain. Win rate historical estimate hai, future returns ki guarantee nahi.
    """)

st.divider()
st.markdown('<div class="small-note">NIFTY 500 constituent source: NSE Indices public constituent CSV · Market candles: Upstox API · Times displayed in IST. Verify signals independently before placing trades.</div>', unsafe_allow_html=True)
