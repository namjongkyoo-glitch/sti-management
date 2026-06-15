"""공통 헬퍼: 계정 체계 / 협력업체 로딩, 자동 채번"""
from datetime import date
import streamlit as st
from db import get_db


@st.cache_data(ttl=300)
def load_accounts() -> list[dict]:
    db = get_db()
    return (db.table("accounts").select("*")
            .eq("is_active", True).order("code").execute().data)


def accounts_by_level(level: int) -> list[dict]:
    return [a for a in load_accounts() if a["level"] == level]


def account_by_id(acc_id: int) -> dict | None:
    for a in load_accounts():
        if a["id"] == acc_id:
            return a
    return None


def account_id_by_name(name: str, level: int) -> int | None:
    for a in load_accounts():
        if a["level"] == level and a["name_kr"] == name:
            return a["id"]
    return None


# 견적 원가 구성에 쓰는 중분류 (수입 제외)
COST_MIDS_DIRECT = ["원재료", "외주비", "직접경비"]
COST_MIDS_INDIRECT = ["노무비", "제조간접경비", "판관비"]
COST_MIDS = COST_MIDS_DIRECT + COST_MIDS_INDIRECT


@st.cache_data(ttl=60)
def load_vendors(active_only: bool = True) -> list[dict]:
    db = get_db()
    q = db.table("vendors").select("*").order("name")
    if active_only:
        q = q.eq("is_active", True)
    return q.execute().data


def vendor_id_by_name(name: str) -> int | None:
    if not name:
        return None
    for v in load_vendors(active_only=False):
        if v["name"] == name:
            return v["id"]
    return None


def vendor_name_by_id(vid) -> str:
    if not vid:
        return ""
    for v in load_vendors(active_only=False):
        if v["id"] == vid:
            return v["name"]
    return ""


@st.cache_data(ttl=60)
def load_clients(active_only: bool = True) -> list[dict]:
    db = get_db()
    q = db.table("clients").select("*").order("name")
    if active_only:
        q = q.eq("is_active", True)
    return q.execute().data


def client_id_by_name(name: str) -> int | None:
    if not name:
        return None
    for c in load_clients(active_only=False):
        if c["name"] == name:
            return c["id"]
    return None


@st.cache_data(ttl=300)
def load_trades() -> list[str]:
    db = get_db()
    rows = (db.table("trades").select("name")
            .eq("is_active", True).order("sort_order").execute().data)
    return [r["name"] for r in rows]


def add_trade(name: str):
    name = name.strip()
    if not name or name in load_trades():
        return
    db = get_db()
    db.table("trades").insert({"name": name, "sort_order": 50}).execute()
    st.cache_data.clear()


def clear_caches():
    st.cache_data.clear()


def load_bank_accounts(active_only: bool = True) -> list[dict]:
    db = get_db()
    q = db.table("bank_accounts").select("*").order("bank_name")
    if active_only:
        q = q.eq("is_active", True)
    return q.execute().data


def bank_label(a: dict | None) -> str:
    if not a:
        return "-"
    no = a.get("account_no") or ""
    tail = f"(...{no[-4:]})" if no else ""
    return f"{a['bank_name']} {a.get('account_name') or ''} {tail}".strip()


def bank_balances() -> list[dict]:
    """통장별 잔액 = 기초 + 수입 - 지출 + 이체입 - 이체출 (이체는 수지 미반영)"""
    db = get_db()
    accs = load_bank_accounts(active_only=False)
    txs = (db.table("transactions")
           .select("tx_type,amount,bank_account_id").execute().data)
    trs = (db.table("bank_transfers")
           .select("from_account_id,to_account_id,amount").execute().data)
    res = []
    for a in accs:
        aid = a["id"]
        tin = sum(float(t["amount"] or 0) for t in txs
                  if t.get("bank_account_id") == aid and t["tx_type"] == "수입")
        tout = sum(float(t["amount"] or 0) for t in txs
                   if t.get("bank_account_id") == aid and t["tx_type"] == "지출")
        trin = sum(float(t["amount"] or 0) for t in trs
                   if t["to_account_id"] == aid)
        trout = sum(float(t["amount"] or 0) for t in trs
                    if t["from_account_id"] == aid)
        base = float(a.get("opening_balance") or 0)
        res.append({"account": a, "기초": base, "수입": tin, "지출": tout,
                    "이체입": trin, "이체출": trout,
                    "잔액": base + tin - tout + trin - trout})
    return res


def next_no(table: str, col: str, prefix: str) -> str:
    """연도별 자동 채번: Q2026-001 / P2026-001"""
    year = date.today().year
    full_prefix = f"{prefix}{year}-"
    db = get_db()
    rows = (db.table(table).select(col)
            .like(col, f"{full_prefix}%").execute().data)
    max_seq = 0
    for r in rows:
        try:
            max_seq = max(max_seq, int(r[col].split("-")[-1]))
        except Exception:
            pass
    return f"{full_prefix}{max_seq + 1:03d}"


# ----------------------------------------------------------
# 페이지 제목 (STI 로고 + 텍스트) — 이모지 대체
# ----------------------------------------------------------
import os as _os


def page_title(text: str, width: int = 40):
    """st.title 대신 사용: 로고 아이콘 + 제목을 한 줄에 표시"""
    import streamlit as st
    logo = _os.path.join(_os.path.dirname(__file__), "sti_logo.png")
    c = st.columns([1, 11])
    if _os.path.exists(logo):
        c[0].image(logo, width=width)
    c[1].title(text)


def safe_filename(*parts) -> str:
    """파일명에 쓸 수 없는 문자를 제거하고 부분들을 _ 로 연결"""
    import re
    cleaned = []
    for p in parts:
        s = re.sub(r'[\\/*?:\[\]<>|"]', "", str(p or "")).strip()
        if s:
            cleaned.append(s)
    return "_".join(cleaned) if cleaned else "STI문서"


def inject_list_style():
    """리스트 화면용 모던 테이블 스타일 (한 번만 주입)"""
    import streamlit as st
    if st.session_state.get("_list_style_done"):
        return
    st.session_state["_list_style_done"] = True
    st.markdown("""
    <style>
    /* 리스트 행: 하단 보더 + hover 강조 */
    div[data-testid="stHorizontalBlock"].sti-row {
        border-bottom: 1px solid rgba(150,160,180,0.18);
        padding: 6px 4px;
        align-items: center;
        transition: background 0.15s;
    }
    div[data-testid="stHorizontalBlock"].sti-row:hover {
        background: rgba(120,140,200,0.08);
    }
    </style>
    """, unsafe_allow_html=True)


def list_header(cols_spec, titles):
    """리스트 헤더 행 (굵게 + 하단 진한 보더)"""
    import streamlit as st
    h = st.columns(cols_spec)
    for c, t in zip(h, titles):
        c.markdown(
            f"<div style='font-weight:700;border-bottom:2px solid "
            f"rgba(150,160,180,0.5);padding-bottom:6px;font-size:13px'>"
            f"{t}</div>", unsafe_allow_html=True)


def metric_card(col, label, value, color="#3B82F6", icon=""):
    """대시보드/현황용 컬러 카드 (col은 st.columns 요소)"""
    col.markdown(
        f"<div style='background:linear-gradient(135deg,{color}22,{color}08);"
        f"border:1px solid {color}55;border-radius:12px;padding:14px 16px;"
        f"margin-bottom:6px'>"
        f"<div style='font-size:12px;color:#9aa4b2;margin-bottom:4px'>"
        f"{icon} {label}</div>"
        f"<div style='font-size:22px;font-weight:800;color:#fff'>{value}</div>"
        f"</div>", unsafe_allow_html=True)


def styled_table(rows, columns, money_cols=None, highlight=None):
    """HTML 테이블 렌더 (소계/합계 행 색상 강조).
    rows: list[dict], highlight: {행index: 'sub'|'total'} """
    import streamlit as st
    money_cols = money_cols or []
    highlight = highlight or {}
    html = ["<table style='width:100%;border-collapse:collapse;font-size:13px'>"]
    # 헤더
    html.append("<tr style='background:#1F4E79;color:#fff'>")
    for c in columns:
        align = "right" if c in money_cols else "left"
        html.append(f"<th style='padding:7px 9px;border:1px solid #cfd6e0;"
                    f"text-align:{align}'>{c}</th>")
    html.append("</tr>")
    for i, r in enumerate(rows):
        hl = highlight.get(i)
        if hl == "total":
            bg, fw = "#A9D08E", "700"
        elif hl == "sub":
            bg, fw = "#FFE699", "700"
        else:
            bg, fw = ("#FFFFFF" if i % 2 == 0 else "#F4F6FA"), "400"
        html.append(f"<tr style='background:{bg}'>")
        for c in columns:
            align = "right" if c in money_cols else "left"
            val = r.get(c, "")
            html.append(f"<td style='padding:6px 9px;border:1px solid #cfd6e0;"
                        f"text-align:{align};color:#000;font-weight:{fw}'>"
                        f"{val}</td>")
        html.append("</tr>")
    html.append("</table>")
    st.markdown("".join(html), unsafe_allow_html=True)


# 통장 표시 순서 (이름 키워드 기준)
BANK_ORDER = ["CitiDirect", "CitiBusiness", "Chase", "SHB Checking", "SHB Loan"]


def bank_sort_key(account: dict) -> int:
    """통장 이름으로 표시 순서 결정 (BANK_ORDER 우선, 그 외는 뒤로)"""
    name = (account.get("bank_name") or "") + " " + \
           (account.get("account_name") or "")
    low = name.lower()
    for i, kw in enumerate(BANK_ORDER):
        if kw.lower() in low:
            return i
    return len(BANK_ORDER) + 1


def bank_balances_ordered() -> list[dict]:
    """bank_balances를 지정된 통장 순서로 정렬해 반환"""
    bals = bank_balances()
    return sorted(bals, key=lambda b: bank_sort_key(b["account"]))


def mid_name_of(acc_id) -> str | None:
    """계정 id의 중분류(level<=2) 이름 반환. 세부계정이면 부모를 따라감."""
    acc = account_by_id(acc_id)
    if not acc:
        return None
    # level이 2 이하(대/중분류)면 그대로, 아니면 부모(중분류)로
    while acc and acc.get("level", 99) > 2 and acc.get("parent_id"):
        acc = account_by_id(acc["parent_id"])
    return acc["name_kr"] if acc else None
