"""협력업체 관리: 등록/수정 + 업체별 현황 (발주/지급/잔여, 프로젝트별)"""
import pandas as pd
import streamlit as st
import auth
import helpers
from db import get_db


def trade_input(key: str, current: str = ""):
    """공종 선택 + 직접 입력. 직접 입력란에 값이 있으면 그것을 우선 사용."""
    trades = helpers.load_trades()
    options = [""] + trades
    idx = options.index(current) if current in options else 0
    c1, c2 = st.columns(2)
    sel = c1.selectbox("공종/업종 (목록 선택)", options, index=idx, key=f"ts_{key}")
    custom = c2.text_input("공종/업종 직접 입력 (입력 시 우선 적용, 목록에 자동 추가)",
                           key=f"tc_{key}",
                           value=current if current and current not in trades else "")
    return custom.strip() if custom.strip() else sel


def vendor_form(key: str, v: dict | None = None):
    """등록/수정 공용 폼. 저장 시 dict 반환, 아니면 None."""
    v = v or {}
    g = lambda f: v.get(f) or ""
    with st.form(f"vf_{key}", clear_on_submit=(v == {})):
        st.markdown("**기본 정보**")
        c1, c2 = st.columns(2)
        name = c1.text_input("업체명 *", value=g("name"))
        contact = c2.text_input("담당자", value=g("contact"))
        trade = trade_input(key, g("trade"))
        c3, c4 = st.columns(2)
        phone = c3.text_input("전화", value=g("phone"))
        email = c4.text_input("이메일", value=g("email"))
        address = st.text_input("주소", value=g("address"))

        st.markdown("**은행 / 송금 정보**")
        b1, b2 = st.columns(2)
        bank_name = b1.text_input("은행명 (Bank Name)", value=g("bank_name"))
        account_no = b2.text_input("계좌번호 (Account #)", value=g("account_no"))
        b3, b4 = st.columns(2)
        routing_no = b3.text_input("Routing #", value=g("routing_no"))
        zelle = b4.text_input("Zelle (이메일/전화)", value=g("zelle"))
        payment_notes = st.text_input("결제 비고 (Check 수취인명, Wire 조건 등)",
                                      value=g("payment_notes"))
        notes = st.text_area("비고", value=g("notes"), height=68)
        if v:
            active = st.checkbox("활성", value=v.get("is_active", True))
        else:
            active = True
        ok = st.form_submit_button("저장", type="primary")
    if not ok:
        return None
    if not name:
        st.error("업체명을 입력하세요.")
        return None
    if trade and trade not in helpers.load_trades():
        helpers.add_trade(trade)  # 커스텀 공종 목록에 추가
    return {
        "name": name, "trade": trade, "contact": contact,
        "phone": phone, "email": email, "address": address,
        "bank_name": bank_name, "account_no": account_no,
        "routing_no": routing_no, "zelle": zelle,
        "payment_notes": payment_notes, "notes": notes,
        "is_active": active,
    }


def render():
    helpers.page_title("협력업체 관리")
    db = get_db()
    editable = auth.can_edit("vendors")

    tab1, tab2 = st.tabs(["📊 업체별 현황", "🏢 업체 관리"])
    with tab1:
        status_tab(db)
    with tab2:
        manage_tab(db, editable)


# ============================================================
# 업체별 현황 (발주/지급/잔여, 프로젝트별 내역)
# ============================================================
def status_tab(db):
    vendors = db.table("vendors").select("*").order("name").execute().data
    if not vendors:
        st.info("등록된 협력업체가 없습니다.")
        return
    vname = {v["id"]: v["name"] for v in vendors}

    budgets = db.table("budget_lines").select("*").execute().data
    txs = (db.table("transactions").select("amount,vendor_id,project_id,tx_type")
           .execute().data)
    projects = db.table("projects").select("id,code,name,status").execute().data
    prj_map = {p["id"]: p for p in projects}

    def mid(aid):
        a = helpers.account_by_id(aid)
        if not a:
            return None
        return a["name_kr"] if a["level"] <= 2 else \
            (helpers.account_by_id(a["parent_id"]) or {}).get("name_kr")

    # 업체별 집계
    rows = []
    for v in vendors:
        vid = v["id"]
        # 발주(예산) = 직접비 예산 중 해당 업체
        ordered = sum(float(b["amount"] or 0) for b in budgets
                      if b.get("vendor_id") == vid
                      and mid(b["account_id"]) in helpers.COST_MIDS_DIRECT)
        # 진행중 프로젝트 발주분
        ordered_active = sum(
            float(b["amount"] or 0) for b in budgets
            if b.get("vendor_id") == vid
            and mid(b["account_id"]) in helpers.COST_MIDS_DIRECT
            and prj_map.get(b["project_id"], {}).get("status") == "진행중")
        # 지급액 = 지출 거래 중 해당 업체
        paid = sum(float(t["amount"] or 0) for t in txs
                   if t["tx_type"] == "지출" and t.get("vendor_id") == vid)
        if ordered == 0 and paid == 0:
            continue
        rows.append({
            "_id": vid, "협력업체": v["name"], "공종": v.get("trade") or "-",
            "총 발주금액": ordered, "진행중 발주": ordered_active,
            "지급액": paid, "잔여(미지급)": ordered - paid,
        })

    if not rows:
        st.info("발주(예산) 또는 지급 내역이 있는 협력업체가 없습니다.")
        return

    df = pd.DataFrame(rows)
    money = ["총 발주금액", "진행중 발주", "지급액", "잔여(미지급)"]
    m = st.columns(4)
    m[0].metric("총 발주금액", f"${df['총 발주금액'].sum():,.0f}")
    m[1].metric("진행중 발주금액", f"${df['진행중 발주'].sum():,.0f}")
    m[2].metric("총 지급액", f"${df['지급액'].sum():,.0f}")
    m[3].metric("총 잔여(미지급)", f"${df['잔여(미지급)'].sum():,.0f}")

    show = df.drop(columns=["_id"]).copy()
    tot = {"협력업체": "합계", "공종": "",
           **{c: df[c].sum() for c in money}}
    show = pd.concat([show, pd.DataFrame([tot])], ignore_index=True)
    for c in money:
        show[c] = show[c].map(lambda x: f"${x:,.0f}")
    st.dataframe(show, use_container_width=True, hide_index=True)

    # ---- 업체 선택 시 프로젝트별 내역 ----
    st.divider()
    sel = st.selectbox("협력업체 선택 (프로젝트별 상세)", rows,
                       format_func=lambda r: f"{r['협력업체']} ({r['공종']})")
    vid = sel["_id"]
    st.markdown(f"**{sel['협력업체']} — 프로젝트별 발주/지급 현황**")

    # 프로젝트별 집계
    prj_rows = {}
    for b in budgets:
        if b.get("vendor_id") == vid and \
                mid(b["account_id"]) in helpers.COST_MIDS_DIRECT:
            pid = b["project_id"]
            prj_rows.setdefault(pid, {"발주": 0.0, "지급": 0.0})
            prj_rows[pid]["발주"] += float(b["amount"] or 0)
    for t in txs:
        if t["tx_type"] == "지출" and t.get("vendor_id") == vid:
            pid = t.get("project_id")
            prj_rows.setdefault(pid, {"발주": 0.0, "지급": 0.0})
            prj_rows[pid]["지급"] += float(t["amount"] or 0)

    if not prj_rows:
        st.info("해당 업체의 프로젝트 내역이 없습니다.")
        return
    detail = []
    for pid, d in prj_rows.items():
        p = prj_map.get(pid, {})
        detail.append({
            "프로젝트": f"{p.get('code', '-')} {p.get('name', '(미지정)')}",
            "상태": p.get("status") or "-",
            "발주금액": d["발주"], "지급액": d["지급"],
            "잔여": d["발주"] - d["지급"],
        })
    ddf = pd.DataFrame(detail)
    for c in ["발주금액", "지급액", "잔여"]:
        ddf[c] = ddf[c].map(lambda x: f"${x:,.0f}")
    st.dataframe(ddf, use_container_width=True, hide_index=True)


# ============================================================
# 업체 관리 (등록/수정)
# ============================================================
def manage_tab(db, editable):
    if editable:
        with st.expander("➕ 협력업체 등록"):
            data = vendor_form("new")
            if data:
                db.table("vendors").insert(data).execute()
                helpers.clear_caches()
                st.success(f"'{data['name']}' 등록 완료")
                st.rerun()

    vendors = db.table("vendors").select("*").order("name").execute().data
    st.subheader(f"업체 목록 ({len(vendors)})")
    if not vendors:
        st.info("등록된 협력업체가 없습니다.")
        return

    for v in vendors:
        title = (f"{'✅' if v['is_active'] else '⛔'} **{v['name']}**"
                 f"  ·  {v.get('trade') or '-'}  ·  {v.get('contact') or '-'}")
        with st.expander(title):
            info1, info2 = st.columns(2)
            info1.markdown(
                f"📞 {v.get('phone') or '-'}  \n"
                f"✉️ {v.get('email') or '-'}  \n"
                f"📍 {v.get('address') or '-'}")
            info2.markdown(
                f"🏦 {v.get('bank_name') or '-'}  \n"
                f"계좌: {v.get('account_no') or '-'} / Routing: {v.get('routing_no') or '-'}  \n"
                f"Zelle: {v.get('zelle') or '-'}")
            if v.get("payment_notes"):
                st.caption(f"결제 비고: {v['payment_notes']}")
            if v.get("notes"):
                st.caption(f"비고: {v['notes']}")
            if editable:
                st.divider()
                data = vendor_form(str(v["id"]), v)
                if data:
                    db.table("vendors").update(data).eq("id", v["id"]).execute()
                    helpers.clear_caches()
                    st.success("수정되었습니다.")
                    st.rerun()
