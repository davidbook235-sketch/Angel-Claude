import os
import pandas as pd
import streamlit as st
import core

st.set_page_config(page_title="Nifty500 Swing Scanner", layout="wide")
st.title("📈 Nifty500 Swing Scanner")


def sec(k):
    try:
        return st.secrets[k]
    except Exception:
        return os.environ.get(k, "")


CFG = {k: sec(k) for k in ["ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PIN", "ANGEL_TOTP_SECRET",
                           "TG_TOKEN", "TG_CHAT_ID"]}

# ---------- Sidebar: parameters ----------
with st.sidebar:
    st.header("Settings")
    source = st.radio("Data source", ["Angel One", "Yahoo (yfinance)"])
    p = dict(core.DEFAULTS)
    p["ema_fast"] = st.number_input("EMA fast", 5, 50, 20)
    p["ema_slow"] = st.number_input("EMA slow", 20, 200, 50)
    p["rsi_min"] = st.number_input("RSI min", 40, 80, 55)
    p["brk_days"] = st.number_input("Breakout days", 5, 60, 20)
    p["vol_mult"] = st.number_input("Volume spike x", 1.0, 5.0, 1.5, 0.1)
    p["atr_mult"] = st.number_input("SL = ATR x", 0.5, 4.0, 1.5, 0.1)
    p["rr"] = st.number_input("Risk:Reward", 1.0, 5.0, 2.0, 0.5)
    p["buy_score"] = st.slider("BUY min score", 3, 5, 4)
    p["watch_score"] = st.slider("WATCH min score", 2, 5, 3)


@st.cache_data(ttl=86400, show_spinner="Nifty500 list load ho rahi hai...")
def universe():
    return core.load_universe()


def get_api():
    if source != "Angel One":
        return None
    if "api" not in st.session_state:
        st.session_state.api = core.angel_login(CFG)
    return st.session_state.api


tab1, tab2 = st.tabs(["🔍 Scanner", "🧪 Backtest"])

# ---------- Scanner ----------
with tab1:
    uni = universe()
    st.caption(f"Universe: {len(uni)} stocks")
    limit = st.number_input("Kitne stocks scan karne hain (test ke liye chhota rakho)", 5, len(uni), len(uni))
    if st.button("Scan chalao", type="primary", use_container_width=True):
        try:
            api = get_api()
            bar = st.progress(0.0)
            st.session_state.res = core.scan_all(uni.head(int(limit)), p, source, api,
                                                 lambda f, t: bar.progress(f, text=t))
            bar.empty()
        except Exception as e:
            st.error(f"Error: {e}")
    res = st.session_state.get("res")
    if res is not None:
        st.success(f"BUY: {(res.Signal == 'BUY').sum()} | WATCH: {(res.Signal == 'WATCH').sum()}")
        st.dataframe(res, use_container_width=True, hide_index=True)
        st.download_button("CSV download", res.to_csv(index=False), "scan.csv", use_container_width=True)
        if st.button("📨 Telegram pe bhejo", use_container_width=True):
            try:
                core.tg_send(CFG["TG_TOKEN"], CFG["TG_CHAT_ID"], core.format_msg(res))
                st.success("Bhej diya ✅")
            except Exception as e:
                st.error(f"Telegram error: {e}")

# ---------- Backtest ----------
with tab2:
    c1, c2 = st.columns(2)
    n_stocks = c1.number_input("Stocks (Nifty500 ke pehle N)", 5, 500, 30)
    years = c2.number_input("Saal", 1, 5, 2)
    c3, c4 = st.columns(2)
    hold = c3.number_input("Max hold (days)", 3, 60, 15)
    cost = c4.number_input("Round-trip cost %", 0.0, 1.0, 0.1, 0.05)
    min_sc = st.slider("Entry min score", 3, 5, p["buy_score"], key="bt_sc")
    alloc = st.slider("Position size % of capital", 1, 25, 10)
    if st.button("Backtest chalao", type="primary", use_container_width=True):
        try:
            api = get_api()
            u, trades, bar = universe().head(int(n_stocks)), [], st.progress(0.0)
            for i, r in enumerate(u.itertuples(), 1):
                df = core.get_data(r.Symbol, r.token, source, api, int(years * 365 + 120))
                if df is not None and len(df) > 80:
                    trades += core.backtest_symbol(r.Symbol, df, p, min_sc, int(hold), cost)
                bar.progress(i / len(u), text=f"{i}/{len(u)} {r.Symbol}")
            bar.empty()
            tdf = pd.DataFrame(trades)
            if tdf.empty:
                st.warning("Koi trade nahi bana.")
            else:
                stats, eq = core.summarize(tdf, 100000, alloc)
                cols = st.columns(2)
                for i, (k, v) in enumerate(stats.items()):
                    cols[i % 2].metric(k, v)
                st.line_chart(eq)
                st.dataframe(tdf.sort_values("ExitDate", ascending=False), use_container_width=True, hide_index=True)
                st.download_button("Trades CSV", tdf.to_csv(index=False), "trades.csv", use_container_width=True)
        except Exception as e:
            st.error(f"Error: {e}")
                                                                   
