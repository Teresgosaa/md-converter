import streamlit as st
st.set_page_config(page_title="Конвертация в Markdown", layout="wide")
from views.pdf_to_md import render
render()