"""직원 관리: 인적사항 + 급여 기본자료 (월별 급여 시트의 로딩 원천)"""
import streamlit as st
import auth
import helpers
from db import get_db

FIELDS_BASIC = [("name", "이름 *"), ("name_en", "영문 이름"),
                ("position", "직책"), ("phone", "전화"), ("email", "이메일"),
                ("address", "주소"), ("visa_status", "비자 상태")]
FIELDS_PAY = [("base_salary", "기본급(월)"), ("per_diem", "일비"),
              ("ot_rate", "OT 시간당 단가"), ("meal_support", "중식 지원"),
              ("vehicle_support", "차량 지원"), ("telecom_support", "통신비 지원"),
              ("transfer_fee", "송금수수료")]


def emp_form(key: str, e: dict | None = None):
    e = e or {}
    with st.form(f"emp_{key}", clear_on_submit=(e == {})):
        st.markdown("**인적 사항**")
        c = st.columns(2)
        vals = {}
        for i, (f, label) in enumerate(FIELDS_BASIC):
            vals[f] = c[i % 2].text_input(label, value=e.get(f) or "")
        c2 = st.columns(2)
        hire = c2[0].text_input("입사일 (YYYY-MM-DD)", value=e.get("hire_date") or "")
        leave = c2[1].text_input("퇴사일 (YYYY-MM-DD)", value=e.get("leave_date") or "")

        st.markdown("**계좌 정보** (급여 이체용)")
        b = st.columns(4)
        bank_name = b[0].text_input("은행명 (Bank)", value=e.get("bank_name") or "")
        account_no = b[1].text_input("계좌번호 (Account #)",
                                     value=e.get("account_no") or "")
        routing_no = b[2].text_input("Routing #", value=e.get("routing_no") or "")
        account_type = b[3].selectbox(
            "계좌 종류", ["", "Checking", "Savings"],
            index=["", "Checking", "Savings"].index(e.get("account_type") or ""))
        zelle = st.text_input("Zelle (이메일/전화)", value=e.get("zelle") or "")

        st.markdown("**급여 기본자료** (월별 급여 시트에 자동 로딩됩니다)")
        c3 = st.columns(4)
        for i, (f, label) in enumerate(FIELDS_PAY):
            vals[f] = c3[i % 4].number_input(
                label, min_value=0.0, step=10.0, format="%.2f",
                value=float(e.get(f) or (15 if f == "transfer_fee" else 0)))
        notes = st.text_area("비고", value=e.get("notes") or "", height=68)
        active = st.checkbox("재직 중", value=e.get("is_active", True))
        ok = st.form_submit_button("저장", type="primary")
    if not ok:
        return None
    if not vals["name"]:
        st.error("이름을 입력하세요.")
        return None
    vals.update({"hire_date": hire or None, "leave_date": leave or None,
                 "bank_name": bank_name, "account_no": account_no,
                 "routing_no": routing_no, "account_type": account_type,
                 "zelle": zelle,
                 "notes": notes, "is_active": active})
    return vals


def render():
    helpers.page_title("직원 관리")
    db = get_db()
    editable = auth.can_edit("employees")

    if editable:
        with st.expander("➕ 직원 등록"):
            data = emp_form("new")
            if data:
                db.table("employees").insert(data).execute()
                st.success(f"'{data['name']}' 등록 완료")
                st.rerun()

    emps = db.table("employees").select("*").order("name").execute().data
    st.subheader(f"직원 목록 ({sum(1 for e in emps if e['is_active'])}명 재직 / "
                 f"전체 {len(emps)}명)")
    if not emps:
        st.info("등록된 직원이 없습니다.")
        return

    for e in emps:
        title = (f"{'🟢' if e['is_active'] else '⚪'} **{e['name']}**"
                 f"  ·  {e.get('position') or '-'}"
                 f"  ·  기본급 ${float(e.get('base_salary') or 0):,.2f}")
        with st.expander(title):
            i1, i2 = st.columns(2)
            i1.markdown(
                f"입사일: {e.get('hire_date') or '-'}  \n"
                f"📞 {e.get('phone') or '-'} / ✉️ {e.get('email') or '-'}  \n"
                f"비자: {e.get('visa_status') or '-'}")
            i2.markdown(
                f"OT단가: ${float(e.get('ot_rate') or 0):,.2f}/hr  \n"
                f"지원: 중식 ${float(e.get('meal_support') or 0):,.0f} · "
                f"차량 ${float(e.get('vehicle_support') or 0):,.0f} · "
                f"통신 ${float(e.get('telecom_support') or 0):,.0f}  \n"
                f"송금수수료: ${float(e.get('transfer_fee') or 0):,.2f}")
            st.caption(
                f"🏦 {e.get('bank_name') or '-'} "
                f"({e.get('account_type') or '-'}) · "
                f"계좌: {e.get('account_no') or '-'} · "
                f"Routing: {e.get('routing_no') or '-'} · "
                f"Zelle: {e.get('zelle') or '-'}")
            if editable:
                st.divider()
                data = emp_form(str(e["id"]), e)
                if data:
                    db.table("employees").update(data).eq("id", e["id"]).execute()
                    st.success("수정되었습니다.")
                    st.rerun()
