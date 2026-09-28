import io, time, html
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0"}
SCRIP_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
N500_URLS = [
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
DEFAULTS = dict(ema_fast=20, ema_slow=50, rsi_min=55, brk_days=20, vol_mult=1.5,
                atr_mult=1.5, rr=2.0, buy_score=4, watch_score=3)


# ---------------- Indicators & scoring ----------------
def score_frame(df, p):
    df = df.copy()
    c, h, l, v = df.close, df.high, df.low, df.volume
    ema_f = c.ewm(span=p["ema_fast"], adjust=False).mean()
    ema_s = c.ewm(span=p["ema_slow"], adjust=False).mean()
    d = c.diff()
    rs = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean() / (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + rs)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    df["c1"] = ema_f > ema_s
    df["c2"] = df.rsi > p["rsi_min"]
    df["c3"] = macd > sig
    df["c4"] = c > h.rolling(p["brk_days"]).max().shift(1)
    df["c5"] = v > p["vol_mult"] * v.rolling(20).mean().shift(1)
    df["score"] = df[["c1", "c2", "c3", "c4", "c5"]].sum(axis=1)
    return df


def labels(p):
    return [f"EMA{p['ema_fast']} > EMA{p['ema_slow']}", f"RSI > {p['rsi_min']}",
            "MACD bullish", f"{p['brk_days']}-day breakout", "Volume spike"]


def scan_one(sym, df, p):
    if df is None or len(df) < max(p["ema_slow"], 60) + 5:
        return None
    r = score_frame(df, p).iloc[-1]
    sc = int(r.score)
    if sc < p["watch_score"] or np.isnan(r.atr):
        return None
    risk = p["atr_mult"] * r.atr
    e = float(r.close)
    reasons = [t for t, k in zip(labels(p), ["c1", "c2", "c3", "c4", "c5"]) if r[k]]
    return dict(Symbol=sym, Signal="BUY" if sc >= p["buy_score"] else "WATCH", Score=sc,
                Entry=round(e, 2), StopLoss=round(e - risk, 2),
                Target=round(e + p["rr"] * risk, 2), Reasons=", ".join(reasons))


# ---------------- Data: universe + candles ----------------
def load_universe():
    n5 = None
    for u in N500_URLS:
        try:
            r = requests.get(u, headers=UA, timeout=30)
            r.raise_for_status()
            n5 = pd.read_csv(io.StringIO(r.text))
            break
        except Exception:
            continue
    if n5 is None:
        n5 = pd.read_csv("nifty500.csv")  # fallback: repo me file rakh do (Symbol column)
    n5 = n5[["Symbol"]].drop_duplicates()
    sm = pd.DataFrame(requests.get(SCRIP_URL, timeout=60).json())
    sm = sm[(sm.exch_seg == "NSE") & sm.symbol.str.endswith("-EQ")][["name", "token"]]
    sm = sm.drop_duplicates("name")
    return n5.merge(sm, left_on="Symbol", right_on="name", how="left").drop(columns="name")


def angel_login(cfg):
    import pyotp
    from SmartApi import SmartConnect
    api = SmartConnect(api_key=cfg["ANGEL_API_KEY"])
    r = api.generateSession(cfg["ANGEL_CLIENT_ID"], cfg["ANGEL_PIN"],
                            pyotp.TOTP(cfg["ANGEL_TOTP_SECRET"]).now())
    if not r.get("status"):
        raise RuntimeError(f"Angel login fail: {r.get('message')}")
    return api


def fetch_angel(api, token, days=400):
    to = datetime.now()
    fr = to - timedelta(days=days)
    for _ in range(3):
        try:
            r = api.getCandleData({"exchange": "NSE", "symboltoken": str(token), "interval": "ONE_DAY",
                                   "fromdate": fr.strftime("%Y-%m-%d 09:15"),
                                   "todate": to.strftime("%Y-%m-%d 15:30")})
            if r.get("data"):
                df = pd.DataFrame(r["data"], columns=["date", "open", "high", "low", "close", "volume"])
                df["date"] = pd.to_datetime(df.date).dt.tz_localize(None)
                return df.set_index("date").astype(float)
        except Exception:
            pass
        time.sleep(1.5)
    return None


def fetch_yf(sym, days=400):
    import yfinance as yf
    try:
        df = yf.download(f"{sym}.NS", period=f"{max(days // 30, 3)}mo", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.columns = [str(c).lower() for c in df.columns]
        return df[["open", "high", "low", "close", "volume"]].dropna()
    except Exception:
        return None


def get_data(sym, token, source, api=None, days=400):
    if source == "Angel One" and api is not None and pd.notna(token):
        df = fetch_angel(api, token, days)
        time.sleep(0.4)  # rate limit
        return df
    return fetch_yf(sym, days)


def scan_all(uni, p, source, api=None, progress=None):
    out = []
    for i, row in enumerate(uni.itertuples(), 1):
        res = scan_one(row.Symbol, get_data(row.Symbol, row.token, source, api), p)
        if res:
            out.append(res)
        if progress:
            progress(i / len(uni), f"{i}/{len(uni)} {row.Symbol}")
    if not out:
        return pd.DataFrame(columns=["Symbol", "Signal", "Score", "Entry", "StopLoss", "Target", "Reasons"])
    return pd.DataFrame(out).sort_values(["Score", "Symbol"], ascending=[False, True]).reset_index(drop=True)


# ---------------- Backtest ----------------
def backtest_symbol(sym, df, p, min_score=4, hold=15, cost_pct=0.1):
    s = score_frame(df, p)
    o, h, l, c = s.open.values, s.high.values, s.low.values, s.close.values
    atr, sc, idx = s.atr.values, s.score.values, s.index
    n, i, trades = len(s), max(p["ema_slow"], 60), []
    while i < n - 1:
        if sc[i] >= min_score and not np.isnan(atr[i]):
            entry = o[i + 1]
            risk = p["atr_mult"] * atr[i]
            sl, tg = entry - risk, entry + p["rr"] * risk
            px, why, j = c[min(i + hold, n - 1)], "TIME", min(i + hold, n - 1)
            for k in range(i + 1, min(i + 1 + hold, n)):
                if l[k] <= sl:          # conservative: SL pehle check
                    px, why, j = min(sl, o[k]), "SL", k
                    break
                if h[k] >= tg:
                    px, why, j = max(tg, o[k]), "TARGET", k
                    break
            ret = (px / entry - 1) * 100 - cost_pct
            trades.append(dict(Symbol=sym, EntryDate=idx[i + 1], ExitDate=idx[j], Entry=round(entry, 2),
                               Exit=round(px, 2), Result=why, ReturnPct=round(ret, 2)))
            i = j + 1
        else:
            i += 1
    return trades


def summarize(trades, capital=100000, alloc_pct=10):
    if trades.empty:
        return {}, pd.Series(dtype=float)
    t = trades.sort_values("ExitDate")
    r = t.ReturnPct / 100
    wins, losses = r[r > 0], r[r <= 0]
    eq = capital * (1 + r * alloc_pct / 100).cumprod()
    dd = (eq / eq.cummax() - 1).min() * 100
    stats = {
        "Trades": len(t),
        "Win rate %": round(len(wins) / len(t) * 100, 1),
        "Avg return %": round(r.mean() * 100, 2),
        "Avg win %": round(wins.mean() * 100, 2) if len(wins) else 0,
        "Avg loss %": round(losses.mean() * 100, 2) if len(losses) else 0,
        "Profit factor": round(wins.sum() / abs(losses.sum()), 2) if losses.sum() != 0 else float("inf"),
        "Max drawdown %": round(dd, 1),
        "Final equity": int(eq.iloc[-1]),
    }
    return stats, pd.Series(eq.values, index=t.ExitDate.values)


# ---------------- Telegram ----------------
def format_msg(df, top=15):
    today = datetime.now().strftime("%d-%b-%Y")
    if df.empty:
        return f"Nifty500 Swing Scan ({today}): koi signal nahi mila."
    buy = df[df.Signal == "BUY"].head(top)
    lines = [f"Nifty500 Swing Scan ({today})", f"BUY: {(df.Signal == 'BUY').sum()} | WATCH: {(df.Signal == 'WATCH').sum()}", ""]
    for r in buy.itertuples():
        lines.append(f"{r.Symbol} (Score {r.Score})\nEntry {r.Entry} | SL {r.StopLoss} | TGT {r.Target}\n{r.Reasons}\n")
    if buy.empty:
        lines.append("Aaj koi BUY signal nahi, sirf WATCH list.")
    return "\n".join(lines)


def tg_send(token, chat_id, text):
    for i in range(0, len(text), 3800):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text[i:i + 3800]}, timeout=20)
        r.raise_for_status()
