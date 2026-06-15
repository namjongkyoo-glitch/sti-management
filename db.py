"""Supabase 연결 모듈 (일시적 네트워크 끊김 자동 복구)"""
import streamlit as st
from supabase import create_client, Client


def _make() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


@st.cache_resource
def _cached_client() -> Client:
    return _make()


def get_db() -> Client:
    """Supabase 클라이언트 반환 (앱 전체에서 재사용)"""
    return _cached_client()


def reset_db():
    """연결이 끊겼을 때 캐시된 클라이언트를 버리고 재생성"""
    try:
        _cached_client.clear()
    except Exception:
        pass
    return _cached_client()
