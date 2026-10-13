import sqlite3, pandas as pd, streamlit as st
from channels import brief, DB


def render():
    st.title("Channel Opportunity Finder - YouTube")
    try:
        con = sqlite3.connect(DB)
        opps = pd.read_sql("SELECT * FROM channel_opps", con)
    except Exception:
        st.warning("No data yet. Set YT_API_KEY and run `python channels.py`."); return

    latest = opps.sort_values("ts").drop_duplicates("niche", keep="last").sort_values("score", ascending=False)
    st.caption("Score rises with outlier videos on small channels, many distinct small winners, new channels succeeding, "
               "and comment requests. It drops when big channels dominate the results.")
    st.dataframe(latest[[c for c in ["niche", "score", "n_channels", "median_ratio", "new_share", "saturation", "requests", "short_share", "monetization", "ts"] if c in latest]], hide_index=True)

    niche = st.selectbox("Investigate", latest.niche, key="c_pick")
    ev = pd.read_sql("SELECT * FROM outlier_videos WHERE niche=? AND ts=(SELECT MAX(ts) FROM outlier_videos WHERE niche=?)",
                     con, params=(niche, niche))
    ev["link"] = "https://youtube.com/watch?v=" + ev.video_id
    st.subheader("Evidence: small channels with outsized views")
    st.dataframe(ev[["title", "channel", "subs", "views", "ratio", "chan_created", "link"]],
                 hide_index=True, column_config={"link": st.column_config.LinkColumn("Watch")})
    if st.button("Generate channel brief"):
        st.markdown(brief(niche, ev.sort_values("ratio", ascending=False).to_dict("records")))

    st.subheader("Fastest-growing small channels (needs scans on 2+ different days)")
    try:
        snap = pd.read_sql("SELECT * FROM chan_snap", con).sort_values("ts")
        g = snap.groupby("channel_id").agg(channel=("channel", "last"), first=("subs", "first"), last=("subs", "last"), scans=("ts", "nunique"))
        g = g[g.scans > 1]; g["growth_%"] = ((g["last"] - g["first"]) / g["first"].clip(lower=1) * 100).round(1)
        st.dataframe(g.sort_values("growth_%", ascending=False).head(20))
    except Exception:
        st.caption("No snapshots yet.")
