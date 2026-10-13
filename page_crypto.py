import sqlite3
import numpy as np, pandas as pd, streamlit as st
from crypto import DB, H, BIAS


def render():

    st.title("Crypto Forecaster - established coins")
    st.warning("Educational tool, not financial advice. Forecasts are statistical estimates with wide ranges, and most coins show "
               "no edge over a naive guess in past tests. Never risk money you cannot afford to lose.")
    con = sqlite3.connect(DB)
    try:
        fc = pd.read_sql("SELECT * FROM crypto_forecast", con)
    except Exception:
        fc = pd.DataFrame()
    if fc.empty:
        st.info("No data yet. Run `python crypto.py` (needs internet) or double-click scan.bat."); return

    fc["ts_dt"] = pd.to_datetime(fc.ts)
    latest = fc[fc.ts == fc.ts.max()].copy()
    st.caption(f"Last scan (UTC): {fc.ts.max()}. Forecast horizon: {H} days. Daily candles from public exchange data.")
    pct = lambda c: st.column_config.NumberColumn(c, format="%.1f%%")
    tabs = st.tabs(["Forecasts", "Early entry / exit", "Pattern signals", "Pattern history", "Scorecard"])

    with tabs[0]:
        t = latest.copy()
        for col in ("chg_24h", "chg_7d", "exp_pct", "lo_pct", "hi_pct", "prob_up"): t[col] = t[col] * 100
        t["direction"] = np.where(t.exp_pct.isna(), "n/a", np.where(t.exp_pct > 0, "Up", "Down"))
        t = t.sort_values("symbol")
        st.dataframe(t[["symbol", "price", "chg_24h", "chg_7d", "direction", "exp_pct", "lo_pct", "hi_pct", "prob_up", "label"]], hide_index=True, column_config={
            "chg_24h": pct("24h %"), "chg_7d": pct("7d %"), "exp_pct": pct(f"Expected {H}d %"), "lo_pct": pct("Low (10th pct) %"),
            "hi_pct": pct("High (90th pct) %"), "prob_up": st.column_config.NumberColumn("Chance up %", format="%.0f%%"),
            "label": "Past-test verdict"})
        st.caption("Every coin is shown, whatever its move. 'Low' and 'High' bracket where 80% of this model's past forecast errors "
                   "fell. If that range is far wider than the expected move, the forecast is mostly noise.")
        pick = st.selectbox("Look closer", t.symbol, key="x_pick")
        r = latest[latest.symbol == pick].iloc[0]
        hist = pd.read_sql("SELECT date, close FROM crypto_prices WHERE symbol=? ORDER BY date", con, params=(pick,))
        if len(hist): st.line_chart(hist.set_index("date")["close"])
        if pd.notna(r.exp_pct):
            a, b, c, d = st.columns(4)
            a.metric(f"Expected move, next {H} days", f"{r.exp_pct * 100:+.1f}%")
            b.metric("80% range", f"{r.lo_pct * 100:+.1f}% to {r.hi_pct * 100:+.1f}%")
            c.metric("Chance of up", f"{r.prob_up * 100:.0f}%")
            d.metric("Past-test verdict", r.label)
            st.write(f"Price now **{r.price:,.4g}**. Expected price in {H} days **{r.price * (1 + r.exp_pct):,.4g}** "
                     f"(range {r.price * (1 + r.lo_pct):,.4g} to {r.price * (1 + r.hi_pct):,.4g}).")
            if pd.notna(r.dir_acc):
                st.caption(f"Past tests ({int(r.n_oos)} out-of-sample forecasts): right direction {r.dir_acc * 100:.0f}% of the time, versus "
                           f"{r.base_acc * 100:.0f}% for always guessing the more common direction. Error versus a naive 'no change' guess: "
                           f"{r.edge * 100:+.0f}% (positive = better than naive).")
        else:
            st.info("Not enough price history on the exchanges tried to build a forecast for this coin.")

    with tabs[1]:
        try:
            pts = pd.read_sql("SELECT * FROM crypto_points", con); pts = pts[pts.ts == pts.ts.max()].copy()
        except Exception:
            pts = pd.DataFrame()
        if pts.empty:
            st.info("No data yet. Run the scan first.")
        else:
            st.write("**Early ENTRY** = price turns up through its 10-day average while momentum and volume confirm, and the move is still young. "
                     "**Early EXIT** = price turns down through that average, or closes under a volatility stop. Signals are read on completed daily candles.")
            pts["entry_dist"] = np.where(pts.entry_level.notna(), (pts.entry_level / pts.price - 1) * 100, np.nan)
            pts["exit_dist"] = np.where(pts.exit_level.notna(), (pts.exit_level / pts.price - 1) * 100, np.nan)
            pts["p_entry"], pts["p_exit"] = pts.p_entry * 100, pts.p_exit * 100
            pts["o"] = pts.status.map({"EARLY ENTRY signal": 0, "EARLY EXIT signal": 1}).fillna(2)
            pts = pts.sort_values(["o", "symbol"])
            st.dataframe(pts[["symbol", "status", "price", "entry_level", "entry_dist", "p_entry", "exit_level", "exit_dist", "p_exit", "stop_if_entry"]], hide_index=True, column_config={
                "status": "Status now", "entry_level": "Entry trigger (close above)", "entry_dist": pct("Move needed %"),
                "p_entry": st.column_config.NumberColumn(f"Chance it triggers in {H}d", format="%.0f%%"),
                "exit_level": "Exit trigger (close below)", "exit_dist": pct("Drop that triggers %"),
                "p_exit": st.column_config.NumberColumn(f"Chance it triggers in {H}d", format="%.0f%%"),
                "stop_if_entry": "Idea fails below (if entering now)"})
            st.caption("A blank entry trigger means price is already above its 10-day average, so a fresh early entry would need a dip and a re-cross. "
                       "A blank exit trigger means an exit signal has already fired or price is already under its stop. The 'chance it triggers' numbers "
                       "come from simulated price paths that reuse the last year of daily moves with the trend removed: they assume normal volatility "
                       "and no direction, so they are not a prediction of where price will go.")
            sym = st.selectbox("Chart a coin", sorted(pts.symbol), key="pt_pick")
            h2 = pd.read_sql("SELECT date, close FROM crypto_prices WHERE symbol=? ORDER BY date", con, params=(sym,))
            if len(h2):
                h2["10-day average"] = h2.close.ewm(span=10, adjust=False).mean()
                st.line_chart(h2.set_index("date"))
        st.subheader("Do these rules beat just holding? (tested on past data)")
        try:
            bt = pd.read_sql("SELECT * FROM crypto_rule_backtest", con)
            for col in ("win_rate", "avg_trade", "strat_ret", "hold_ret", "strat_dd", "hold_dd", "in_market"): bt[col] = bt[col] * 100
            st.dataframe(bt[["symbol", "trades", "win_rate", "avg_trade", "strat_ret", "hold_ret", "strat_dd", "hold_dd", "in_market"]], hide_index=True, column_config={
                "trades": st.column_config.NumberColumn("Trades", format="%.0f"), "win_rate": pct("Winning trades %"), "avg_trade": pct("Average trade %"),
                "strat_ret": pct("Rules: total return %"), "hold_ret": pct("Just holding: total return %"),
                "strat_dd": pct("Rules: worst drop %"), "hold_dd": pct("Holding: worst drop %"), "in_market": pct("Time invested %")})
            st.caption("Each entry is filled at the NEXT day's open and every trade pays fees both ways (default 0.1% per side). The rules usually "
                       "reduce the worst drops but lag badly in a steady uptrend, because they are invested only part of the time. Coins with the "
                       "best past results may simply have been lucky. Only 2 to 3 years of history are available, and delisted coins are missing.")
        except Exception:
            st.info("No rule backtest yet.")

    with tabs[2]:
        st.write("Patterns active on the last two completed days. Bias tells you which way the pattern is traditionally read, not what will happen.")
        rows = []
        for _, r in latest.iterrows():
            for p in [x for x in (r.patterns or "").split("|") if x]:
                rows.append(dict(symbol=r.symbol, pattern=p, bias=BIAS.get(p, ""), price=r.price,
                                 fails_below=r.fail_level, trailing_exit=r.trail_level))
        if rows: st.dataframe(pd.DataFrame(rows), hide_index=True)
        else: st.info("No patterns active right now.")
        st.caption("'fails_below' is the lowest low of the last 10 days: if price closes under it, the pattern's idea has failed. "
                   "'trailing_exit' is the 20-day high minus twice the average daily range: a common trailing-stop style level. "
                   "These are reference levels, not instructions.")

    with tabs[3]:
        try:
            ps = pd.read_sql("SELECT * FROM crypto_pattern_stats", con)
            for col in ("med7", "up7", "big_up7", "big_dn7", "med30", "gain2x90", "gain10x90"): ps[col] = ps[col] * 100
            st.dataframe(ps[["pattern", "n", "med7", "up7", "big_up7", "big_dn7", "med30", "gain2x90", "gain10x90"]], hide_index=True, column_config={
                "n": "Times seen", "med7": pct("Median 7d %"), "up7": pct("Up after 7d %"), "big_up7": pct("+10% or more in 7d, % of cases"),
                "big_dn7": pct("-10% or worse in 7d, % of cases"), "med30": pct("Median 30d %"),
                "gain2x90": pct("Doubled within 90d, % of cases"), "gain10x90": pct("10x within 90d, % of cases")})
            st.caption("What actually followed each pattern in the coins and history tested. Compare each row with 'All days (no signal)': "
                       "a pattern only matters if it differs clearly from that baseline. Small counts are unreliable, and coins that "
                       "were delisted are missing from the data, which flatters every result.")
        except Exception:
            st.info("No pattern history yet.")

    with tabs[4]:
        st.write(f"How this tool's own past forecasts turned out (needs scans at least {H} days apart).")
        old = fc[fc.ts_dt <= pd.Timestamp.now("UTC").tz_localize(None) - pd.Timedelta(days=H)].dropna(subset=["exp_pct"]).copy()
        if old.empty:
            st.info("Not enough history yet. Keep scanning daily and this fills in.")
        else:
            old["target"] = old.ts_dt + pd.Timedelta(days=H)
            px = fc[["symbol", "ts_dt", "price"]].rename(columns={"ts_dt": "ts2", "price": "price_later"}).sort_values("ts2")
            m = pd.merge_asof(old.sort_values("target"), px, left_on="target", right_on="ts2", by="symbol",
                              direction="forward", tolerance=pd.Timedelta(days=2)).dropna(subset=["price_later"])
            if m.empty: st.info("Waiting for later scans to compare against.")
            else:
                m["actual"] = m.price_later / m.price - 1
                m["right_dir"] = np.sign(m.exp_pct) == np.sign(m.actual)
                m["inside"] = (m.actual >= m.lo_pct) & (m.actual <= m.hi_pct)
                a, b, c = st.columns(3)
                a.metric("Forecasts checked", len(m)); b.metric("Right direction", f"{m.right_dir.mean() * 100:.0f}%")
                c.metric("Actual move inside 80% range", f"{m.inside.mean() * 100:.0f}%")
                m["expected %"], m["actual %"] = m.exp_pct * 100, m.actual * 100
                st.dataframe(m[["ts", "symbol", "expected %", "actual %", "right_dir", "inside"]].sort_values("ts", ascending=False), hide_index=True)
        st.subheader("Paper trading: what pattern signals did after they appeared")
        try:
            sg = pd.read_sql("SELECT * FROM crypto_signals", con)
            now_px = latest.set_index("symbol").price
            sg["price_now"] = sg.symbol.map(now_px); sg["change_%"] = (sg.price_now / sg.price - 1) * 100
            sg["days_ago"] = (pd.Timestamp.now("UTC").tz_localize(None) - pd.to_datetime(sg.ts)).dt.days
            st.dataframe(sg.sort_values("ts", ascending=False)[["ts", "symbol", "pattern", "bias", "price", "price_now", "change_%", "days_ago"]], hide_index=True)
            st.caption("Nothing is bought or sold. This only records signals so you can see, before risking money, whether they were worth following.")
        except Exception:
            st.info("No signals logged yet.")
