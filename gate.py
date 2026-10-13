"""Optional password for the online version. Set APP_PASSWORD in Streamlit secrets to turn it on."""
import os, streamlit as st

def require_password():
    pw = os.getenv("APP_PASSWORD")
    if pw and not st.session_state.get("ok"):
        if st.text_input("Password", type="password") == pw:
            st.session_state["ok"] = True
            st.rerun()
        st.stop()
