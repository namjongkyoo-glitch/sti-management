"""자금 집행: 지출 신청 → 관리자 승인 → 지급 처리 / 공통비 풀 / 수입 등록"""
import pandas as pd
import streamlit as st
from datetime import date, datetime

import auth
import helpers
from db import get_db

REQ_BADGE = {"요청": "🟠 요청", "승인": "🟢 승인",
             "반려": "❌ 반려", "지출완료": "✅ 지출완료"}


# ------------------------------------------------------------
# 공통 헬퍼
# ------------------------------------------------------------
def load_projects(db):
    """공통비 풀 + 수주 프로젝트(취소 제외)"""
    prjs = (db.table("projects").select("*")
            .neq("status", "취소").execute().data)
    prjs.sort(key=lambda p: (not p["is_common_pool"], p["code"]))
    return prjs


def account_options():
    """지출 계정 선택지: 직접비 중분류 + 간접비/금융 세부계정"""
    accs = helpers.load_accounts()
    by_id = {a["id"]: a for a in accs}
    opts = []
    for a in accs:
        if a["level"] == 2 and a["name_kr"] in helpers.COST_MIDS_DIRECT:
            opts.append((a["id"], f"직접비 > {a['name_kr']}"))
    for a in accs:
        if a["level"] == 3:
            p = by_id.get(a["parent_id"])
            if p:
                opts.append((a["id"], f"{p['name_kr']} > {a['name_kr']}"))
    return opts


def mid_of(acc: dict) -> dict:
    return acc if acc["level"] == 2 else helpers.account_by_id(acc["parent_id"])


def subtree_ids(mid_id: int) -> list[int]:
    return [mid_id] + [a["id"] for a in helpers.load_accounts()
                       if a["parent_id"] == mid_id]


def users_map(db):
    return {u["id"]: u["name"]
            for u in db.table("app_users").select("id,name").execute().data}


def budget_and_spent(db, prj: dict, acc: dict):
    """(예산, 지급완료 누적, 승인·요청 대기 누적) - 중분류 기준.
    공통비 풀이면 전체 프로젝트의 해당 간접비 예산/집행을 합산."""
    mid = mid_of(acc)
    ids = subtree_ids(mid["id"])
    pool = prj["is_common_pool"] and mid.get("is_common")

    bq = db.table("budget_lines").select("amount,project_id") \
        .in_("account_id", ids)
    if not pool:
        bq = bq.eq("project_id", prj["id"])
    budget = sum(float(x["amount"] or 0) for x in bq.execute().data)

    tq = db.table("transactions").select("amount,project_id") \
        .eq("tx_type", "지출").in_("account_id", ids)
    rq = db.table("expense_requests").select("amount,project_id") \
        .in_("account_id", ids).in_("status", ["요청", "승인"])
    if not pool:
        tq = tq.eq("project_id", prj["id"])
        rq = rq.eq("project_id", prj["id"])
    paid = sum(float(x["amount"] or 0) for x in tq.execute().data)
    pending = sum(float(x["amount"] or 0) for x in rq.execute().data)
    return budget, paid, pending


# ------------------------------------------------------------
def render():
    helpers.page_title("자금 집행")
    db = get_db()
    editable = auth.can_edit("expenses")
    admin = auth.is_admin()

    pending_cnt = len(db.table("expense_requests").select("id")
                      .eq("status", "요청").execute().data)
    tabs = st.tabs([
        "📝 지출 신청",
        f"✅ 승인 관리{f' ({pending_cnt})' if pending_cnt else ''}",
        "📚 신청 내역 / 지급 처리",
        "💰 수입 등록",
        "🏦 통장 관리/이체",
    ])
    with tabs[0]:
        request_tab(db, editable)
    with tabs[1]:
        approval_tab(db, admin)
    with tabs[2]:
        history_tab(db, editable)
    with tabs[3]:
        income_tab(db, editable)
    with tabs[4]:
        bank_tab(db, editable)


# ============================================================
# 탭1: 지출 신청
# ============================================================
def request_tab(db, editable):
    if not editable:
        st.info("지출 신청 권한이 없습니다. (편집 권한 필요)")
        return
    prjs = load_projects(db)
    if not prjs:
        st.info("프로젝트가 없습니다.")
        return

    c1, c2 = st.columns(2)
    prj = c1.selectbox(
        "프로젝트", prjs,
        format_func=lambda p: ("🏢 " if p["is_common_pool"] else "🏗️ ")
        + f"{p['code']} {p['name']}")
    opts = account_options()
    acc_id, _ = c2.selectbox("지출 계정", opts, format_func=lambda o: o[1])
    acc = helpers.account_by_id(acc_id)
    mid = mid_of(acc)

    # ---- 예산 대비 누적 현황 ----
    budget, paid, pending = budget_and_spent(db, prj, acc)
    remain = budget - paid - pending
    scope = "전체 프로젝트 합산 (공통비 풀)" \
        if prj["is_common_pool"] and mid.get("is_common") else prj["code"]
    st.markdown(f"**[{mid['name_kr']}] 예산 대비 현황** — 기준: {scope}")
    m = st.columns(4)
    m[0].metric("예산", f"${budget:,.0f}")
    m[1].metric("지급 완료 누적", f"${paid:,.0f}")
    m[2].metric("승인/요청 대기", f"${pending:,.0f}")
    m[3].metric("잔여", f"${remain:,.0f}",
                delta=None if remain >= 0 else "예산 초과",
                delta_color="inverse")
    if budget > 0:
        st.progress(min((paid + pending) / budget, 1.0))

    st.divider()
    vendors = [""] + [v["name"] for v in helpers.load_vendors()]
    f1, f2, f3 = st.columns(3)
    amount = f1.number_input("금액 (USD) *", min_value=0.0, step=100.0,
                             format="%.2f", key="req_amt")
    vendor = f2.selectbox("협력업체/거래처", vendors, key="req_vendor")
    req_date = f3.date_input("지출 예정일", value=date.today(), key="req_date")
    purpose = st.text_input("지출 내용 *", key="req_purpose",
                            placeholder="예: Anchor 설치 1차 기성, 4월 사무실 임차료")
    notes = st.text_input("비고", key="req_notes")

    over = amount > 0 and (amount > remain) and budget > 0
    if over:
        st.warning(f"⚠️ 신청 금액이 잔여 예산을 ${amount - remain:,.2f} 초과합니다. "
                   "신청은 가능하나 관리자 승인 시 참고됩니다.")

    if st.button("📤 지출 신청", type="primary"):
        if amount <= 0 or not purpose.strip():
            st.error("금액과 지출 내용을 입력하세요.")
        else:
            db.table("expense_requests").insert({
                "project_id": prj["id"], "account_id": acc_id,
                "vendor_id": helpers.vendor_id_by_name(vendor),
                "amount": amount, "request_date": str(req_date),
                "requested_by": st.session_state["user"]["id"],
                "purpose": purpose.strip(), "notes": notes.strip(),
            }).execute()
            st.success("지출 신청이 등록되었습니다. 관리자 승인 후 지급 처리됩니다.")
            st.rerun()


# ============================================================
# 탭2: 승인 관리 (관리자)
# ============================================================
def approval_tab(db, admin):
    reqs = (db.table("expense_requests").select("*")
            .eq("status", "요청").order("created_at").execute().data)
    if not reqs:
        st.info("승인 대기 중인 지출 신청이 없습니다.")
        return
    if not admin:
        st.info("승인/반려는 관리자만 가능합니다. 아래는 대기 목록입니다.")

    prj_map = {p["id"]: p for p in load_projects(db)}
    umap = users_map(db)

    for r in reqs:
        prj = prj_map.get(r["project_id"], {})
        acc = helpers.account_by_id(r["account_id"])
        mid = mid_of(acc)
        budget, paid, pending = budget_and_spent(db, prj, acc) if prj else (0, 0, 0)
        with st.container(border=True):
            c = st.columns([2.5, 2, 1.5, 2.5, 2])
            c[0].markdown(f"**{prj.get('code','-')} {prj.get('name','')}**  \n"
                          f"{mid['name_kr']} > {acc['name_kr']}")
            c[1].markdown(f"**${float(r['amount']):,.2f}**  \n"
                          f"{helpers.vendor_name_by_id(r.get('vendor_id')) or '-'}")
            c[2].markdown(f"{r['request_date']}  \n"
                          f"신청: {umap.get(r.get('requested_by'), '-')}")
            c[3].markdown(f"{r.get('purpose') or ''}  \n"
                          f"예산 ${budget:,.0f} / 누적 ${paid + pending:,.0f}")
            if admin:
                if c[4].button("✅ 승인", key=f"ap_{r['id']}", type="primary",
                               use_container_width=True):
                    db.table("expense_requests").update({
                        "status": "승인",
                        "approved_by": st.session_state["user"]["id"],
                        "approved_at": datetime.now().isoformat(),
                    }).eq("id", r["id"]).execute()
                    st.rerun()
                with c[4].popover("↩️ 반려", use_container_width=True):
                    reason = st.text_input("반려 사유", key=f"rj_{r['id']}")
                    if st.button("반려 확정", key=f"rjb_{r['id']}"):
                        db.table("expense_requests").update({
                            "status": "반려",
                            "approved_by": st.session_state["user"]["id"],
                            "approved_at": datetime.now().isoformat(),
                            "rejected_reason": reason,
                        }).eq("id", r["id"]).execute()
                        st.rerun()


# ============================================================
# 탭3: 신청 내역 / 지급 처리
# ============================================================
def history_tab(db, editable):
    prjs = load_projects(db)
    prj_map = {p["id"]: p for p in prjs}
    umap = users_map(db)

    f1, f2 = st.columns(2)
    status_f = f1.multiselect("상태", ["요청", "승인", "반려", "지출완료"],
                              default=["요청", "승인", "지출완료"])
    prj_f = f2.selectbox("프로젝트", [None] + prjs,
                         format_func=lambda p: "전체" if p is None
                         else f"{p['code']} {p['name']}")

    q = db.table("expense_requests").select("*").order("created_at", desc=True)
    if status_f:
        q = q.in_("status", status_f)
    if prj_f:
        q = q.eq("project_id", prj_f["id"])
    reqs = q.limit(300).execute().data
    if not reqs:
        st.info("내역이 없습니다.")
        return

    # ---- 승인 건 지급 처리 ----
    approved = [r for r in reqs if r["status"] == "승인"]
    if approved and editable:
        st.markdown("**💳 지급 처리 (승인 완료 건)**")
        for r in approved:
            prj = prj_map.get(r["project_id"], {})
            acc = helpers.account_by_id(r["account_id"])
            with st.container(border=True):
                c = st.columns([3, 2, 2.5, 2.5])
                c[0].markdown(f"**{prj.get('code','-')}** {acc['name_kr']}  \n"
                              f"{r.get('purpose') or ''}")
                c[1].markdown(f"**${float(r['amount']):,.2f}**  \n"
                              f"{helpers.vendor_name_by_id(r.get('vendor_id')) or '-'}")
                pay_date = c[2].date_input("지급일", value=date.today(),
                                           key=f"pd_{r['id']}")
                method = c[2].selectbox("지급 방법",
                                        ["계좌이체(ACH/Wire)", "Check", "카드", "Zelle", "기타"],
                                        key=f"pm_{r['id']}")
                banks = helpers.load_bank_accounts()
                bank = c[2].selectbox("출금 통장", [None] + banks,
                                      format_func=helpers.bank_label,
                                      key=f"bk_{r['id']}")
                if c[3].button("✅ 지급 완료 처리", key=f"pay_{r['id']}",
                               type="primary", use_container_width=True):
                    if helpers.load_bank_accounts() and bank is None:
                        st.error("출금 통장을 선택하세요.")
                        st.stop()
                    db.table("transactions").insert({
                        "tx_type": "지출", "project_id": r["project_id"],
                        "account_id": r["account_id"],
                        "bank_account_id": bank["id"] if bank else None,
                        "vendor_id": r.get("vendor_id"),
                        "expense_request_id": r["id"],
                        "amount": r["amount"], "tx_date": str(pay_date),
                        "description": r.get("purpose"),
                        "created_by": st.session_state["user"]["id"],
                    }).execute()
                    db.table("expense_requests").update({
                        "status": "지출완료", "paid_at": str(pay_date),
                        "payment_method": method,
                    }).eq("id", r["id"]).execute()
                    st.success("지급 완료 — 자금 원장에 기록되었습니다.")
                    st.rerun()
        st.divider()

    # ---- 지급 완료 거래 관리 (관리자: 수정/삭제) ----
    if auth.is_admin():
        paid_txs = (db.table("transactions").select("*")
                    .eq("tx_type", "지출")
                    .order("tx_date", desc=True).limit(100).execute().data)
        if paid_txs:
            with st.expander("🔧 지급 완료 거래 수정/삭제 (관리자)"):
                st.caption("지급 처리된 거래를 수정하거나 삭제합니다. "
                           "삭제하면 통장 잔액·예산 집행에서 제외되고, "
                           "연결된 신청 건은 '승인' 상태로 되돌아갑니다.")
                tsel = st.selectbox(
                    "거래 선택", paid_txs,
                    format_func=lambda t:
                    f"{t['tx_date']} · "
                    f"{prj_map.get(t.get('project_id'), {}).get('code', '-')} · "
                    f"{(helpers.account_by_id(t['account_id']) or {}).get('name_kr', '-')} · "
                    f"${float(t['amount']):,.2f} · {t.get('description') or ''}",
                    key="paid_tx_sel")
                with st.form(f"edit_tx_{tsel['id']}"):
                    ec = st.columns(3)
                    e_amount = ec[0].number_input(
                        "금액", value=float(tsel["amount"] or 0),
                        step=100.0, format="%.2f")
                    e_date = ec[1].date_input(
                        "거래일", value=pd.to_datetime(tsel["tx_date"]).date())
                    e_banks = [None] + helpers.load_bank_accounts()
                    e_bank = ec[2].selectbox(
                        "출금 통장", e_banks, format_func=helpers.bank_label,
                        index=next((i for i, b in enumerate(e_banks)
                                    if b and b["id"] == tsel.get("bank_account_id")), 0))
                    e_desc = st.text_input("내용", tsel.get("description") or "")
                    bcol = st.columns(2)
                    save = bcol[0].form_submit_button("💾 수정 저장", type="primary")
                    delete = bcol[1].form_submit_button("🗑️ 거래 삭제")
                if save:
                    db.table("transactions").update({
                        "amount": e_amount, "tx_date": str(e_date),
                        "bank_account_id": e_bank["id"] if e_bank else None,
                        "description": e_desc,
                    }).eq("id", tsel["id"]).execute()
                    st.success("거래가 수정되었습니다.")
                    st.rerun()
                if delete:
                    # 연결된 신청 건을 승인 상태로 되돌림
                    if tsel.get("expense_request_id"):
                        db.table("expense_requests").update({
                            "status": "승인", "paid_at": None,
                            "payment_method": None,
                        }).eq("id", tsel["expense_request_id"]).execute()
                    db.table("transactions").delete() \
                        .eq("id", tsel["id"]).execute()
                    st.success("거래가 삭제되었습니다. (신청 건은 승인 상태로 복귀)")
                    st.rerun()
    rows = []
    for r in reqs:
        prj = prj_map.get(r["project_id"], {})
        acc = helpers.account_by_id(r["account_id"])
        rows.append({
            "상태": REQ_BADGE.get(r["status"], r["status"]),
            "신청일": str(r["request_date"]),
            "프로젝트": prj.get("code", "-"),
            "계정": f"{mid_of(acc)['name_kr']}>{acc['name_kr']}",
            "거래처": helpers.vendor_name_by_id(r.get("vendor_id")) or "-",
            "금액": f"${float(r['amount']):,.2f}",
            "내용": r.get("purpose") or "",
            "신청자": umap.get(r.get("requested_by"), "-"),
            "승인자": umap.get(r.get("approved_by"), "-"),
            "지급일": r.get("paid_at") or "",
            "비고": r.get("rejected_reason") or r.get("notes") or "",
        })
    st.markdown(f"**전체 내역 ({len(rows)}건)**")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ============================================================
# 탭4: 수입 등록
# ============================================================
def income_tab(db, editable):
    if editable:
        prjs = load_projects(db)
        inc_accs = [a for a in helpers.load_accounts()
                    if a["level"] == 2 and a["code"].startswith("1")]
        with st.form("income", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            prj = c1.selectbox("프로젝트", [None] + prjs,
                               format_func=lambda p: "(프로젝트 없음)" if p is None
                               else f"{p['code']} {p['name']}")
            acc = c2.selectbox("수입 계정", inc_accs,
                               format_func=lambda a: a["name_kr"])
            tx_date = c3.date_input("입금일", value=date.today())
            c4, c5, c6 = st.columns([1, 1.4, 1.6])
            amount = c4.number_input("금액 (USD) *", min_value=0.0,
                                     step=1000.0, format="%.2f")
            bank = c5.selectbox("입금 통장", [None] + helpers.load_bank_accounts(),
                                format_func=helpers.bank_label)
            desc = c6.text_input("내용 *", placeholder="예: SECAI 1차 기성금")
            ok = st.form_submit_button("💰 수입 등록", type="primary")
        if ok:
            if amount <= 0 or not desc.strip():
                st.error("금액과 내용을 입력하세요.")
            else:
                db.table("transactions").insert({
                    "tx_type": "수입",
                    "project_id": prj["id"] if prj else None,
                    "bank_account_id": bank["id"] if bank else None,
                    "account_id": acc["id"], "amount": amount,
                    "tx_date": str(tx_date), "description": desc.strip(),
                    "created_by": st.session_state["user"]["id"],
                }).execute()
                st.success("수입이 등록되었습니다.")
                st.rerun()
    else:
        st.info("수입 등록 권한이 없습니다.")

    # ---- 지출 반환(환불) 등록 ----
    if editable:
        st.divider()
        st.markdown("**↩️ 지출 반환(환불) 등록**")
        st.caption("지출했다가 돌려받은 금액입니다. 통장 잔액은 복구되고, "
                   "수입이 아니라 해당 계정/프로젝트의 지출에서 차감됩니다.")
        prjs2 = load_projects(db)
        exp_accs = [a for a in helpers.load_accounts()
                    if a["level"] == 2 and not str(a.get("code") or "").startswith("1")]
        with st.form("refund", clear_on_submit=True):
            r1, r2, r3 = st.columns(3)
            rprj = r1.selectbox("프로젝트", [None] + prjs2,
                                format_func=lambda p: "(프로젝트 없음)" if p is None
                                else f"{p['code']} {p['name']}", key="rfp")
            racc = r2.selectbox("지출 계정 (반환 대상)", exp_accs,
                                format_func=lambda a: a["name_kr"], key="rfa")
            rdate = r3.date_input("반환일", value=date.today(), key="rfd")
            r4, r5, r6 = st.columns([1, 1.4, 1.6])
            ramount = r4.number_input("반환 금액 (USD) *", min_value=0.0,
                                      step=100.0, format="%.2f", key="rfm")
            rbank = r5.selectbox("입금 통장", [None] + helpers.load_bank_accounts(),
                                 format_func=helpers.bank_label, key="rfb")
            rdesc = r6.text_input("내용 *", placeholder="예: 과지급 환불",
                                  key="rfdesc")
            rok = st.form_submit_button("↩️ 반환 등록", type="primary")
        if rok:
            if ramount <= 0 or not rdesc.strip():
                st.error("금액과 내용을 입력하세요.")
            else:
                db.table("transactions").insert({
                    "tx_type": "반환",
                    "project_id": rprj["id"] if rprj else None,
                    "bank_account_id": rbank["id"] if rbank else None,
                    "account_id": racc["id"], "amount": ramount,
                    "tx_date": str(rdate), "description": rdesc.strip(),
                    "created_by": st.session_state["user"]["id"],
                }).execute()
                st.success("지출 반환이 등록되었습니다.")
                st.rerun()

    txs = (db.table("transactions").select("*").eq("tx_type", "수입")
           .order("tx_date", desc=True).limit(50).execute().data)
    if txs:
        prj_map = {p["id"]: p for p in load_projects(db)}
        st.markdown("**최근 수입 내역**")
        st.dataframe(pd.DataFrame([{
            "입금일": t["tx_date"],
            "프로젝트": prj_map.get(t.get("project_id"), {}).get("code", "-"),
            "계정": helpers.account_by_id(t["account_id"])["name_kr"]
            if t.get("account_id") else "-",
            "금액": f"${float(t['amount']):,.2f}",
            "내용": t.get("description") or "",
        } for t in txs]), use_container_width=True, hide_index=True)


# ============================================================
# 탭5: 통장 관리 / 이체
# ============================================================
def bank_tab(db, editable):
    # ---- 잔액 현황 ----
    bals = helpers.bank_balances_ordered()
    if bals:
        bdf = pd.DataFrame([{
            "통장": helpers.bank_label(b["account"]),
            "상태": "✅" if b["account"]["is_active"] else "⛔",
            "기초금액": b["기초"], "수입": b["수입"], "지출": b["지출"],
            "이체 입금": b["이체입"], "이체 출금": b["이체출"], "잔액": b["잔액"],
        } for b in bals])
        tot = {"통장": "합계", "상태": ""}
        for c in ["기초금액", "수입", "지출", "이체 입금", "이체 출금", "잔액"]:
            tot[c] = bdf[c].sum()
        bdf = pd.concat([bdf, pd.DataFrame([tot])], ignore_index=True)
        for c in ["기초금액", "수입", "지출", "이체 입금", "이체 출금", "잔액"]:
            bdf[c] = bdf[c].map(lambda x: f"${x:,.2f}")
        st.markdown("**통장별 잔액 현황**")
        st.dataframe(bdf, use_container_width=True, hide_index=True)
        st.caption("잔액 = 기초금액 + 수입 − 지출 + 이체입금 − 이체출금  ·  "
                   "통장 간 이체는 자금 수지/요약에 반영되지 않습니다.")
    else:
        st.info("등록된 통장이 없습니다. 아래에서 등록하세요.")

    if not editable:
        return

    # ---- 통장 등록/수정 ----
    with st.expander("➕ 통장 등록 / 수정"):
        with st.form("new_bank", clear_on_submit=True):
            c = st.columns(4)
            bank_name = c[0].text_input("은행명 *")
            account_name = c[1].text_input("용도/별칭 (운영, 급여 등)")
            account_no = c[2].text_input("계좌번호")
            opening = c[3].number_input("기초금액 (USD)", step=100.0,
                                        format="%.2f")
            notes = st.text_input("비고", key="bank_notes")
            ok = st.form_submit_button("통장 등록", type="primary")
        if ok:
            if not bank_name:
                st.error("은행명을 입력하세요.")
            else:
                db.table("bank_accounts").insert({
                    "bank_name": bank_name, "account_name": account_name,
                    "account_no": account_no, "opening_balance": opening,
                    "notes": notes}).execute()
                st.success("통장이 등록되었습니다.")
                st.rerun()

        for a in helpers.load_bank_accounts(active_only=False):
            with st.form(f"eb_{a['id']}"):
                c = st.columns(5)
                bn = c[0].text_input("은행명", value=a["bank_name"])
                an = c[1].text_input("별칭", value=a.get("account_name") or "")
                no = c[2].text_input("계좌번호", value=a.get("account_no") or "")
                op = c[3].number_input("기초금액", value=float(a["opening_balance"] or 0),
                                       step=100.0, format="%.2f")
                act = c[4].checkbox("활성", value=a["is_active"])
                ok = st.form_submit_button("수정 저장")
            if ok:
                db.table("bank_accounts").update({
                    "bank_name": bn, "account_name": an, "account_no": no,
                    "opening_balance": op, "is_active": act,
                }).eq("id", a["id"]).execute()
                st.rerun()

    # ---- 이체 ----
    banks = helpers.load_bank_accounts()
    if len(banks) >= 2:
        st.markdown("**🔁 통장 간 이체** (자금 흐름에 반영되지 않음)")
        # 이체 분류용 계정 (전체 중분류)
        xfer_accs = [None] + [a for a in helpers.load_accounts()
                              if a["level"] == 2]
        with st.form("transfer", clear_on_submit=True):
            c = st.columns(4)
            frm = c[0].selectbox("출금 통장", banks, format_func=helpers.bank_label)
            to = c[1].selectbox("입금 통장", banks, format_func=helpers.bank_label,
                                index=min(1, len(banks) - 1))
            amt = c[2].number_input("금액 (USD)", min_value=0.0, step=100.0,
                                    format="%.2f")
            tdate = c[3].date_input("이체일", value=date.today())
            c2 = st.columns(2)
            xacc = c2[0].selectbox(
                "분류 계정 (선택)", xfer_accs,
                format_func=lambda a: "(분류 없음)" if a is None
                else f"{a.get('code') or ''} {a['name_kr']}")
            tnotes = c2[1].text_input("비고", key="tr_notes")
            ok = st.form_submit_button("이체 기록", type="primary")
        if ok:
            if frm["id"] == to["id"]:
                st.error("출금/입금 통장이 같습니다.")
            elif amt <= 0:
                st.error("금액을 입력하세요.")
            else:
                db.table("bank_transfers").insert({
                    "from_account_id": frm["id"], "to_account_id": to["id"],
                    "amount": amt, "transfer_date": str(tdate),
                    "account_id": xacc["id"] if xacc else None,
                    "notes": tnotes,
                    "created_by": st.session_state["user"]["id"],
                }).execute()
                st.success("이체가 기록되었습니다. (잔액에만 반영, 수지 미반영)")
                st.rerun()

    trs = (db.table("bank_transfers").select("*")
           .order("transfer_date", desc=True).limit(50).execute().data)
    if trs:
        amap = {a["id"]: helpers.bank_label(a)
                for a in helpers.load_bank_accounts(active_only=False)}
        st.markdown("**이체 이력**")
        st.dataframe(pd.DataFrame([{
            "이체일": t["transfer_date"],
            "출금": amap.get(t["from_account_id"], "-"),
            "입금": amap.get(t["to_account_id"], "-"),
            "금액": f"${float(t['amount']):,.2f}",
            "분류": (helpers.account_by_id(t["account_id"]) or {}).get("name_kr", "-")
            if t.get("account_id") else "-",
            "비고": t.get("notes") or "",
        } for t in trs]), use_container_width=True, hide_index=True)
