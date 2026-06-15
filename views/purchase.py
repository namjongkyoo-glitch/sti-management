"""발주서(PO) 관리: 프로젝트 수주분의 외주업체 발주, 금액 수정→예산 반영, 엑셀 출력"""
import pandas as pd
import streamlit as st
from datetime import date

import auth
import helpers
from db import get_db
from po_excel import build_po_excel

PO_BADGE = {"작성중": "🟡 작성중", "발행": "📤 발행"}


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


def next_po_no(db, pjt_no):
    base = (pjt_no or "STIUSA").replace(" ", "")
    ym = date.today().strftime("%m%Y")
    prefix = f"STIUSA-{ym}"
    rows = (db.table("purchase_orders").select("po_no")
            .like("po_no", f"{prefix}%").execute().data)
    return f"{prefix}-{len(rows) + 1:02d}"


def render():
    helpers.page_title("발주서(PO)")
    st.info("🏗️ 수주된 프로젝트의 **외주 협력업체 발주서**입니다. "
            "금액을 수정하면 해당 프로젝트 예산(직접비)에 반영됩니다.")
    db = get_db()
    editable = auth.can_edit("purchase")

    if st.session_state.get("po_open"):
        detail_screen(db, st.session_state["po_open"], editable)
    else:
        list_screen(db, editable)


# ============================================================
def list_screen(db, editable):
    if editable:
        with st.expander("➕ 새 발주서 작성"):
            prjs = (db.table("projects").select("*")
                    .eq("is_common_pool", False).neq("status", "취소")
                    .order("code").execute().data)
            c = st.columns([2.2, 2.2, 1])
            prj = c[0].selectbox(
                "프로젝트 *", prjs,
                format_func=lambda p: f"{p['code']} {p['name']}") \
                if prjs else None
            # 프로젝트에 할당된 협력업체 우선 표시
            vendors = db.table("vendors").select("*").order("name").execute().data
            vendor = c[1].selectbox(
                "외주 협력업체 *", vendors,
                format_func=lambda v: f"{v['name']} ({v.get('trade') or '-'})") \
                if vendors else None
            if c[2].button("작성 시작", type="primary",
                           disabled=not (prj and vendor)):
                pjt_no = ""
                # 견적/프로젝트에서 PJT No 유추 (없으면 코드)
                pjt_no = prj.get("code") or ""
                row = db.table("purchase_orders").insert({
                    "po_no": next_po_no(db, pjt_no),
                    "po_date": str(date.today()),
                    "project_id": prj["id"], "vendor_id": vendor["id"],
                    "pjt_no": pjt_no,
                    "supplier_name": vendor["name"],
                    "supplier_address": vendor.get("address") or "",
                    "attn": vendor.get("contact") or "",
                    "supplier_tel": vendor.get("phone") or "",
                    "supplier_email": vendor.get("email") or "",
                    "destination": "STI AD USA, INC.",
                    "payment_terms": "50% TT upon receipt of invoice & "
                                     "progress report / Balance 50% upon "
                                     "final invoice",
                    "budget_account_id": helpers.account_id_by_name("외주비", 2),
                    "created_by": st.session_state["user"]["id"],
                }).execute().data[0]
                db.table("po_lines").insert({
                    "po_id": row["id"], "description": "", "unit": "LOT",
                    "qty": 1, "unit_price": 0, "sort_order": 0,
                }).execute()
                st.session_state["po_open"] = row["id"]
                st.rerun()

    pos = (db.table("purchase_orders").select("*")
           .order("created_at", desc=True).execute().data)
    st.subheader(f"발주서 목록 ({len(pos)})")
    if not pos:
        st.info("작성된 발주서가 없습니다.")
        return
    lines = db.table("po_lines").select(
        "po_id,qty,unit_price").execute().data
    amt = {}
    for l in lines:
        amt[l["po_id"]] = amt.get(l["po_id"], 0) + _f(l["qty"]) * _f(l["unit_price"])
    prj_map = {p["id"]: p["code"] for p in
               db.table("projects").select("id,code").execute().data}
    pospec = [1.8, 1.3, 2.4, 1.4, 1.1, 0.8]
    helpers.list_header(pospec, ["PO 번호", "프로젝트", "공급업체",
                                 "금액", "상태", ""])
    for p in pos:
        with st.container(border=True):
            c = st.columns(pospec)
            c[0].markdown(f"**{p['po_no']}**")
            c[1].write(prj_map.get(p.get("project_id"), "-"))
            c[2].write(p.get("supplier_name") or "-")
            c[3].write(f"${amt.get(p['id'], 0):,.2f}")
            c[4].write(PO_BADGE.get(p["status"], p["status"]))
            if c[5].button("열기", key=f"po_{p['id']}"):
                st.session_state["po_open"] = p["id"]
                st.rerun()


# ============================================================
def detail_screen(db, pid, editable):
    rows = db.table("purchase_orders").select("*").eq("id", pid).execute().data
    if not rows:
        st.session_state["po_open"] = None
        st.rerun()
    po = rows[0]
    admin = auth.is_admin()
    k = f"po{pid}_"

    top = st.columns([1, 5, 2])
    if top[0].button("← 목록"):
        st.session_state["po_open"] = None
        st.rerun()
    top[1].subheader(f"PO {po['po_no']}")
    top[2].markdown(f"### {PO_BADGE.get(po['status'], po['status'])}")

    # ---- 헤더 정보 ----
    c = st.columns(3)
    po_date = c[0].date_input("PO Date",
                              value=pd.to_datetime(po["po_date"]).date(),
                              disabled=not editable, key=k+"d")
    pjt_no = c[1].text_input("PJT No", po.get("pjt_no") or "",
                             disabled=not editable, key=k+"pjt")
    quotation_ref = c[2].text_input("Quotation Ref", po.get("quotation_ref") or "",
                                    disabled=not editable, key=k+"qr")
    c = st.columns(2)
    supplier_name = c[0].text_input("Supplier Name", po.get("supplier_name") or "",
                                    disabled=not editable, key=k+"sn")
    supplier_addr = c[1].text_input("Supplier Address", po.get("supplier_address") or "",
                                    disabled=not editable, key=k+"sa")
    c = st.columns(3)
    attn = c[0].text_input("Attn", po.get("attn") or "", disabled=not editable, key=k+"at")
    supplier_tel = c[1].text_input("Supplier TEL", po.get("supplier_tel") or "",
                                   disabled=not editable, key=k+"st")
    supplier_email = c[2].text_input("Supplier Email", po.get("supplier_email") or "",
                                     disabled=not editable, key=k+"se")
    c = st.columns(2)
    destination = c[0].text_input("Destination", po.get("destination") or "",
                                  disabled=not editable, key=k+"de")
    dest_addr = c[1].text_input("Destination 주소", po.get("destination_addr") or "",
                                disabled=not editable, key=k+"da")
    payment_terms = st.text_input("Payment Terms", po.get("payment_terms") or "",
                                  disabled=not editable, key=k+"pt")
    remark = st.text_input("Remark", po.get("remark") or "",
                           disabled=not editable, key=k+"rm")

    # ---- 품목 ----
    st.markdown("**품목 (Description / Qty / Unit Price)**")
    lines = (db.table("po_lines").select("*").eq("po_id", pid)
             .order("sort_order").execute().data)
    ldf = pd.DataFrame([{
        "Description": l.get("description") or "",
        "Unit": l.get("unit") or "LOT",
        "Qty": _f(l.get("qty")) or 1.0,
        "Unit Price": _f(l.get("unit_price")),
        "Remark": l.get("remark") or "",
    } for l in lines]) if lines else pd.DataFrame(
        columns=["Description", "Unit", "Qty", "Unit Price", "Remark"])
    edited = st.data_editor(
        ldf, num_rows="dynamic", use_container_width=True, hide_index=True,
        disabled=not editable, key=k+"lines",
        column_config={
            "Qty": st.column_config.NumberColumn("Qty", format="%.2f"),
            "Unit Price": st.column_config.NumberColumn("Unit Price($)",
                                                        format="%.2f"),
        })
    total = sum(_f(r.get("Qty")) * _f(r.get("Unit Price"))
                for _, r in edited.iterrows())
    st.metric("Total Amount", f"${total:,.2f}")

    current = {
        "po_date": str(po_date), "pjt_no": pjt_no, "quotation_ref": quotation_ref,
        "supplier_name": supplier_name, "supplier_address": supplier_addr,
        "attn": attn, "supplier_tel": supplier_tel, "supplier_email": supplier_email,
        "destination": destination, "destination_addr": dest_addr,
        "payment_terms": payment_terms, "remark": remark,
    }

    def save_all():
        db.table("purchase_orders").update(current).eq("id", pid).execute()
        db.table("po_lines").delete().eq("po_id", pid).execute()
        rows = []
        for i, r in edited.iterrows():
            if not _t(r.get("Description")):
                continue
            rows.append({"po_id": pid, "description": _t(r.get("Description")),
                         "unit": _t(r.get("Unit")) or "LOT",
                         "qty": _f(r.get("Qty")) or 1,
                         "unit_price": _f(r.get("Unit Price")),
                         "remark": _t(r.get("Remark")), "sort_order": i})
        if rows:
            db.table("po_lines").insert(rows).execute()

    def apply_to_budget():
        """PO 금액을 프로젝트 예산(외주비)의 해당 업체 라인에 반영"""
        if not po.get("project_id"):
            return
        acc_id = po.get("budget_account_id") or \
            helpers.account_id_by_name("외주비", 2)
        vendor_id = po.get("vendor_id")
        # 같은 프로젝트+계정+업체 예산 라인 갱신 또는 생성
        existing = (db.table("budget_lines").select("*")
                    .eq("project_id", po["project_id"])
                    .eq("account_id", acc_id).execute().data)
        target = None
        for b in existing:
            if b.get("vendor_id") == vendor_id:
                target = b
                break
        if target:
            db.table("budget_lines").update({"amount": total}) \
                .eq("id", target["id"]).execute()
        else:
            db.table("budget_lines").insert({
                "project_id": po["project_id"], "account_id": acc_id,
                "vendor_id": vendor_id, "amount": total,
                "notes": f"PO {po['po_no']} 발주",
            }).execute()

    st.divider()
    b = st.columns(4)
    if editable and b[0].button("💾 저장", type="primary"):
        save_all()
        st.success("저장되었습니다.")
        st.rerun()

    if editable and b[1].button("💾 저장 + 예산 반영", type="primary"):
        save_all()
        apply_to_budget()
        st.success(f"저장 및 프로젝트 예산(외주비)에 ${total:,.2f} "
                   "반영되었습니다.")
        st.rerun()

    if po["status"] == "작성중" and editable:
        if b[2].button("📤 발행 처리"):
            save_all()
            apply_to_budget()
            db.table("purchase_orders").update({"status": "발행"}) \
                .eq("id", pid).execute()
            st.rerun()

    # ---- 엑셀 ----
    xl_lines = [{"description": _t(r.get("Description")),
                 "unit": _t(r.get("Unit")) or "LOT",
                 "qty": _f(r.get("Qty")) or 1,
                 "unit_price": _f(r.get("Unit Price")),
                 "remark": _t(r.get("Remark"))}
                for _, r in edited.iterrows() if _t(r.get("Description"))]
    prj_map = {p["id"]: p for p in
               db.table("projects").select("id,name").execute().data}
    pname = prj_map.get(po.get("project_id"), {}).get("name", "")
    b[3].download_button(
        "⬇️ 발주서 엑셀 출력",
        data=build_po_excel({**po, **current}, xl_lines),
        file_name=helpers.safe_filename("PO", po["po_no"],
                                        po.get("supplier_name")) + ".xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True)

    if admin and po["status"] == "작성중":
        st.divider()
        with st.popover("🗑️ 발주서 삭제 (관리자)"):
            if st.button("삭제 확정", key=k+"del"):
                db.table("purchase_orders").delete().eq("id", pid).execute()
                st.session_state["po_open"] = None
                st.rerun()
