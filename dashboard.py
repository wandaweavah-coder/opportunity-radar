import streamlit as st

st.set_page_config(page_title="Opportunity Radar", layout="wide")
import gate; gate.require_password()
import page_products, page_channels, page_digital, page_crypto

st.title("Opportunity Radar")
tabs = st.tabs(["Products", "Channels", "Digital & Services", "Crypto"])
for tab, mod in zip(tabs, (page_products, page_channels, page_digital, page_crypto)):
    with tab:
        mod.render()
