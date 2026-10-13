import os, json, sqlite3, pandas as pd, streamlit as st
from gaps import DB


def render():
    st.title("Digital Products & Service Gaps")
    con = sqlite3.connect(DB)
    pcon = sqlite3.connect(os.getenv("PIPELINE_DB", "pipeline.db"))
    pcon.execute("CREATE TABLE IF NOT EXISTS pipeline(idea TEXT PRIMARY KEY, kind TEXT, status TEXT, notes TEXT)")
    try:
        df = pd.read_sql("SELECT * FROM gaps", con)
    except Exception:
        st.warning("No data yet. Run `python gaps.py` first."); return

    df = df[df.ts == df.ts.max()]
    kind = st.radio("Type", ["all", "digital", "service"], horizontal=True, key="d_kind")
    floor = st.slider("Min score", 0.0, 1.0, 0.2, key="d_min")
    view = df[(df.score >= floor) & ((df.kind == kind) if kind != "all" else True)].sort_values("score", ascending=False)
    st.caption("supply = how many similar projects already exist on GitHub (0 = none, 1 = many). Each row is a cluster of people asking for the same thing. More mentions, more sources and more engagement = stronger demand.")
    st.dataframe(view[[c for c in ["title", "kind", "score", "mentions", "sources", "engagement", "supply"] if c in view]], hide_index=True)

    if len(view):
        row = view[view.title == st.selectbox("Investigate", view.title, key="d_pick")].iloc[0]
        st.subheader("Evidence")
        for text, url, src in json.loads(row.evidence):
            st.markdown(f"- [{text}]({url}) _({src})_")
        st.info("Validate: search for existing solutions and read their bad reviews; ask 5 people in the thread if they would pay; "
                + ("sell a simple version first (Gumroad, Etsy, Notion template)." if row.kind == "digital"
                   else "offer it manually to 3 people before building a business around it."))
        if st.button("Save to pipeline"):
            pcon.execute("INSERT OR IGNORE INTO pipeline VALUES (?,?,?,?)", (row.title, row.kind, "watching", "")); pcon.commit()

    st.subheader("My pipeline")
    pipe = pd.read_sql("SELECT * FROM pipeline", pcon)
    edited = st.data_editor(pipe, hide_index=True, key="pipe", column_config={
        "status": st.column_config.SelectboxColumn(options=["watching", "validating", "testing", "launched", "dropped"])})
    if st.button("Save pipeline changes"):
        pcon.execute("DELETE FROM pipeline"); edited.to_sql("pipeline", pcon, if_exists="append", index=False); pcon.commit(); st.success("Saved")

    c1, c2 = st.columns(2)
    c1.download_button("Download pipeline (CSV)", edited.to_csv(index=False), "pipeline.csv", "text/csv")
    up = c2.file_uploader("Restore pipeline (CSV)", type="csv")
    if up is not None and st.button("Load file"):
        pd.read_csv(up).to_sql("pipeline", pcon, if_exists="replace", index=False); st.success("Loaded. Refresh the page.")
