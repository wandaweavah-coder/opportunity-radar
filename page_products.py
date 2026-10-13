import sqlite3, pandas as pd, streamlit as st
from radar import supplier_links, risk_flags, check_links, DB


def render():
    st.title("Opportunity Radar - Products")
    try:
        df = pd.read_sql("SELECT * FROM opps", sqlite3.connect(DB))
    except Exception:
        st.warning("No data yet. Run `python radar.py` first."); return

    st.caption(f"Last scan (UTC): {df.ts.max()}")
    latest = df.sort_values("ts").drop_duplicates("keyword", keep="last")
    seed = st.multiselect("Category", sorted(latest.seed.unique()), key="p_cat")
    min_score = st.slider("Min score", 0.0, 1.0, 0.3, key="p_min")
    view = latest[(latest.score >= min_score) & (latest.seed.isin(seed) if seed else True)].sort_values("score", ascending=False)
    st.dataframe(view[[c for c in ["keyword", "score", "lifecycle", "growth", "requests", "complaints", "intent", "supply", "seed", "ts"] if c in view]], hide_index=True)

    if len(view):
        pick = st.selectbox("Investigate", view.keyword, key="p_pick")
        a, b = st.columns(2)
        with a:
            st.subheader("Find suppliers")
            for name, url in supplier_links(pick).items():
                st.markdown(f"- [{name}]({url})")
            st.subheader("Risk check")
            for f in risk_flags(pick) or ["No obvious flags. Still check the rules for your country."]: st.write("- " + f)
            for name, url in check_links(pick).items(): st.markdown(f"- [{name}]({url})")
            hist = df[df.keyword == pick].set_index("ts").score
            if len(hist) > 1: st.line_chart(hist)
        with b:
            st.subheader("Profit calculator")
            cost = st.number_input("Unit cost $", 0.0, value=4.0)
            ship = st.number_input("Shipping per unit $", 0.0, value=1.5)
            price = st.number_input("Selling price $", 0.0, value=24.99)
            fee = st.slider("Marketplace fees %", 0, 40, 15)
            ads = st.number_input("Ad cost per sale $", 0.0, value=5.0)
            moq = st.number_input("MOQ (units)", 1, value=200)
            profit = price * (1 - fee / 100) - cost - ship - ads
            st.metric("Profit per unit", f"${profit:.2f}", f"{profit / price * 100:.0f}% margin" if price else None)
            st.metric("Starting capital", f"${(cost + ship) * moq:,.0f}")
            st.metric("Break-even units", f"{(cost + ship) * moq / profit:,.0f}" if profit > 0 else "n/a")
