from __future__ import annotations

import streamlit as st


def info(message: str) -> None:
    st.info(message)


def warning(message: str) -> None:
    st.warning(message)


def error(message: str) -> None:
    st.error(message)
