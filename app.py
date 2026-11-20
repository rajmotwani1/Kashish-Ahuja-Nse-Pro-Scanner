import io, json, gzip, os, time
from datetime import date, timedelta, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go

st.set_page_config(page_title="NIFTY 500 | RSI & EMA Signal Desk", page_icon="📊", layout="wide", initial_sidebar_state="expanded")
IST = ZoneInfo("Asia/Kolkata")
INDEX_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
INSTRUMENT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
BASE = "https://api.upstox.com/v3/historical-candle"
HEADERS_BASE = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

# Light, readable trading-terminal styling (does not force a dark theme).
st.markdown("""
<style>
:root { color-scheme: light; }
.stApp { background: #f4f7fb; color: #172033; }
[data-testid="stHeader"] { background: rgba(244,247,251,.96); }
[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #dce4ef; }
.block-container { padding-top: 1.2rem; padding-bottom: 2.5rem; max-width: 1600px; }
h1,h2,h3 { color: #172b4d !important; letter-spacing: -.02em; }
[data-testid="stMetric"] { background: #ffffff; border: 1px solid #dce4ef; border-radius: 12px; padding: 14px 16px; box-shadow: 0 2px 8px rgba(20,40,80,.04); }
[data-testid="stMetricLabel"] { color: #62718a !important; }
[data-testid="stMetricValue"] { color: #152746 !important; }
.stButton > button[kind="primary"] { background: #1769d2; border: 1px solid #1769d2; color: white; border-radius: 9px; font-weight: 700; min-height: 2.7rem; }
.stButton > button { border-radius: 9px; }
[data-testid="stDataFrame"] { background: #fff; border: 1px solid #dce4ef; border-radius: 10px; }
[data-testid="stTabs"] button { font-weight: 650; }
.desk-hero { background: linear-gradient(110deg,#ffffff,#eaf2ff); border: 1px solid #d7e4f7; border-radius: 16px; padding: 22px 24px; margin-bottom: 16px; }
.desk-kicker { color:#1769d2; font-weight:800; text-transform:uppercase; font-size:11px; letter-spacing:.12em; }
.desk-sub { color:#61718a; font-size:14px; margin-top:4px; }
.status-pill { display:inline-block; padding:5px 10px; border-radius:20px; background:#e8f7ee; color:#147a3d; font-size:12px; font-weight:700; }
.small-note { color:#64748b; font-size:12px; }
</style>
""", unsafe_allow_html=True)

SECTOR_ALIASES = {
    "Financial Services": "Banking & Financial Services", "Financial Services - Banks": "Banking & Financial Services",
    "Information Technology": "IT & Software", "Healthcare": "Pharma & Healthcare",
    "Automobile and Auto Components": "Automobile", "Fast Moving Consumer Goods": "FMCG",
    "Oil Gas & Consumable Fuels": "Energy & Power", "Power": "Energy & Power",
    "Construction": "Infrastructure", "Telecommunication": "Telecom",
}
TIMEFRAMES = ["5m", "15m", "30m", "1H", "2H", "4H", "Daily", "Weekly"]

# Same authentication pattern as the user's existing Blue Whale NSE scanner:
# store UPSTOX_ACCESS_TOKEN in Streamlit Secrets, not in a textbox inside the scanner.
def get_secret(name, default=""):
    try:
        value = st.secrets.get(name, default)
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.getenv(name, default).strip()

TOKEN = get_secret("UPSTOX_ACCESS_TOKEN")
APP_PASSWORD = get_secret("APP_PASSWORD")
if APP_PASSWORD and not st.session_state.get("authenticated", False):
    st.markdown('<div class="desk-hero"><div class="desk-kicker">Private workspace</div><h2>📊 NIFTY 500 Signal Desk</h2><div class="desk-sub">Sign in to continue.</div></div>', unsafe_allow_html=True)
    pwd = st.text_input("App password", type="password")
    if st.button("Login", type="primary"):
        if pwd == APP_PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

@st.cache_data(ttl=3600, show_spinner=False)
def load_nifty500():
    headers = {**HEADERS_BASE, "Referer": "https://www.niftyindices.com/", "Accept": "text/csv,*/*"}
    r = requests.get(INDEX_URL, headers=headers, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content))
    df.columns = [str(c).strip() for c in df.columns]
    sym_col = next((c for c in df.columns if c.lower() in ("symbol", "ticker")), None)
    sec_col = next((c for c in df.columns if c.lower() in ("industry", "sector")), None)
    name_col = next((c for c in df.columns if c.lower() in ("company name", "companyname", "name")), None)
    if not sym_col:
        raise ValueError("NIFTY 500 CSV mein Symbol column nahi mila.")
    out = pd.DataFrame({"Symbol": df[sym_col].astype(str).str.strip()})
    out["Company"] = df[name_col].astype(str).str.strip() if name_col else out.Symbol
    out["Sector"] = df[sec_col].astype(str).str.strip() if sec_col else "Unclassified"
    out["Sector"] = out.Sector.replace(SECTOR_ALIASES).replace({"nan": "Unclassified", "": "Unclassified"})
    return out[out.Symbol.ne("")].drop_duplicates("Symbol").reset_index(drop=True)

@st.cache_data(ttl=86400, show_spinner=False)
def load_instruments():
    r = requests.get(INSTRUMENT_URL, headers=HEADERS_BASE, timeout=60)
    r.raise_for_status()
    raw = gzip.decompress(r.content)
    data = json.loads(raw.decode("utf-8"))
    df = pd.DataFrame(data)
    required = {"segment", "instrument_type", "trading_symbol", "instrument_key"}
    if not required.issubset(df.columns):
        raise ValueError("Upstox instrument master ka format expected format se alag hai.")
    df = df[(df.segment == "NSE_EQ") & (df.instrument_type == "EQ")].drop_duplicates("trading_symbol")
    return dict(zip(df.trading_symbol.astype(str), df.instrument_key.astype(str)))

@st.cache_data(ttl=180, show_spinner=False)
def get_candles(instrument_key, token, timeframe, days):
    intervals = {"5m":("minutes",5), "15m":("minutes",15), "30m":("minutes",30), "1H":("minutes",60), "2H":("minutes",120), "4H":("minutes",240), "Daily":("days",1), "Weekly":("weeks",1)}
    unit, interval = intervals[timeframe]
    end = date.today(); start = end - timedelta(days=days)
    url = f"{BASE}/{quote(instrument_key, safe='')}/{unit}/{interval}/{end.isoformat()}/{start.isoformat()}"
    headers = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=25)
    if resp.status_code != 200:
        raise RuntimeError(f"Upstox {resp.status_code}: {resp.text[:160]}")
    candles = resp.json().get("data", {}).get("candles", [])
    if not candles: return pd.DataFrame()
    df = pd.DataFrame(candles, columns=["Datetime","Open","High","Low","Close","Volume","OI"][:len(candles[0])])
    df["Datetime"] = pd.to_datetime(df.Datetime, errors="coerce")
    if df.Datetime.dt.tz is None:
        df["Datetime"] = df.Datetime.dt.tz_localize(IST)
    else:
        df["Datetime"] = df.Datetime.dt.tz_convert(IST)
    for col in ["Open","High","Low","Close","Volume"]: df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Datetime","Open","High","Low","Close"]).sort_values("Datetime").drop_duplicates("Datetime").reset_index(drop=True)
    # Remove the currently forming candle for intraday intervals.
    minutes = {"5m":5,"15m":15,"30m":30,"1H":60,"2H":120,"4H":240}.get(timeframe)
    if minutes and not df.empty:
        now = datetime.now(IST)
        df = df[df.Datetime + pd.to_timedelta(minutes, unit="m") <= now].reset_index(drop=True)
    return df

def calculate_rsi(close, period=14):
    delta = close.diff(); gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50)
    return rsi.astype(float)

def enrich(df, rsi_period, rsi_buy, rsi_sell, ema_fast, ema_slow):
    d = df.copy()
    d["RSI"] = calculate_rsi(d.Close, rsi_period)
    for n in sorted(set([ema_fast, ema_slow, 50, 200])):
        d[f"EMA{n}"] = d.Close.ewm(span=n, adjust=False, min_periods=n).mean()
    d["EMA50"] = d.Close.ewm(span=50, adjust=False, min_periods=50).mean()
    d["EMA200"] = d.Close.ewm(span=200, adjust=False, min_periods=200).mean()
    d["RSI_BUY"] = (d.RSI.shift(1) <= rsi_buy) & (d.RSI > rsi_buy)
    d["RSI_SELL"] = (d.RSI.shift(1) >= rsi_sell) & (d.RSI < rsi_sell)
    d["EMA_BUY"] = (d[f"EMA{ema_fast}"].shift(1) <= d[f"EMA{ema_slow}"].shift(1)) & (d[f"EMA{ema_fast}"] > d[f"EMA{ema_slow}"])
    d["EMA_SELL"] = (d[f"EMA{ema_fast}"].shift(1) >= d[f"EMA{ema_slow}"].shift(1)) & (d[f"EMA{ema_fast}"] < d[f"EMA{ema_slow}"])
    d["Trend"] = "Neutral"
    d.loc[(d.Close > d.EMA50) & (d.EMA50 > d.EMA200), "Trend"] = "Bullish"
    d.loc[(d.Close < d.EMA50) & (d.EMA50 < d.EMA200), "Trend"] = "Bearish"
    return d

def historical_stats(df, signal_col, side, horizon):
    ids = list(df.index[df[signal_col].fillna(False)])
    wins = total = 0
    for i in ids:
        entry_i, exit_i = i + 1, i + 1 + horizon
        if exit_i >= len(df): continue
        entry = float(df.iloc[entry_i].Open); exit_price = float(df.iloc[exit_i].Close)
        total += 1
        wins += int(exit_price > entry if side == "BUY" else exit_price < entry)
    return total, wins, (100*wins/total if total else None)

def scan_stock(stock, sector, instrument_key, token, timeframe, days, mode, horizon, rsi_period, rsi_buy, rsi_sell, ema_fast, ema_slow, confirm50, confirm200):
    try:
        raw = get_candles(instrument_key, token, timeframe, days)
        min_needed = max(30, ema_slow + 3, rsi_period + 3)
        if mode == "EMA" and (confirm50 or confirm200): min_needed = max(min_needed, 205 if confirm200 else 55)
        if len(raw) < min_needed:
            return None, f"{stock}: candles kam hain ({len(raw)}; required approx {min_needed})"
        d = enrich(raw, rsi_period, rsi_buy, rsi_sell, ema_fast, ema_slow)
        buy_col, sell_col = (("RSI_BUY","RSI_SELL") if mode == "RSI" else ("EMA_BUY","EMA_SELL"))
        if mode == "EMA" and confirm50:
            d[buy_col] = d[buy_col] & (d.Close > d.EMA50)
            d[sell_col] = d[sell_col] & (d.Close < d.EMA50)
        if mode == "EMA" and confirm200:
            d[buy_col] = d[buy_col] & (d.Close > d.EMA200)
            d[sell_col] = d[sell_col] & (d.Close < d.EMA200)
        signals = []
        for col, side in [(buy_col,"BUY"),(sell_col,"SELL")]:
            trades, wins, wr = historical_stats(d, col, side, horizon)
            sigs = d[d[col].fillna(False)]
            # return all historical signals; last 30 days are selected in the UI
            for _, row in sigs.iterrows():
                signals.append({"Sector":sector,"Stock":stock,"Signal":side,"Signal Time":row.Datetime.strftime("%Y-%m-%d %H:%M"),
                    "Entry Price":round(float(row.Close),2),"Close":round(float(row.Close),2),"RSI":round(float(row.RSI),2) if pd.notna(row.RSI) else None,
                    "EMA Fast":round(float(row[f"EMA{ema_fast}"]),2) if pd.notna(row[f"EMA{ema_fast}"]) else None,
                    "EMA Slow":round(float(row[f"EMA{ema_slow}"]),2) if pd.notna(row[f"EMA{ema_slow}"]) else None,
                    "Trend":row.Trend,"Historical Trades":trades,"Historical Wins":wins,"Historical Win Rate %":round(wr,1) if wr is not None else None})
        last = d.iloc[-1]
        snapshot = {"Sector":sector,"Stock":stock,"Last Price":round(float(last.Close),2),"RSI":round(float(last.RSI),2) if pd.notna(last.RSI) else None,
            "EMA Fast":round(float(last[f"EMA{ema_fast}"]),2) if pd.notna(last[f"EMA{ema_fast}"]) else None,
            "EMA Slow":round(float(last[f"EMA{ema_slow}"]),2) if pd.notna(last[f"EMA{ema_slow}"]) else None,"Trend":last.Trend,
            "Last Candle":last.Datetime.strftime("%Y-%m-%d %H:%M")}
        chart = d.tail(180).copy()
        return {"signals":signals,"snapshot":snapshot,"chart":chart}, None
    except Exception as exc:
        return None, f"{stock}: {str(exc)[:180]}"

# Header
st.markdown('<div class="desk-hero"><div class="desk-kicker">Indian equities · NIFTY 500</div><h1>📊 RSI + EMA Signal Desk</h1><div class="desk-sub">Sector-wise market scanner · Upstox historical candles · IST timestamps</div></div>', unsafe_allow_html=True)

# Load public stock universe before scanner settings; show a useful dashboard immediately.
with st.spinner("Loading NIFTY 500 constituents and instrument master…"):
    try:
        universe = load_nifty500()
        instrument_map = load_instruments()
        universe["Instrument Key"] = universe.Symbol.map(instrument_map)
        matched = int(universe["Instrument Key"].notna().sum())
        universe = universe.dropna(subset=["Instrument Key"]).reset_index(drop=True)
    except Exception as exc:
        st.error(f"Stock universe load nahi hua: {exc}")
        st.stop()

c1,c2,c3,c4 = st.columns(4)
c1.metric("NIFTY 500 constituents", f"{len(universe)}")
c2.metric("Upstox instruments matched", f"{matched}")
c3.metric("Sectors covered", f"{universe.Sector.nunique()}")
c4.metric("Upstox connection", "Ready" if TOKEN else "Not configured")
if not TOKEN:
    st.warning("Upstox token scanner ke andar enter nahi karna hai. Apne existing scanner ki tarah Streamlit Secrets mein UPSTOX_ACCESS_TOKEN set karo. Local use ke liye .streamlit/secrets.toml; Streamlit Cloud ke liye App → Settings → Secrets.")

with st.sidebar:
    st.title("Scanner Controls")
    mode = st.radio("Choose scanner", ["RSI", "EMA"], horizontal=True)
    timeframe = st.selectbox("Timeframe", TIMEFRAMES, index=1)
    sector_options = ["All Sectors"] + sorted(universe.Sector.dropna().unique().tolist())
    sector = st.selectbox("Sector", sector_options)
    sector_stocks = universe if sector == "All Sectors" else universe[universe.Sector == sector]
    symbols = st.multiselect("Specific stocks (optional)", sorted(sector_stocks.Symbol.tolist()), default=[])
    days_default = 90 if timeframe in ("5m","15m","30m","1H","2H","4H") else 365
    lookback = st.selectbox("Historical lookback", [30,60,90,180,365], index=[30,60,90,180,365].index(days_default))
    horizon = st.selectbox("Win-rate horizon (candles)", [3,5,10,15], index=1)
    max_stocks = st.selectbox("Maximum stocks per scan", [25,50,100,200,300,500], index=5)
    workers = st.select_slider("Parallel requests", options=[1,2,3,4,5], value=3)
    st.divider()
    st.subheader("RSI settings")
    rsi_period = st.number_input("RSI period", min_value=2, max_value=50, value=14)
    rsi_buy = st.number_input("RSI BUY cross above", min_value=5, max_value=50, value=30)
    rsi_sell = st.number_input("RSI SELL cross below", min_value=50, max_value=95, value=70)
    st.divider()
    st.subheader("EMA settings")
    ema_fast = st.number_input("Fast EMA", min_value=2, max_value=100, value=9)
    ema_slow = st.number_input("Slow EMA", min_value=3, max_value=250, value=21)
    confirm50 = st.checkbox("EMA 50 trend confirmation", value=False, disabled=(mode != "EMA"))
    confirm200 = st.checkbox("EMA 200 trend confirmation", value=False, disabled=(mode != "EMA"))
    run = st.button("▶ RUN SCANNER", type="primary", use_container_width=True, disabled=not bool(TOKEN))
    if TOKEN:
        st.markdown('<span class="status-pill">● Token loaded from app secrets</span>', unsafe_allow_html=True)
    else:
        st.caption("Token: not configured in app secrets")

# Main area tabs
tab_overview, tab_scanner, tab_history, tab_method = st.tabs(["Market Overview", "Scanner Results", "Signal History", "Methodology"])
with tab_overview:
    left,right = st.columns([1.2,1])
    with left:
        st.subheader("Sector coverage")
        sector_counts = universe.groupby("Sector").size().sort_values(ascending=True)
        fig = go.Figure(go.Bar(x=sector_counts.values, y=sector_counts.index, orientation="h", marker_color="#3478c9", text=sector_counts.values, textposition="outside"))
        fig.update_layout(height=480, margin=dict(l=5,r=35,t=15,b=15), xaxis_title="Number of stocks", yaxis_title="", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", font=dict(color="#26364f"), xaxis=dict(gridcolor="#e7edf5"))
        st.plotly_chart(fig, use_container_width=True)
    with right:
        st.subheader("Scanner setup")
        st.markdown("""
        **RSI Scanner**  
        RSI(14) BUY when RSI crosses above 30; SELL when RSI crosses below 70.

        **EMA Scanner**  
        BUY when fast EMA crosses above slow EMA; SELL when fast EMA crosses below slow EMA. EMA 50 / EMA 200 confirmation can be enabled.

        **Signal rules**  
        Only completed candles are evaluated. Results are timestamped in Indian Standard Time.
        """)
        st.info("Choose RSI or EMA in the sidebar, select sector and timeframe, then press RUN SCANNER. The token is read from app secrets; it is not typed into the scanner.")
        st.subheader("Sector stock list")
        st.dataframe(universe.groupby("Sector").agg(Stocks=("Symbol","count"), Example=("Symbol",lambda x:", ".join(x.head(4)))).reset_index().sort_values("Stocks",ascending=False), use_container_width=True, hide_index=True, height=310)

if run:
    scoped = sector_stocks.copy()
    if symbols: scoped = scoped[scoped.Symbol.isin(symbols)]
    scoped = scoped.head(max_stocks)
    if scoped.empty:
        st.warning("Selected sector mein stocks nahi mile.")
    else:
        effective_days = lookback
        if timeframe in ("5m","15m"): effective_days = min(lookback,30)
        elif timeframe in ("30m","1H","2H","4H"): effective_days = min(lookback,90)
        if timeframe == "Weekly": effective_days = min(lookback,365)
        st.session_state["last_scan_config"] = {"mode":mode,"sector":sector,"timeframe":timeframe,"days":effective_days}
        st.info(f"Scanning {len(scoped)} stocks · {sector} · {mode} · {timeframe} · {effective_days} days")
        if effective_days != lookback:
            st.warning(f"Upstox V3 historical API ke intraday range ke hisaab se {timeframe} par lookback {effective_days} days kiya gaya hai.")
        progress = st.progress(0); status = st.empty(); results=[]; errors=[]
        work = list(scoped[["Symbol","Sector","Instrument Key"]].itertuples(index=False,name=None))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(scan_stock, sym, sec, key, TOKEN, timeframe, effective_days, mode, horizon, int(rsi_period), float(rsi_buy), float(rsi_sell), int(ema_fast), int(ema_slow), confirm50, confirm200):sym for sym,sec,key in work}
            for i,fut in enumerate(as_completed(futures),1):
                res,err = fut.result()
                if res: results.append(res)
                if err: errors.append(err)
                progress.progress(i/len(futures)); status.write(f"Processed {i}/{len(futures)} stocks")
        all_signals=[]; snapshots=[]; chart_map={}
        for res in results:
            all_signals.extend(res["signals"]); snapshots.append(res["snapshot"]); chart_map[res["snapshot"]["Stock"]]=res["chart"]
        signal_df = pd.DataFrame(all_signals)
        snapshot_df = pd.DataFrame(snapshots)
        if not signal_df.empty:
            signal_df["Signal Time Parsed"] = pd.to_datetime(signal_df["Signal Time"])
            cutoff = datetime.now(IST).replace(tzinfo=None) - timedelta(days=30)
            signal_df = signal_df[signal_df["Signal Time Parsed"] >= cutoff].drop(columns=["Signal Time Parsed"])
            signal_df = signal_df.sort_values("Signal Time",ascending=False).reset_index(drop=True)
        st.session_state["rsi_ema_signals"] = signal_df
        st.session_state["rsi_ema_snapshot"] = snapshot_df
        st.session_state["rsi_ema_charts"] = chart_map
        st.session_state["rsi_ema_scan_mode"] = mode
        st.session_state["rsi_ema_scan_config"] = st.session_state["last_scan_config"]
        st.session_state["rsi_ema_errors"] = errors
        st.success(f"Scan finished. {len(results)} stocks processed; {len(signal_df)} signal events in the last 30 days.")

with tab_scanner:
    cfg = st.session_state.get("rsi_ema_scan_config")
    if cfg is None:
        st.info("Scanner ready. Sidebar se RSI / EMA, sector aur timeframe choose karke RUN SCANNER dabao. Results yahan appear honge.")
    else:
        sig = st.session_state.get("rsi_ema_signals",pd.DataFrame())
        snap = st.session_state.get("rsi_ema_snapshot",pd.DataFrame())
        st.caption(f"Last scan: {cfg['mode']} · {cfg['sector']} · {cfg['timeframe']} · last {cfg['days']} days of source data")
        if not snap.empty:
            buy_n = int((sig.Signal == "BUY").sum()) if not sig.empty else 0
            sell_n = int((sig.Signal == "SELL").sum()) if not sig.empty else 0
            m1,m2,m3,m4 = st.columns(4)
            m1.metric("Stocks scanned",len(snap)); m2.metric("BUY signals",buy_n); m3.metric("SELL signals",sell_n); m4.metric("Stocks with data",snap.Stock.nunique())
            if sig.empty:
                st.warning("Last 30 days mein selected rules ke matching signals nahi mile. Latest indicator snapshot neeche hai.")
            else:
                st.subheader("Latest signal events")
                st.dataframe(sig, use_container_width=True, hide_index=True, height=420)
                st.download_button("⬇ Download signal history CSV",sig.to_csv(index=False).encode("utf-8"),"nifty500_signal_history.csv","text/csv")
            st.subheader("Latest indicator snapshot")
            st.dataframe(snap.sort_values("RSI",ascending=True,na_position="last"),use_container_width=True,hide_index=True,height=360)
            st.download_button("⬇ Download stock snapshot CSV",snap.to_csv(index=False).encode("utf-8"),"nifty500_indicator_snapshot.csv","text/csv")
            if not sig.empty:
                choices = sorted(sig.Stock.unique().tolist())
                chosen = st.selectbox("Chart stock",choices)
                d = st.session_state["rsi_ema_charts"].get(chosen)
                if d is not None and not d.empty:
                    st.subheader(f"{chosen} · Candlestick chart")
                    fig = go.Figure(go.Candlestick(x=d.Datetime,open=d.Open,high=d.High,low=d.Low,close=d.Close,name=chosen))
                    fast_col=f"EMA{int(ema_fast)}"; slow_col=f"EMA{int(ema_slow)}"
                    if fast_col in d: fig.add_trace(go.Scatter(x=d.Datetime,y=d[fast_col],name=fast_col,line=dict(width=1.5,color="#1c8b58")))
                    if slow_col in d: fig.add_trace(go.Scatter(x=d.Datetime,y=d[slow_col],name=slow_col,line=dict(width=1.5,color="#e28b2c")))
                    fig.update_layout(height=500,xaxis_rangeslider_visible=False,paper_bgcolor="#fff",plot_bgcolor="#fff",font=dict(color="#26364f"),margin=dict(l=10,r=10,t=20,b=10))
                    st.plotly_chart(fig,use_container_width=True)
        errs=st.session_state.get("rsi_ema_errors",[])
        if errs:
            with st.expander(f"Skipped stocks / API errors ({len(errs)})"):
                st.code("\n".join(errs[:80]))

with tab_history:
    sig = st.session_state.get("rsi_ema_signals",pd.DataFrame())
    if sig.empty:
        st.info("Scan run karne ke baad pichhle 30 din ke RSI / EMA signal events yahan milenge.")
    else:
        st.subheader("Past 30 days signal history")
        mode_filter = st.selectbox("Signal type",["All", "BUY", "SELL"],key="history_side")
        hist = sig if mode_filter == "All" else sig[sig.Signal == mode_filter]
        st.dataframe(hist,use_container_width=True,hide_index=True,height=520)

with tab_method:
    st.subheader("Signal & historical win-rate methodology")
    st.markdown("""
    - **RSI BUY:** RSI(14) previous completed candle par BUY threshold ke neeche / equal tha aur latest completed candle par threshold ke upar gaya.
    - **RSI SELL:** RSI(14) previous completed candle par SELL threshold ke upar / equal tha aur latest completed candle par threshold ke neeche aaya.
    - **EMA BUY / SELL:** Fast EMA slow EMA ko upward / downward cross kare.
    - **Trend filters:** EMA 50 aur EMA 200 confirmations independently enable kiye ja sakte hain.
    - **Historical win-rate:** Signal candle ke baad next candle open ko entry aur selected horizon candle close ko exit maana jata hai. BUY mein exit > entry aur SELL mein exit < entry ko win maana gaya. Brokerage, slippage, stop-loss / target include nahi hain.
    - **Important:** Historical hit-rate estimate hai, future performance ki guarantee nahi. Sample size kam ho toh percentage ko reliable na maanein.
    """)

st.divider()
st.markdown('<div class="small-note">Market data: Upstox API · Stock universe: NIFTY 500 constituents · All displayed timestamps: Asia/Kolkata (IST). API limits and history availability depend on your Upstox plan.</div>',unsafe_allow_html=True)
