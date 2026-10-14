import sqlite3
import pandas as pd, streamlit as st
from crypto import DB, H

SIGNAL = {"EARLY ENTRY signal": ("🟢", "Entry signal", "the price just turned up with strength"),
          "EARLY EXIT signal": ("🔴", "Exit signal", "the price just turned down"),
          "Uptrend underway (entering now would be chasing)": ("🟡", "Already rising", "late to jump in now"),
          "Above short average, no confirmed entry": ("⚪", "Recovering", "no signal yet"),
          "Waiting (below short average)": ("⚪", "Wait", "the price is below its short-term average")}
TRUST = {"Edge in past tests (no guarantee)": "Medium", "Weak edge": "Low to medium",
         "No edge shown - treat as noise": "Low", "Not enough history": "None"}

def money(x):
    return f"{x:,.2f}" if x >= 100 else f"{x:,.4g}"

def render():
    st.header("Crypto, in plain English")
    st.caption("Educational only, not financial advice. Nobody can reliably predict crypto prices.")
    with st.expander("How to read this page"):
        st.markdown(
            "🟢 **Entry signal**: the price just turned up with strength. This is the earliest sign of a rise.  \n"
            "🔴 **Exit signal**: the price just turned down. This is the earliest sign of a drop.  \n"
            "🟡 **Already rising**: the move has started, so jumping in now means chasing it.  \n"
            "⚪ **Wait / Recovering**: nothing to act on.  \n\n"
            "**Next 7 days (guess)** is the tool's estimate. **Trust** says how well that kind of guess did in past tests. "
            "**Low means it was no better than a coin flip, so ignore the number.** Even a Medium guess can be badly wrong.  \n\n"
            "Never put in money you cannot afford to lose.")
    try:
        con = sqlite3.connect(DB)
        fc = pd.read_sql("SELECT * FROM crypto_forecast", con)
        pt = pd.read_sql("SELECT * FROM crypto_points", con)
    except Exception:
        fc = pt = pd.DataFrame()
    if fc.empty or pt.empty:
        st.info("No crypto data yet. It appears after the first scan finishes.")
        return
    fc, pt = fc[fc.ts == fc.ts.max()], pt[pt.ts == pt.ts.max()]
    t = fc.merge(pt[["symbol", "status", "entry_level", "exit_level", "p_entry", "p_exit"]], on="symbol")
    t["order"] = t.status.map({"EARLY ENTRY signal": 0, "EARLY EXIT signal": 1}).fillna(2)
    t = t.sort_values(["order", "symbol"])
    st.caption(f"Last scan (UTC): {fc.ts.max()}. Coins with a signal are listed first.")
    for _, r in t.iterrows():
        icon, what, why = SIGNAL.get(r.status, ("⚪", r.status, ""))
        guess = f"{'Up' if r.exp_pct > 0 else 'Down'} about {abs(r.exp_pct) * 100:.1f}%" if pd.notna(r.exp_pct) else "no forecast"
        st.markdown(f"{icon} **{r.symbol}** · {money(r.price)}  \nLast 7 days: {r.chg_7d * 100:+.1f}% · "
                    f"Next 7 days (guess): {guess} · Trust: {TRUST.get(r.label, 'Low')}  \n**{what}**: {why}")
    pick = st.selectbox("Tap a coin for details", list(t.symbol), key="xs_pick")
    r = t[t.symbol == pick].iloc[0]
    st.subheader(f"{pick} in detail")
    if pd.notna(r.exp_pct):
        st.write(f"Price now **{money(r.price)}**. In {H} days the best guess is **{money(r.price * (1 + r.exp_pct))}**, but it could land "
                 f"anywhere from {money(r.price * (1 + r.lo_pct))} to {money(r.price * (1 + r.hi_pct))}. "
                 f"Trust in this guess: **{TRUST.get(r.label, 'Low')}**.")
    if pd.notna(r.entry_level):
        odds = f", roughly a {r.p_entry * 100:.0f}% chance within {H} days" if pd.notna(r.p_entry) else ""
        st.write(f"🟢 An **entry signal** appears if the price closes above **{money(r.entry_level)}** ({(r.entry_level / r.price - 1) * 100:+.1f}% from now){odds}.")
    else:
        st.write("🟢 The price is already above its short-term average, so a new entry signal would need a dip and a bounce first.")
    if pd.notna(r.exit_level):
        odds = f", roughly a {r.p_exit * 100:.0f}% chance within {H} days" if pd.notna(r.p_exit) else ""
        st.write(f"🔴 An **exit signal** appears if the price closes below **{money(r.exit_level)}** ({(r.exit_level / r.price - 1) * 100:+.1f}% from now){odds}.")
    else:
        st.write("🔴 No exit level right now: an exit signal is already active, or the price is already below its safety line.")
    st.caption("The 'chance' figures come from simulated price moves with no direction assumed. They show how close a level is, not where price will go.")
    h = pd.read_sql("SELECT date, close FROM crypto_prices WHERE symbol=? ORDER BY date", con, params=(pick,))
    if len(h): st.line_chart(h.set_index("date")["close"])
    st.caption("For pattern signals, past results and the forecast scorecard, open the 'Crypto details' tab.")
