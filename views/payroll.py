"""급여 관리: 월별 시트 (기본자료 로딩 → 수정 → 확정 → 공통비 자동 반영 → 엑셀)"""
import pandas as pd
import streamlit as st
from datetime import date, datetime

import auth
import helpers
from db import get_db
from excel_reports import build_payroll_excel

COLS = ["급여", "일비", "OT시간", "OT금액", "중식", "차량지원", "통신비",
        "송금수수료", "초과금환수", "원천세", "비고"]


def salary_account_id():
    for a in helpers.load_accounts():
        if a["code"] == "3101":  # 급여,연금
            return a["id"]
    return None


def common_project_id(db):
    rows = (db.table("projects").select("id")
            .eq("is_common_pool", True).limit(1).execute().data)
    return rows[0]["id"] if rows else None


def render():
    helpers.page_title("급여 관리")
    db = get_db()
    editable = auth.can_edit("payroll")
    admin = auth.is_admin()

    c1, c2 = st.columns(2)
    today = date.today()
    year = c1.selectbox("연도", list(range(today.year - 2, today.year + 2)),
                        index=2)
    month = c2.selectbox("월", list(range(1, 13)), index=today.month - 1)

    pm = (db.table("payroll_months").select("*")
          .eq("pay_year", year).eq("pay_month", month).execute().data)

    if not pm:
        st.info(f"{year}년 {month}월 급여 시트가 아직 없습니다.")
        if editable and st.button("📋 급여 시트 생성 (직원 기본자료 자동 로딩)",
                                  type="primary"):
            create_month(db, year, month)
        return

    pm = pm[0]
    confirmed = pm["status"] == "확정"
    st.markdown(f"### {year}년 {month}월 급여  ·  "
                f"{'✅ 확정' if confirmed else '🟡 작성중'}")

    lines = (db.table("payroll_lines").select("*, employees(name)")
             .eq("payroll_month_id", pm["id"]).order("id").execute().data)
    if not lines:
        st.warning("급여 라인이 없습니다.")
        return

    df = pd.DataFrame([{
        "_id": l["id"], "구분": (l.get("employees") or {}).get("name", "-"),
        "급여": float(l["base_salary"] or 0),
        "일비": float(l["per_diem"] or 0),
        "OT시간": float(l["ot_hours"] or 0),
        "OT금액": float(l["ot_amount"] or 0),
        "중식": float(l["meal"] or 0),
        "차량지원": float(l["vehicle"] or 0),
        "통신비": float(l["telecom"] or 0),
        "송금수수료": float(l["transfer_fee"] or 0),
        "초과금환수": float(l["clawback"] or 0),
        "원천세": float(l["withholding_tax"] or 0),
        "비고": l.get("remarks") or "",
        "_emp": l["employee_id"],
    } for l in lines])

    money_cfg = {c: st.column_config.NumberColumn(c, format="%.2f")
                 for c in COLS if c != "비고"}
    edited = st.data_editor(
        df.drop(columns=["_id", "_emp"]),
        use_container_width=True, hide_index=True,
        disabled=confirmed or not editable,
        key=f"pay_{pm['id']}",
        column_config={"구분": st.column_config.TextColumn("구분", disabled=True),
                       **money_cfg})

    auto_ot = st.checkbox("저장 시 OT금액을 'OT시간 × 직원별 OT단가'로 자동 계산",
                          value=True, disabled=confirmed)

    # ---- 합계 미리보기 ----
    sub = (edited["급여"] + edited["일비"] + edited["OT금액"] + edited["중식"]
           + edited["차량지원"] + edited["통신비"] + edited["송금수수료"]
           + edited["초과금환수"])
    net = sub - edited["원천세"]
    m = st.columns(4)
    m[0].metric("회사 지급 총액 (소계 합)", f"${sub.sum():,.2f}")
    m[1].metric("원천세 합계", f"${edited['원천세'].sum():,.2f}")
    m[2].metric("차감 지급액 합계", f"${net.sum():,.2f}")
    m[3].metric("인원", f"{len(edited)}명")

    # ---- 버튼들 ----
    pay_bank = None
    if not confirmed and admin:
        banks = helpers.load_bank_accounts()
        if banks:
            pay_bank = st.selectbox("급여 출금 통장 (확정 시 기록)",
                                    [None] + banks,
                                    format_func=helpers.bank_label)
    b = st.columns(4)
    if not confirmed and editable and b[0].button("💾 저장", type="primary"):
        save_lines(db, pm, df, edited, auto_ot)

    if not confirmed and admin and b[1].button("✅ 급여 확정 (관리자)"):
        confirm_month(db, pm, year, month, pay_bank)

    if confirmed and admin and b[1].button("↩️ 확정 취소 (관리자)"):
        unconfirm_month(db, pm, year, month)

    # ---- 엑셀 출력 ----
    bank_map = {e["id"]: e for e in db.table("employees").select(
        "id,bank_name,account_no,routing_no,account_type,zelle").execute().data}
    xl_lines = []
    for i, r in edited.iterrows():
        s = (r["급여"] + r["일비"] + r["OT금액"] + r["중식"] + r["차량지원"]
             + r["통신비"] + r["송금수수료"] + r["초과금환수"])
        bank = bank_map.get(df.iloc[i]["_emp"], {})
        xl_lines.append({
            "name": r["구분"], "base_salary": r["급여"], "per_diem": r["일비"],
            "ot_amount": r["OT금액"], "meal": r["중식"], "vehicle": r["차량지원"],
            "telecom": r["통신비"], "transfer_fee": r["송금수수료"],
            "clawback": r["초과금환수"], "subtotal": s,
            "withholding_tax": r["원천세"], "net_pay": s - r["원천세"],
            "remarks": r["비고"],
            "bank_name": bank.get("bank_name"),
            "account_no": bank.get("account_no"),
            "routing_no": bank.get("routing_no"),
            "account_type": bank.get("account_type"),
            "zelle": bank.get("zelle"),
        })
    b[2].download_button(
        "⬇️ 엑셀 출력", data=build_payroll_excel(year, month, xl_lines),
        file_name=f"미국법인_{year}년{month}월_급여지급세부내역.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    if confirmed:
        st.success("확정된 급여입니다. 회사 지급 총액이 공통비 > 노무비 > 급여,연금 "
                   "계정으로 자금 원장에 자동 반영되어 있습니다.")


# ------------------------------------------------------------
def create_month(db, year, month):
    emps = (db.table("employees").select("*")
            .eq("is_active", True).order("name").execute().data)
    if not emps:
        st.error("재직 중인 직원이 없습니다. 직원 관리에서 먼저 등록하세요.")
        return
    pm = db.table("payroll_months").insert(
        {"pay_year": year, "pay_month": month}).execute().data[0]
    db.table("payroll_lines").insert([{
        "payroll_month_id": pm["id"], "employee_id": e["id"],
        "base_salary": e.get("base_salary") or 0,
        "per_diem": e.get("per_diem") or 0,
        "meal": e.get("meal_support") or 0,
        "vehicle": e.get("vehicle_support") or 0,
        "telecom": e.get("telecom_support") or 0,
        "transfer_fee": e.get("transfer_fee") or 0,
    } for e in emps]).execute()
    st.success(f"{len(emps)}명의 기본자료로 {year}년 {month}월 시트를 생성했습니다.")
    st.rerun()


def save_lines(db, pm, df, edited, auto_ot):
    rates = {e["id"]: float(e.get("ot_rate") or 0)
             for e in db.table("employees").select("id,ot_rate").execute().data}
    for i, r in edited.iterrows():
        lid = int(df.iloc[i]["_id"])
        emp = df.iloc[i]["_emp"]
        ot_amt = float(r["OT금액"] or 0)
        if auto_ot:
            ot_amt = round(float(r["OT시간"] or 0) * rates.get(emp, 0), 2)
        sub = (float(r["급여"]) + float(r["일비"]) + ot_amt + float(r["중식"])
               + float(r["차량지원"]) + float(r["통신비"])
               + float(r["송금수수료"]) + float(r["초과금환수"]))
        db.table("payroll_lines").update({
            "base_salary": float(r["급여"]), "per_diem": float(r["일비"]),
            "ot_hours": float(r["OT시간"]), "ot_amount": ot_amt,
            "meal": float(r["중식"]), "vehicle": float(r["차량지원"]),
            "telecom": float(r["통신비"]),
            "transfer_fee": float(r["송금수수료"]),
            "clawback": float(r["초과금환수"]),
            "subtotal": sub,
            "withholding_tax": float(r["원천세"]),
            "net_pay": sub - float(r["원천세"]),
            "remarks": str(r["비고"] or ""),
        }).eq("id", lid).execute()
    st.success("저장되었습니다." + (" (OT금액 자동 계산 적용)" if auto_ot else ""))
    st.rerun()


def _pay_desc(year, month):
    return f"{year}-{month:02d} 급여 (월급여 확정)"


def confirm_month(db, pm, year, month, pay_bank=None):
    lines = (db.table("payroll_lines").select("subtotal")
             .eq("payroll_month_id", pm["id"]).execute().data)
    total = sum(float(l["subtotal"] or 0) for l in lines)
    acc = salary_account_id()
    prj = common_project_id(db)
    db.table("transactions").insert({
        "tx_type": "지출", "project_id": prj, "account_id": acc,
        "bank_account_id": pay_bank["id"] if pay_bank else None,
        "amount": total, "tx_date": str(date.today()),
        "description": _pay_desc(year, month),
        "created_by": st.session_state["user"]["id"],
    }).execute()
    db.table("payroll_months").update({
        "status": "확정",
        "confirmed_by": st.session_state["user"]["id"],
        "confirmed_at": datetime.now().isoformat(),
    }).eq("id", pm["id"]).execute()
    st.success(f"확정 완료 — 공통비(급여,연금)로 ${total:,.2f} 자동 반영되었습니다.")
    st.rerun()


def unconfirm_month(db, pm, year, month):
    db.table("transactions").delete() \
        .eq("description", _pay_desc(year, month)) \
        .eq("tx_type", "지출").execute()
    db.table("payroll_months").update({"status": "작성중"}) \
        .eq("id", pm["id"]).execute()
    st.success("확정이 취소되었습니다. (자금 반영분 삭제)")
    st.rerun()
