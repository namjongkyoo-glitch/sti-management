"""대출 관리: 대출 등록 / 이자·상환 기록 / 잔액 현황 (이자는 금융비용 자동 반영)"""
import pandas as pd
import streamlit as st
from datetime import date

import auth
import helpers
from db import get_db


def interest_account_id():
    for a in helpers.load_accounts():
        if a["code"] == "4101":  # 대여·대출이자
            return a["id"]
    return None


def common_project_id(db):
    rows = (db.table("projects").select("id")
            .eq("is_common_pool", True).limit(1).execute().data)
    return rows[0]["id"] if rows else None


def render():
    helpers.page_title("대출 관리")
    db = get_db()
    editable = auth.can_edit("loans")

    loans = db.table("loans").select("*").order("start_date").execute().data
    pays = db.table("loan_payments").select("*").execute().data

    # loan_id별 누계를 명시적으로 집계 (타입 불일치/매칭오류 방지)
    paid_by_loan = {}
    int_by_loan = {}
    for p in pays:
        lid = p.get("loan_id")
        if lid is None:
            continue
        lid = int(lid)
        paid_by_loan[lid] = paid_by_loan.get(lid, 0.0) + \
            float(p.get("principal_paid") or 0)
        int_by_loan[lid] = int_by_loan.get(lid, 0.0) + \
            float(p.get("interest_paid") or 0)

    pdf = pd.DataFrame(pays) if pays else pd.DataFrame(
        columns=["loan_id", "pay_date", "principal_paid", "interest_paid",
                 "balance_after"])
    if not pdf.empty:
        for c in ["principal_paid", "interest_paid"]:
            pdf[c] = pdf[c].astype(float)

    # ---- 전체 현황 ----
    rows = []
    for l in loans:
        lid = int(l["id"])
        repaid = paid_by_loan.get(lid, 0.0)
        interest = int_by_loan.get(lid, 0.0)
        rows.append({"loan": l, "원금": float(l["principal"] or 0),
                     "상환누계": repaid, "잔액": float(l["principal"] or 0) - repaid,
                     "이자누계": interest})

    m = st.columns(4)
    m[0].metric("대출 건수", f"{len(loans)}건")
    m[1].metric("총 대출 원금", f"${sum(r['원금'] for r in rows):,.0f}")
    m[2].metric("총 잔액", f"${sum(r['잔액'] for r in rows):,.0f}")
    m[3].metric("누적 이자 지급", f"${sum(r['이자누계'] for r in rows):,.0f}")

    # ---- 대출 등록 ----
    if editable:
        with st.expander("➕ 대출 등록"):
            with st.form("new_loan", clear_on_submit=True):
                c = st.columns(3)
                lender = c[0].text_input("대출 기관 * (Citi, Shinhan, 본사 등)")
                loan_name = c[1].text_input("대출명")
                principal = c[2].number_input("원금 (USD) *", min_value=0.0,
                                              step=1000.0, format="%.2f")
                c2 = st.columns(4)
                rate = c2[0].number_input("연이율 (%)", min_value=0.0,
                                          step=0.1, format="%.3f")
                start = c2[1].date_input("실행일", value=date.today())
                maturity = c2[2].date_input("만기일", value=None)
                pay_day = c2[3].number_input("매월 상환일", min_value=0,
                                             max_value=31, value=0)
                notes = st.text_input("비고")
                ok = st.form_submit_button("등록", type="primary")
            if ok:
                if not lender or principal <= 0:
                    st.error("대출 기관과 원금을 입력하세요.")
                else:
                    db.table("loans").insert({
                        "lender": lender, "loan_name": loan_name,
                        "principal": principal, "interest_rate": rate,
                        "start_date": str(start),
                        "maturity_date": str(maturity) if maturity else None,
                        "payment_day": pay_day or None, "notes": notes,
                    }).execute()
                    st.success("대출이 등록되었습니다.")
                    st.rerun()

    if not loans:
        st.info("등록된 대출이 없습니다.")
        return

    # ---- 대출 목록 ----
    sdf = pd.DataFrame([{
        "기관": r["loan"]["lender"],
        "대출명": r["loan"].get("loan_name") or "-",
        "이율": f"{float(r['loan'].get('interest_rate') or 0):.2f}%",
        "실행일": r["loan"].get("start_date") or "-",
        "만기일": r["loan"].get("maturity_date") or "-",
        "원금": r["원금"],
        "상환누계": r["상환누계"],
        "잔액": r["잔액"],
        "이자누계": r["이자누계"],
        "상태": "✅ 완제" if r["loan"]["status"] == "완제" else "🟢 진행",
    } for r in rows])

    def _row_style(row):
        done = row["상태"].startswith("✅")
        bg = "background-color: rgba(59,130,246,0.18)" if done \
            else "background-color: rgba(34,197,94,0.12)"
        return [bg] * len(row)

    styled = (sdf.style
              .apply(_row_style, axis=1)
              .format({"원금": "${:,.0f}", "상환누계": "${:,.0f}",
                       "잔액": "${:,.0f}", "이자누계": "${:,.0f}"}))
    st.dataframe(styled, use_container_width=True, hide_index=True)
    st.caption("🟢 진행 (초록) · ✅ 완제 (파랑)")

    st.divider()
    sel = st.selectbox("대출 상세 / 이자·상환 기록", rows,
                       format_func=lambda r:
                       f"{r['loan']['lender']} {r['loan'].get('loan_name') or ''} "
                       f"· 실행 {r['loan'].get('start_date') or '-'} "
                       f"· 원금 ${r['원금']:,.0f} "
                       f"(잔액 ${r['잔액']:,.0f})")
    loan = sel["loan"]

    # ---- 이자/상환 입력 ----
    if editable:
        with st.form(f"pay_{loan['id']}", clear_on_submit=True):
            c = st.columns(4)
            pay_date = c[0].date_input("지급일", value=date.today())
            principal_paid = c[1].number_input("원금 상환", min_value=0.0,
                                               step=100.0, format="%.2f")
            interest_paid = c[2].number_input("이자 지급", min_value=0.0,
                                              step=10.0, format="%.2f")
            post_interest = c[3].checkbox("이자를 자금원장에 반영\n(금융비용>대출이자)",
                                          value=True)
            pay_bank = st.selectbox("출금 통장 (이자 기록용)",
                                    [None] + helpers.load_bank_accounts(),
                                    format_func=helpers.bank_label,
                                    key=f"lb_{loan['id']}")
            notes = st.text_input("비고", key=f"pn_{loan['id']}")
            ok = st.form_submit_button("기록 추가", type="primary")
        if ok:
            if principal_paid <= 0 and interest_paid <= 0:
                st.error("원금 또는 이자를 입력하세요.")
            else:
                new_balance = sel["잔액"] - principal_paid
                tx_id = None
                if interest_paid > 0 and post_interest:
                    tx = db.table("transactions").insert({
                        "tx_type": "지출", "project_id": common_project_id(db),
                        "account_id": interest_account_id(),
                        "bank_account_id": pay_bank["id"] if pay_bank else None,
                        "amount": interest_paid, "tx_date": str(pay_date),
                        "description": f"{loan['lender']} 대출이자",
                        "created_by": st.session_state["user"]["id"],
                    }).execute().data[0]
                    tx_id = tx["id"]
                db.table("loan_payments").insert({
                    "loan_id": loan["id"], "pay_date": str(pay_date),
                    "principal_paid": principal_paid,
                    "interest_paid": interest_paid,
                    "balance_after": new_balance,
                    "transaction_id": tx_id, "notes": notes,
                }).execute()
                if new_balance <= 0:
                    db.table("loans").update({"status": "완제"}) \
                        .eq("id", loan["id"]).execute()
                st.success("기록되었습니다."
                           + (" 이자가 자금 원장에 반영되었습니다."
                              if interest_paid > 0 and post_interest else ""))
                st.rerun()
        st.caption("※ 원금 상환은 대출 원장에서만 관리되고, 이자만 비용으로 "
                   "자금 원장(금융비용 > 대여·대출이자, 공통비)에 반영됩니다.")

    # ---- 상환 이력 ----
    if not pdf.empty:
        lp = pdf[pdf["loan_id"].apply(
            lambda x: x is not None and int(x) == int(loan["id"]))]
    else:
        lp = pdf
    if not lp.empty:
        hist = lp.sort_values("pay_date", ascending=False)
        st.markdown("**이자/상환 이력**")
        st.dataframe(pd.DataFrame([{
            "지급일": r["pay_date"],
            "원금 상환": f"${float(r['principal_paid']):,.2f}",
            "이자": f"${float(r['interest_paid']):,.2f}",
            "잔액": f"${float(r['balance_after'] or 0):,.2f}",
            "비고": r.get("notes") or "",
        } for _, r in hist.iterrows()]),
            use_container_width=True, hide_index=True)

    # ---- 전체 상환/이자 이력 (모든 대출) ----
    st.divider()
    st.markdown("### 📋 전체 상환/이자 이력")
    if pdf.empty:
        st.info("상환/이자 기록이 없습니다.")
    else:
        loan_info = {int(l["id"]):
                     f"{l['lender']} {l.get('loan_name') or ''} "
                     f"(실행 {l.get('start_date') or '-'})"
                     for l in loans}
        allh = pdf.sort_values("pay_date", ascending=False)
        rows_all = []
        for _, r in allh.iterrows():
            lid = r.get("loan_id")
            rows_all.append({
                "지급일": r["pay_date"],
                "대출": loan_info.get(int(lid), "-") if lid is not None else "-",
                "원금 상환": float(r["principal_paid"] or 0),
                "이자 지급": float(r["interest_paid"] or 0),
                "비고": r.get("notes") or "",
            })
        adf = pd.DataFrame(rows_all)
        m = st.columns(3)
        m[0].metric("총 원금 상환", f"${adf['원금 상환'].sum():,.2f}")
        m[1].metric("총 이자 지급", f"${adf['이자 지급'].sum():,.2f}")
        m[2].metric("상환 건수", f"{len(adf)}건")
        show = adf.copy()
        show["원금 상환"] = show["원금 상환"].map(lambda x: f"${x:,.2f}")
        show["이자 지급"] = show["이자 지급"].map(lambda x: f"${x:,.2f}")
        st.dataframe(show, use_container_width=True, hide_index=True)
        # 엑셀 다운로드
        from io import BytesIO
        buf = BytesIO()
        adf.to_excel(buf, index=False)
        st.download_button("⬇️ 전체 상환이력 엑셀", data=buf.getvalue(),
                           file_name=f"대출_전체상환이력_{date.today()}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet")
