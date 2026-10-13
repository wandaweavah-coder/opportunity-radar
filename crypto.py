"""Crypto Forecaster - established coins on major exchanges (free public data, no API keys).
For every coin: recent move, a statistical forecast (expected move + 80% range + chance of up), pattern signals with
levels, and an honest check of whether the forecast beat a naive guess in past tests. Not financial advice."""
import env  # loads settings.env
import os, sqlite3, time, math, requests, datetime as dt
import numpy as np, pandas as pd

DB = os.getenv("RADAR_DB", "radar.db")
H = int(os.getenv("FORECAST_DAYS", 7))
COINS = [c.strip().upper() for c in os.getenv(
    "CRYPTO_COINS", "BTC,ETH,BNB,XRP,SOL,ADA,DOGE,TRX,AVAX,LINK,DOT,LTC,BCH,XLM,ATOM,NEAR,UNI,ETC").split(",") if c.strip()]
UA = {"User-Agent": "opportunity-radar/0.2 (personal research)"}
FEE = float(os.getenv("CRYPTO_FEE_PCT", 0.1)) / 100   # trading cost per side used in the backtest
KRAKEN_NAMES = {"BTC": "XBT", "DOGE": "XDG"}
BIAS = {"Breakout: new 20-day high on strong volume": "bullish", "Volatility squeeze (direction unknown)": "neutral",
        "Trend cross up (20/50 EMA)": "bullish", "Trend cross down (20/50 EMA)": "bearish",
        "Oversold bounce (RSI<30, up day)": "bullish", "Overbought (RSI>75)": "bearish",
        "Overextended: >25% above 50-day average": "bearish"}

# ---------------- data (tries Binance, then Kraken, then Coinbase; all public, no key) ----------------
def _frame(rows):
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"]).astype(float)
    df["date"] = pd.to_datetime(df["t"], unit="s")
    return df.drop(columns="t").sort_values("date").reset_index(drop=True)

def _binance(sym):
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            r = requests.get(host + "/api/v3/klines", headers=UA, timeout=20,
                             params={"symbol": sym + "USDT", "interval": "1d", "limit": 1000})
            if r.status_code == 200:
                return _frame([[x[0] / 1000, x[1], x[2], x[3], x[4], x[5]] for x in r.json()])
        except Exception:
            pass
    return None

def _kraken(sym):
    j = requests.get("https://api.kraken.com/0/public/OHLC", headers=UA, timeout=20,
                     params={"pair": KRAKEN_NAMES.get(sym, sym) + "USD", "interval": 1440}).json()
    if j.get("error"): return None
    rows = j["result"][[k for k in j["result"] if k != "last"][0]]
    return _frame([[x[0], x[1], x[2], x[3], x[4], x[6]] for x in rows])

def _coinbase(sym):
    r = requests.get(f"https://api.exchange.coinbase.com/products/{sym}-USD/candles", headers=UA, timeout=20,
                     params={"granularity": 86400})
    return _frame([[x[0], x[3], x[2], x[1], x[4], x[5]] for x in r.json()]) if r.status_code == 200 else None

def get_candles(sym):
    for name, fn in (("Binance", _binance), ("Kraken", _kraken), ("Coinbase", _coinbase)):
        try:
            df = fn(sym)
        except Exception:
            df = None
        if df is not None and len(df) >= 200:
            return name, df
    return None

# ---------------- features, model, honest evaluation ----------------
def _rsi(c, n=14):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / (dn + 1e-12))

def make_features(df):
    c, v = df["close"], df["volume"].replace(0, np.nan)
    f = pd.DataFrame(index=df.index)
    for n in (1, 3, 7, 14, 30): f[f"r{n}"] = np.log(c / c.shift(n))
    f["vol20"] = np.log(c).diff().rolling(20).std()
    f["rsi"] = _rsi(c) / 100 - 0.5
    f["d20"] = np.log(c / c.rolling(20).mean()); f["d50"] = np.log(c / c.rolling(50).mean())
    f["volr"] = np.log(v / v.rolling(20).mean())
    return f.replace([np.inf, -np.inf], np.nan)

def ridge_fit(X, y, lam=30.0):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return mu, sd, w, y.mean()

def ridge_pred(m, X):
    mu, sd, w, b = m
    return ((X - mu) / sd) @ w + b

def walk_forward(F, y, start=250, step=20):
    """Out-of-sample forecasts: a forecast at day t only learns from days whose outcome was already known by t."""
    n, preds = len(F), np.full(len(F), np.nan)
    for t0 in range(start, n, step):
        tr = np.arange(0, t0 - H + 1)
        tr = tr[~np.isnan(y[tr]) & ~np.isnan(F[tr]).any(1)]
        if len(tr) < 120: continue
        m = ridge_fit(F[tr], y[tr])
        te = np.arange(t0, min(t0 + step, n)); te = te[~np.isnan(F[te]).any(1)]
        preds[te] = ridge_pred(m, F[te])
    return preds

def evaluate(preds, y):
    ok = ~np.isnan(preds) & ~np.isnan(y)
    if ok.sum() < 100: return None
    p, a = preds[ok], y[ok]
    base_up = float(np.mean(a > 0))
    return dict(n=int(ok.sum()), dir_acc=float(np.mean(np.sign(p) == np.sign(a))), base_acc=max(base_up, 1 - base_up),
                edge=float(1 - np.mean(np.abs(a - p)) / np.mean(np.abs(a))), resid=a - p)

def verdict(ev):
    if ev is None: return "Not enough history"
    if ev["edge"] > 0.03 and ev["dir_acc"] > ev["base_acc"] + 0.03: return "Edge in past tests (no guarantee)"
    if ev["edge"] > 0 and ev["dir_acc"] > ev["base_acc"]: return "Weak edge"
    return "No edge shown - treat as noise"

def forecast(df):
    if len(df) < 400: return dict(label="Not enough history")
    F, c = make_features(df).values, df["close"].values
    y = np.append(np.log(c[H:] / c[:-H]), [np.nan] * H)
    ev = evaluate(walk_forward(F, y), y)
    known = np.where(~np.isnan(y) & ~np.isnan(F).any(1))[0]
    if len(known) < 150 or np.isnan(F[-1]).any(): return dict(label="Not enough history")
    m = ridge_fit(F[known], y[known])
    p = float(ridge_pred(m, F[-1:])[0])
    res = ev["resid"] if ev and len(ev["resid"]) >= 100 else y[known] - ridge_pred(m, F[known])
    lo, hi = np.quantile(p + res, [0.10, 0.90])
    return dict(exp=math.exp(p) - 1, lo=math.exp(lo) - 1, hi=math.exp(hi) - 1, prob_up=float(np.mean(p + res > 0)),
                ev=ev, label=verdict(ev))

# ---------------- patterns ----------------
def pattern_flags(df, dedupe=True):
    c, v = df["close"], df["volume"].replace(0, np.nan)
    ema = lambda s, n: s.ewm(span=n, adjust=False).mean()
    e20, e50, rsi = ema(c, 20), ema(c, 50), _rsi(c)
    vol20 = np.log(c).diff().rolling(20).std()
    pct = vol20.rolling(120).apply(lambda a: (a <= a[-1]).mean(), raw=True)
    f = pd.DataFrame(index=df.index)
    f["Breakout: new 20-day high on strong volume"] = (c > c.shift(1).rolling(20).max()) & (v / v.rolling(20).mean() > 1.5)
    f["Volatility squeeze (direction unknown)"] = pct <= 0.10
    f["Trend cross up (20/50 EMA)"] = (e20 > e50) & (e20.shift(1) <= e50.shift(1))
    f["Trend cross down (20/50 EMA)"] = (e20 < e50) & (e20.shift(1) >= e50.shift(1))
    f["Oversold bounce (RSI<30, up day)"] = (rsi < 30) & (c > c.shift(1))
    f["Overbought (RSI>75)"] = rsi > 75
    f["Overextended: >25% above 50-day average"] = c > 1.25 * c.rolling(50).mean()
    f = f.fillna(False).astype(bool)
    if dedupe:   # count a pattern once, not on every consecutive day
        prev = f.astype(int).shift(1).rolling(5, min_periods=1).max().fillna(0).astype(bool)
        f = f & ~prev
    return f

def levels(df):
    atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
    return float(df["low"].iloc[-10:].min()), float(df["close"].iloc[-20:].max() - 2 * atr)

def pattern_stats(frames):
    """What actually followed each pattern in history, across every coin tested."""
    acc = {}
    def add(name, f7, f30, g90):
        a = acc.setdefault(name, [[], [], []])
        a[0] += list(f7); a[1] += list(f30); a[2] += list(g90)
    for sym, df in frames.items():
        c = pd.Series(df["close"].values)
        f7, f30 = (c.shift(-7) / c - 1).values, (c.shift(-30) / c - 1).values
        g90 = (c.iloc[::-1].rolling(90).max().iloc[::-1].shift(-1) / c - 1).values
        add("All days (no signal)", f7[~np.isnan(f7)], f30[~np.isnan(f30)], g90[~np.isnan(g90)])
        fl = pattern_flags(df)
        for name in fl.columns:
            ix = np.where(fl[name].values)[0]
            add(name, [x for x in f7[ix] if not np.isnan(x)], [x for x in f30[ix] if not np.isnan(x)], [x for x in g90[ix] if not np.isnan(x)])
    rows = []
    for name, (a7, a30, g) in acc.items():
        a7, a30, g = np.array(a7), np.array(a30), np.array(g)
        if len(a7) < 5: continue
        rows.append((name, len(a7), float(np.median(a7)), float(np.mean(a7 > 0)), float(np.mean(a7 >= .10)), float(np.mean(a7 <= -.10)),
                     float(np.median(a30)) if len(a30) else None, float(np.mean(g >= 1.0)) if len(g) else None,
                     float(np.mean(g >= 9.0)) if len(g) else None))
    return rows

# ---------------- early entry / early exit points ----------------
def signals(df):
    """Early ENTRY = price turns up through its 10-day average while momentum and volume confirm and the move is still young
    (price still within four average daily ranges of its 10-day low). Early EXIT = price turns down through that average, or closes under a volatility stop."""
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"].replace(0, np.nan)
    e10, e30 = c.ewm(span=10, adjust=False).mean(), c.ewm(span=30, adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    atr = (h - l).rolling(14).mean()
    young = c < l.rolling(10).min() + 4 * atr
    cross_up = (c > e10) & (c.shift(1) <= e10.shift(1))
    entry = (cross_up & (e10 > e10.shift(3)) & (hist > hist.shift(1)) & (v / v.rolling(20).mean() >= 0.8) & young).fillna(False)
    chand = c.rolling(10).max() - 2.5 * atr
    cross_dn = (c < e10) & (c.shift(1) >= e10.shift(1)) & (e10 < e10.shift(3))
    exit_ev = (cross_dn | ((c < chand.shift(1)) & (c.shift(1) >= chand.shift(2)))).fillna(False)
    return dict(e10=e10, e30=e30, atr=atr, low7=l.rolling(7).min(), entry=entry, exit_ev=exit_ev, chand=chand)

def trigger_odds(df, entry_lvl, exit_lvl, n=2000):
    """Chance price closes beyond each trigger within H days, from simulated paths that reuse the last year of daily
    moves with the drift removed (so it assumes normal volatility and no trend, not a prediction of direction)."""
    price = float(df["close"].iloc[-1])
    r = np.log(df["close"]).diff().dropna().values[-365:]
    r = r - r.mean()
    paths = price * np.exp(np.cumsum(np.random.default_rng(0).choice(r, size=(n, H)), axis=1))
    p_in = float(np.mean(paths.max(1) >= entry_lvl)) if entry_lvl and entry_lvl > price else None
    p_out = float(np.mean(paths.min(1) <= exit_lvl)) if exit_lvl and exit_lvl < price else None
    return p_in, p_out

def early_points(df):
    s = signals(df)
    price, e10, e30 = float(df["close"].iloc[-1]), float(s["e10"].iloc[-1]), float(s["e30"].iloc[-1])
    atr = float(s["atr"].iloc[-1])
    ent, ext = bool(s["entry"].iloc[-2:].any()), bool(s["exit_ev"].iloc[-2:].any())
    stop_if_entry = float(max(s["low7"].iloc[-1], price - 3 * atr))
    exit_lvl = float(s["chand"].iloc[-1])
    if ent and not ext: status, exit_lvl = "EARLY ENTRY signal", stop_if_entry
    elif ext: status, exit_lvl = "EARLY EXIT signal", None
    else:
        status = ("Uptrend underway (entering now would be chasing)" if price > e10 > e30 else
                  "Above short average, no confirmed entry" if price > e10 else "Waiting (below short average)")
    entry_lvl = e10 if price <= e10 else None
    if exit_lvl is not None and exit_lvl >= price: exit_lvl = None
    p_in, p_out = trigger_odds(df, entry_lvl, exit_lvl)
    return dict(status=status, entry_level=entry_lvl, exit_level=exit_lvl, p_entry=p_in, p_exit=p_out, stop_if_entry=stop_if_entry)

def simulate(df, fee=None):
    """Honest test of the entry/exit rules on past data: buy at the NEXT day's open after an entry signal, exit on a stop or an
    exit signal, pay fees both ways, then compare with just holding. Returns trades, win rate, avg trade, strategy and
    hold returns, their worst drawdowns, and the share of days spent in the market."""
    fee = FEE if fee is None else fee
    s = signals(df)
    o, l, c = (df[k].values for k in ("open", "low", "close"))
    entry, e10, atr, low7 = s["entry"].values, s["e10"].values, s["atr"].values, s["low7"].values
    n, start = len(c), 60
    cash, pos, ep, stop, eq0, eq, trades, days_in = 1.0, False, 0.0, 0.0, 1.0, np.ones(len(c)), [], 0
    for t in range(start, n):
        if pos:
            days_in += 1; exit_px = None
            if l[t] <= stop: exit_px = min(o[t], stop)
            else:
                stop = max(stop, c[t] - 2.5 * atr[t])
                if c[t] < e10[t] and e10[t] < e10[t - 3] and t + 1 < n: exit_px = o[t + 1]
            if exit_px is not None:
                r = exit_px / ep * (1 - fee) ** 2 - 1
                cash, pos, eq[t] = eq0 * (1 + r), False, eq0 * (1 + r); trades.append(r)
            else: eq[t] = eq0 * (1 - fee) * c[t] / ep
        else:
            eq[t] = cash
            if entry[t] and t + 1 < n and not np.isnan(atr[t]):
                pos, ep, eq0, stop = True, o[t + 1], cash, max(low7[t], c[t] - 3 * atr[t])
    dd = lambda x: float(np.min(x / np.maximum.accumulate(x) - 1))
    e, hold = eq[start:], c[start:] / c[start]
    wins = [r for r in trades if r > 0]
    return (len(trades), len(wins) / len(trades) if trades else None, float(np.mean(trades)) if trades else None,
            float(e[-1] - 1), float(hold[-1] - 1), dd(e), dd(hold), days_in / max(1, n - start))

# ---------------- run ----------------
def main():
    db = sqlite3.connect(DB)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS crypto_forecast(ts TEXT, symbol TEXT, source TEXT, price REAL, chg_24h REAL, chg_7d REAL, horizon INT,
        exp_pct REAL, lo_pct REAL, hi_pct REAL, prob_up REAL, dir_acc REAL, base_acc REAL, edge REAL, n_oos INT, label TEXT,
        patterns TEXT, fail_level REAL, trail_level REAL);
    CREATE TABLE IF NOT EXISTS crypto_prices(symbol TEXT, date TEXT, close REAL);
    CREATE TABLE IF NOT EXISTS crypto_signals(ts TEXT, symbol TEXT, pattern TEXT, bias TEXT, price REAL, fail_level REAL, trail_level REAL);
    CREATE TABLE IF NOT EXISTS crypto_pattern_stats(ts TEXT, pattern TEXT, n INT, med7 REAL, up7 REAL, big_up7 REAL, big_dn7 REAL,
        med30 REAL, gain2x90 REAL, gain10x90 REAL);
    CREATE TABLE IF NOT EXISTS crypto_points(ts TEXT, symbol TEXT, status TEXT, price REAL, entry_level REAL, exit_level REAL,
        p_entry REAL, p_exit REAL, stop_if_entry REAL);
    CREATE TABLE IF NOT EXISTS crypto_rule_backtest(ts TEXT, symbol TEXT, trades INT, win_rate REAL, avg_trade REAL, strat_ret REAL,
        hold_ret REAL, strat_dd REAL, hold_dd REAL, in_market REAL);""")
    now = dt.datetime.utcnow(); ts, today = now.isoformat(timespec="minutes"), pd.Timestamp(now.date())
    frames, new_sigs, bt_rows = {}, [], []
    for sym in COINS:
        got = get_candles(sym); time.sleep(0.6)
        if not got:
            print(f"{sym}: no data from any exchange"); continue
        src, full = got
        df = full[full["date"] < today].reset_index(drop=True)      # model on completed days only
        if len(df) < 60: continue
        frames[sym] = df
        price = float(full["close"].iloc[-1])
        ch24, ch7 = price / float(full["close"].iloc[-2]) - 1, price / float(full["close"].iloc[-8]) - 1
        fc = forecast(df); ev = fc.get("ev") or {}
        active = [n for n, col in pattern_flags(df, dedupe=False).items() if col.iloc[-2:].any()]
        fail, trail = levels(df)
        db.execute("INSERT INTO crypto_forecast VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (ts, sym, src, price, ch24, ch7, H, fc.get("exp"), fc.get("lo"), fc.get("hi"), fc.get("prob_up"),
                    ev.get("dir_acc"), ev.get("base_acc"), ev.get("edge"), ev.get("n"), fc["label"], "|".join(active), fail, trail))
        pt = early_points(df)
        prev = db.execute("SELECT status FROM crypto_points WHERE symbol=? ORDER BY ts DESC LIMIT 1", (sym,)).fetchone()
        db.execute("INSERT INTO crypto_points VALUES (?,?,?,?,?,?,?,?,?)", (ts, sym, pt["status"], price, pt["entry_level"], pt["exit_level"],
                                                                         pt["p_entry"], pt["p_exit"], pt["stop_if_entry"]))
        if pt["status"].startswith("EARLY") and (not prev or prev[0] != pt["status"]): new_sigs.append(f"{sym}: {pt['status']}")
        bt_rows.append((sym,) + simulate(df))
        db.execute("DELETE FROM crypto_prices WHERE symbol=?", (sym,))
        db.executemany("INSERT INTO crypto_prices VALUES (?,?,?)",
                       [(sym, str(d.date()), float(x)) for d, x in zip(full["date"].iloc[-180:], full["close"].iloc[-180:])])
        cutoff = (now - dt.timedelta(days=3)).isoformat(timespec="minutes")
        for n in active:
            if not db.execute("SELECT 1 FROM crypto_signals WHERE symbol=? AND pattern=? AND ts>?", (sym, n, cutoff)).fetchone():
                db.execute("INSERT INTO crypto_signals VALUES (?,?,?,?,?,?,?)", (ts, sym, n, BIAS[n], price, fail, trail))
                new_sigs.append(f"{sym}: {n}")
        e = f"{fc['exp']:+.1%} ({fc['lo']:+.1%} to {fc['hi']:+.1%})" if "exp" in fc else "n/a"
        print(f"{sym:5} {src:8} {price:>12,.4g} | 24h {ch24:+.1%} 7d {ch7:+.1%} | next {H}d {e} | {fc['label']} | {pt['status']}")
        db.commit()
    if frames:
        db.execute("DELETE FROM crypto_pattern_stats")
        db.executemany("INSERT INTO crypto_pattern_stats VALUES (?,?,?,?,?,?,?,?,?,?)", [(ts,) + r for r in pattern_stats(frames)])
        db.execute("DELETE FROM crypto_rule_backtest")
        allrow = ("ALL (median of coins)",) + tuple(
            (float(np.median([r[i] for r in bt_rows if r[i] is not None])) if any(r[i] is not None for r in bt_rows) else None) for i in range(1, 9))
        db.executemany("INSERT INTO crypto_rule_backtest VALUES (?,?,?,?,?,?,?,?,?,?)", [(ts,) + r for r in bt_rows + [allrow]])
        db.commit()
    if new_sigs:
        from radar import alert
        alert("Crypto signals (educational, not advice):\n" + "\n".join(new_sigs[:10]))
    print(f"Done: {len(frames)} coins.")

if __name__ == "__main__":
    main()
