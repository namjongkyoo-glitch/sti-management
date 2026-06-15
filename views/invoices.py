"""인보이스 생성/관리: 프로젝트 연동, 발행/입금 상태, INVOICE 엑셀 출력"""
import pandas as pd
import streamlit as st
from datetime import date

import auth
import helpers
from db import get_db
from invoice_excel import build_invoice_excel, DEFAULT_BANK

I_BADGE = {"작성중": "🟡 작성중", "발행": "📤 발행", "입금완료": "✅ 입금완료"}


def _f(v):
    try:
        f = float(v)
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def _t(v):
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def next_invoice_no(db, inv_date):
    prefix = str(inv_date)[:7].replace("-", "")  # YYYYMM
    rows = (db.table("invoices").select("invoice_no")
            .like("invoice_no", f"{prefix}%").execute().data)
    return f"{prefix}{len(rows) + 1:03d}"


def prev_billed_for(db, project_id, exclude_id=None):
    """동일 프로젝트의 기존 인보이스 청구 합계"""
    if not project_id:
        return 0.0
    invs = (db.table("invoices").select("id")
            .eq("project_id", project_id).execute().data)
    ids = [i["id"] for i in invs if i["id"] != exclude_id]
    if not ids:
        return 0.0
    lines = (db.table("invoice_lines").select("invoice_id,quantity,unit_price")
             .in_("invoice_id", ids).execute().data)
    return sum(_f(l["quantity"]) * _f(l["unit_price"]) for l in lines)


def income_account_id():
    accs = helpers.load_accounts()
    for a in accs:
        if a["name_kr"] == "기성금/계약수입":
            return a["id"]
    for a in accs:
        if a["level"] == 2 and str(a.get("code", "")).startswith("1"):
            return a["id"]
    return None


def render():
    helpers.page_title("인보이스 관리")
    db = get_db()
    editable = auth.can_edit("invoices")

    if st.session_state.get("inv_open"):
        detail_screen(db, st.session_state["inv_open"], editable)
    else:
        list_screen(db, editable)


# ============================================================
def list_screen(db, editable):
    if editable:
        with st.expander("➕ 새 인보이스 작성"):
            prjs = (db.table("projects").select("*")
                    .eq("is_common_pool", False).neq("status", "취소")
                    .order("code").execute().data)
            c = st.columns([2.2, 1.2, 1])
            prj = c[0].selectbox(
                "프로젝트 (선택 시 고객사/계약금액/이전 청구액 자동)",
                [None] + prjs,
                format_func=lambda p: "직접 입력"
                if p is None else f"{p['code']} {p['name']}")
            inv_date = c[1].date_input("인보이스 날짜", value=date.today())
            if c[2].button("작성 시작", type="primary"):
                client_name = client_addr = ""
                contract = prev = 0.0
                if prj:
                    client_name = prj.get("client") or ""
                    contract = _f(prj.get("contract_amount"))
                    prev = prev_billed_for(db, prj["id"])
                row = db.table("invoices").insert({
                    "invoice_no": next_invoice_no(db, inv_date),
                    "invoice_date": str(inv_date),
                    "project_id": prj["id"] if prj else None,
                    "client_name": client_name,
                    "client_address": client_addr,
                    "job": prj["name"] if prj else "",
                    "contract_amount": contract, "prev_billed": prev,
                    "bank_info": DEFAULT_BANK,
                    "created_by": st.session_state["user"]["id"],
                }).execute().data[0]
                db.table("invoice_lines").insert({
                    "invoice_id": row["id"], "description": "",
                    "quantity": 1, "unit_price": 0, "sort_order": 0,
                }).execute()
                st.session_state["inv_open"] = row["id"]
                st.rerun()

    invs = (db.table("invoices").select("*")
            .order("invoice_no", desc=True).execute().data)
    st.subheader(f"인보이스 목록 ({len(invs)})")
    if not invs:
        st.info("작성된 인보이스가 없습니다.")
        return
    lines = db.table("invoice_lines").select(
        "invoice_id,quantity,unit_price").execute().data
    amt_map = {}
    for l in lines:
        amt_map[l["invoice_id"]] = amt_map.get(l["invoice_id"], 0) + \
            _f(l["quantity"]) * _f(l["unit_price"])
    prj_map = {p["id"]: p["code"] for p in
               db.table("projects").select("id,code").execute().data}
    ispec = [1.4, 1.2, 2.4, 1.4, 1.3, 1.1, 0.8]
    helpers.list_header(ispec, ["인보이스", "발행일", "고객/프로젝트",
                                "금액", "상태", "입금일", ""])
    for v in invs:
        with st.container(border=True):
            c = st.columns(ispec)
            c[0].markdown(f"**{v['invoice_no']}**")
            c[1].write(str(v["invoice_date"])[:10])
            c[2].write(f"{v.get('client_name') or '-'} · "
                       f"{prj_map.get(v.get('project_id'), '직접입력')}")
            c[3].write(f"${amt_map.get(v['id'], 0):,.2f}")
            c[4].write(I_BADGE.get(v["status"], v["status"]))
            c[5].write(str(v.get("paid_date") or "")[:10])
            if c[6].button("열기", key=f"inv_{v['id']}"):
                st.session_state["inv_open"] = v["id"]
                st.rerun()


# ============================================================
def detail_screen(db, iid, editable):
    rows = db.table("invoices").select("*").eq("id", iid).execute().data
    if not rows:
        st.session_state["inv_open"] = None
        st.rerun()
    inv = rows[0]
    admin = auth.is_admin()
    locked = inv["status"] == "입금완료" or not editable
    k = f"inv{iid}_"

    top = st.columns([1, 4.5, 2.5])
    if top[0].button("← 목록"):
        st.session_state["inv_open"] = None
        st.rerun()
    top[1].subheader(f"INVOICE {inv['invoice_no']}")
    top[2].markdown(f"### {I_BADGE.get(inv['status'], inv['status'])}")

    # ---- 헤더 정보 ----
    c = st.columns(3)
    inv_date = c[0].date_input(
        "DATE", value=pd.to_datetime(inv["invoice_date"]).date(),
        disabled=locked, key=k + "d")
    po_no = c[1].text_input("PO No", inv.get("po_no") or "",
                            disabled=locked, key=k + "po")
    pay_terms = c[2].text_input("PAYMENT TERMS",
                                inv.get("payment_terms") or "Net 30 Days Balance",
                                disabled=locked, key=k + "pt")
    c = st.columns(2)
    client_name = c[0].text_input("TO (고객사명)", inv.get("client_name") or "",
                                  disabled=locked, key=k + "cn")
    client_addr = c[1].text_input("고객사 주소", inv.get("client_address") or "",
                                  disabled=locked, key=k + "ca")
    job = st.text_input("JOB", inv.get("job") or "", disabled=locked,
                        key=k + "job")
    c = st.columns(2)
    position_note = c[0].text_input("1) Position",
                                    inv.get("position_note") or "",
                                    disabled=locked, key=k + "pos",
                                    placeholder="예: - Control Manager: Yun Jonggeun")
    period_note = c[1].text_input("2) Period", inv.get("period_note") or "",
                                  disabled=locked, key=k + "per",
                                  placeholder="예: 2023-04-01 ~ 2026-04-30")

    # ---- 품목 ----
    st.markdown("**품목 (DESCRIPTION / QUANTITY / AMOUNT)**")
    lines = (db.table("invoice_lines").select("*").eq("invoice_id", iid)
             .order("sort_order").execute().data)
    ldf = pd.DataFrame([{
        "DESCRIPTION": l.get("description") or "",
        "QUANTITY": _f(l.get("quantity")) or 1.0,
        "단가(AMOUNT)": _f(l.get("unit_price")),
    } for l in lines]) if lines else pd.DataFrame(
        columns=["DESCRIPTION", "QUANTITY", "단가(AMOUNT)"])
    edited = st.data_editor(
        ldf, num_rows="dynamic", use_container_width=True, hide_index=True,
        disabled=locked, key=k + "lines",
        column_config={
            "QUANTITY": st.column_config.NumberColumn("QUANTITY",
                                                      format="%.2f"),
            "단가(AMOUNT)": st.column_config.NumberColumn("단가(AMOUNT $)",
                                                        format="%.2f"),
        })
    cur_total = sum(_f(r.get("QUANTITY")) * _f(r.get("단가(AMOUNT)"))
                    for _, r in edited.iterrows())

    # ---- 신청 내역 ----
    st.markdown("**3) 신청 내역**")
    c = st.columns(4)
    contract = c[0].number_input("계약금액",
                                 value=_f(inv.get("contract_amount")),
                                 step=1000.0, format="%.2f",
                                 disabled=locked, key=k + "ct")
    c[1].metric("금회 신청금액", f"${cur_total:,.2f}")
    auto_prev = prev_billed_for(db, inv.get("project_id"), exclude_id=iid)
    prev = c[2].number_input("이전 신청 금액",
                             value=_f(inv.get("prev_billed")) or auto_prev,
                             step=1000.0, format="%.2f",
                             disabled=locked, key=k + "pv",
                             help=f"동일 프로젝트 기존 인보이스 합계: ${auto_prev:,.2f}")
    c[3].metric("잔여금액", f"${contract - prev - cur_total:,.2f}")
    st.caption(f"TOTAL DUE (이전 제외 잔여 청구 대상): ${contract - prev:,.2f}")

    bank_info = st.text_area("하단 은행 정보", inv.get("bank_info") or DEFAULT_BANK,
                             height=120, disabled=locked, key=k + "bk")

    current = {
        "invoice_date": str(inv_date), "po_no": po_no,
        "payment_terms": pay_terms, "client_name": client_name,
        "client_address": client_addr, "job": job,
        "position_note": position_note, "period_note": period_note,
        "contract_amount": contract, "prev_billed": prev,
        "bank_info": bank_info,
    }

    def save_all():
        db.table("invoices").update(current).eq("id", iid).execute()
        db.table("invoice_lines").delete().eq("invoice_id", iid).execute()
        rows = []
        for i, r in edited.iterrows():
            if not _t(r.get("DESCRIPTION")):
                continue
            rows.append({"invoice_id": iid,
                         "description": _t(r.get("DESCRIPTION")),
                         "quantity": _f(r.get("QUANTITY")) or 1,
                         "unit_price": _f(r.get("단가(AMOUNT)")),
                         "sort_order": i})
        if rows:
            db.table("invoice_lines").insert(rows).execute()

    st.divider()
    b = st.columns(4)
    if not locked and b[0].button("💾 저장", type="primary"):
        save_all()
        st.success("저장되었습니다.")
        st.rerun()

    # ---- 상태 워크플로우 ----
    if inv["status"] == "작성중" and editable:
        if b[1].button("📤 발행 처리"):
            save_all()
            db.table("invoices").update({"status": "발행"}).eq("id", iid).execute()
            st.rerun()
    elif inv["status"] == "발행" and editable:
        with b[1].popover("✅ 입금 완료 처리"):
            pdate = st.date_input("입금일", value=date.today(), key=k + "pd")
            banks = helpers.load_bank_accounts()
            bank = st.selectbox("입금 통장", [None] + banks,
                                format_func=helpers.bank_label, key=k + "pb")
            make_tx = st.checkbox("수입 거래 자동 등록 (자금 원장 반영)",
                                  value=True, key=k + "mt")
            if st.button("입금 확정", type="primary", key=k + "pcf"):
                db.table("invoices").update({
                    "status": "입금완료", "paid_date": str(pdate),
                }).eq("id", iid).execute()
                if make_tx and cur_total > 0:
                    db.table("transactions").insert({
                        "tx_type": "수입", "tx_date": str(pdate),
                        "project_id": inv.get("project_id"),
                        "account_id": income_account_id(),
                        "bank_account_id": bank["id"] if bank else None,
                        "amount": cur_total,
                        "description": f"INVOICE {inv['invoice_no']} 입금",
                        "created_by": st.session_state["user"]["id"],
                    }).execute()
                st.rerun()
        if b[2].button("↩️ 작성중으로 되돌리기"):
            db.table("invoices").update({"status": "작성중"}).eq("id", iid).execute()
            st.rerun()
    elif inv["status"] == "입금완료":
        st.success(f"입금 완료 — {str(inv.get('paid_date') or '')[:10]}")

    # ---- 엑셀 출력 ----
    xl_lines = [{"description": _t(r.get("DESCRIPTION")),
                 "quantity": _f(r.get("QUANTITY")) or 1,
                 "unit_price": _f(r.get("단가(AMOUNT)"))}
                for _, r in edited.iterrows() if _t(r.get("DESCRIPTION"))]
    b[3].download_button(
        "⬇️ INVOICE 엑셀 출력",
        data=build_invoice_excel({**inv, **current}, xl_lines),
        file_name=helpers.safe_filename("INVOICE", inv["invoice_no"], inv.get("job") or inv.get("client_name")) + ".xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True)

    # ---- 관리자 삭제 ----
    if admin and inv["status"] != "입금완료":
        st.divider()
        with st.popover("🗑️ 인보이스 삭제 (관리자)"):
            st.warning(f"'{inv['invoice_no']}' 인보이스가 영구 삭제됩니다.")
            confirm = st.text_input("확인을 위해 인보이스 번호 입력", key=k + "del")
            if st.button("삭제 확정", type="primary", key=k + "delb"):
                if confirm.strip() == inv["invoice_no"]:
                    db.table("invoices").delete().eq("id", iid).execute()
                    st.session_state["inv_open"] = None
                    st.rerun()
                else:
                    st.error("번호가 일치하지 않습니다.")
