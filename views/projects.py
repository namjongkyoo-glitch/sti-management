"""프로젝트 관리: 수주 프로젝트 목록 / 예산 관리(관리자 변경 + 이력) / 정보 관리"""
import pandas as pd
import streamlit as st
from datetime import datetime

import auth
import helpers
from db import get_db

PRJ_BADGE = {"진행중": "🟢 진행중", "완료": "🔵 완료",
             "보류": "🟠 보류", "취소": "⚫ 취소"}
PRJ_COLOR = {"진행중": "#22C55E", "완료": "#3B82F6",
             "보류": "#F59E0B", "취소": "#9CA3AF"}


def status_html(status):
    color = PRJ_COLOR.get(status, "#9CA3AF")
    label = PRJ_BADGE.get(status, status)
    return (f"<span style='color:{color};font-weight:700'>{label}</span>")


def render():
    helpers.page_title("프로젝트 관리")
    db = get_db()
    if st.session_state.get("prj_open"):
        detail_screen(db, st.session_state["prj_open"])
    else:
        list_screen(db)


# ============================================================
# 목록 (수주된 프로젝트만 - 공통비 풀 제외)
# ============================================================
def list_screen(db):
    st.caption("견적에서 관리자가 수주 승인한 프로젝트만 이곳에서 관리되며, "
               "전체 자금 관리에 반영됩니다.")
    prjs = (db.table("projects").select("*")
            .eq("is_common_pool", False)
            .order("created_at", desc=True).execute().data)
    if not prjs:
        st.info("수주된 프로젝트가 없습니다. 견적 관리에서 수주 승인 시 자동 생성됩니다.")
        return

    # 프로젝트별 예산 편성 여부 (예산 라인 1개 이상이면 편성됨)
    blines = db.table("budget_lines").select("project_id").execute().data
    budgeted = {b["project_id"] for b in blines}

    # ---- 상태별 요약 ----
    from collections import Counter
    cnt = Counter(p["status"] for p in prjs)
    not_budgeted = sum(1 for p in prjs if p["id"] not in budgeted
                       and p["status"] != "취소")
    m = st.columns(5)
    m[0].metric("전체", f"{len(prjs)}건")
    m[1].metric("🟢 진행중", f"{cnt.get('진행중', 0)}건")
    m[2].metric("🔵 완료", f"{cnt.get('완료', 0)}건")
    m[3].metric("🟠 보류", f"{cnt.get('보류', 0)}건")
    m[4].metric("⚠️ 예산 미편성", f"{not_budgeted}건")

    # ---- 상태 필터 (기본: 진행중) ----
    options = ["전체", "진행중", "완료", "보류", "취소"]
    fc = st.columns([2, 3, 2])
    sel = fc[0].radio("상태별 조회", options, horizontal=True,
                      index=1, label_visibility="collapsed")
    kw = fc[1].text_input("검색 (코드/이름/고객사)", "",
                          label_visibility="collapsed",
                          placeholder="🔍 코드/이름/고객사 검색")

    filt = prjs
    if sel != "전체":
        filt = [p for p in filt if p["status"] == sel]
    if kw.strip():
        k = kw.strip().lower()
        filt = [p for p in filt
                if k in (p["code"] or "").lower()
                or k in (p["name"] or "").lower()
                or k in (p.get("client") or "").lower()]
    only_unbudgeted = fc[2].checkbox("⚠️ 예산 미편성만")
    if only_unbudgeted:
        filt = [p for p in filt if p["id"] not in budgeted]

    # 합계
    total_amt = sum(float(p["contract_amount"] or 0) for p in filt)
    st.markdown(f"**{sel} — {len(filt)}건 · 수주액 합계 ${total_amt:,.0f}**")

    if not filt:
        st.info("해당 조건의 프로젝트가 없습니다.")
        return

    # 헤더
    pspec = [1.3, 2.6, 1.5, 1.6, 1.2, 1.1, 1.5, 0.9]
    helpers.list_header(pspec, ["코드", "프로젝트명", "고객사", "수주금액",
                                "상태", "예산", "기간", ""])
    for p in filt:
        with st.container(border=True):
            cols = st.columns(pspec)
            cols[0].markdown(f"**{p['code']}**")
            cols[1].write(p["name"])
            cols[2].write(p.get("client") or "-")
            cols[3].write(f"${float(p['contract_amount'] or 0):,.0f}")
            cols[4].markdown(status_html(p["status"]),
                             unsafe_allow_html=True)
            if p["id"] in budgeted:
                cols[5].markdown(
                    "<span style='color:#22C55E;font-weight:600'>"
                    "✅ 편성</span>", unsafe_allow_html=True)
            else:
                cols[5].markdown(
                    "<span style='color:#F59E0B;font-weight:600'>"
                    "⚠️ 미편성</span>", unsafe_allow_html=True)
            cols[6].write(f"{p.get('start_date') or ''}~{p.get('end_date') or ''}")
            if cols[7].button("열기", key=f"prj_{p['id']}"):
                st.session_state["prj_open"] = p["id"]
                st.rerun()


# ============================================================
# 상세
# ============================================================
def detail_screen(db, pid):
    rows = db.table("projects").select("*").eq("id", pid).execute().data
    if not rows:
        st.session_state["prj_open"] = None
        st.rerun()
    prj = rows[0]
    admin = auth.is_admin()

    top = st.columns([1, 5, 2])
    if top[0].button("← 목록"):
        st.session_state["prj_open"] = None
        st.rerun()
    top[1].subheader(f"{prj['code']}  {prj['name']}")
    top[2].markdown(f"### {status_html(prj['status'])}",
                    unsafe_allow_html=True)
    st.caption(f"고객사: {prj.get('client') or '-'}  ·  "
               f"현장: {prj.get('location') or '-'}  ·  PM: {prj.get('pm') or '-'}")

    budgets = (db.table("budget_lines").select("*")
               .eq("project_id", pid).order("id").execute().data)
    b_direct = sum(float(b["amount"] or 0) for b in budgets
                   if helpers.account_by_id(b["account_id"])["name_kr"]
                   in helpers.COST_MIDS_DIRECT)
    b_indirect = sum(float(b["amount"] or 0) for b in budgets
                     if helpers.account_by_id(b["account_id"])["name_kr"]
                     in helpers.COST_MIDS_INDIRECT)
    contract = float(prj["contract_amount"] or 0)

    m = st.columns(5)
    m[0].metric("계약금액", f"${contract:,.0f}")
    m[1].metric("직접비 예산", f"${b_direct:,.0f}")
    m[2].metric("간접비(공통비) 예산", f"${b_indirect:,.0f}")
    m[3].metric("총원가 예산", f"${b_direct + b_indirect:,.0f}")
    m[4].metric("예상이익", f"${contract - b_direct - b_indirect:,.0f}")

    tab1, tab2, tab3 = st.tabs(["💰 예산 관리", "📝 프로젝트 정보", "📜 예산 변경 이력"])
    with tab1:
        budget_tab(db, prj, budgets, admin)
    with tab2:
        info_tab(db, prj, admin)
    with tab3:
        history_tab(db, pid)

    # ---- 관리자: 예산 미편성 프로젝트 삭제 ----
    if admin:
        st.divider()
        # 연결 데이터 점검
        txs = (db.table("transactions").select("id")
               .eq("project_id", pid).limit(1).execute().data)
        exps = (db.table("expense_requests").select("id")
                .eq("project_id", pid).limit(1).execute().data)
        scheds = (db.table("schedule_items").select("id")
                  .eq("project_id", pid).limit(1).execute().data)
        try:
            pos = (db.table("purchase_orders").select("id")
                   .eq("project_id", pid).limit(1).execute().data)
        except Exception:
            pos = []
        try:
            invs = (db.table("invoices").select("id")
                    .eq("project_id", pid).limit(1).execute().data)
        except Exception:
            invs = []

        blockers = []
        if budgets:
            blockers.append(f"예산 {len(budgets)}건")
        if txs:
            blockers.append("거래내역")
        if exps:
            blockers.append("지출신청")
        if scheds:
            blockers.append("스케줄")
        if pos:
            blockers.append("발주서")
        if invs:
            blockers.append("인보이스")

        if blockers:
            st.caption(f"🔒 이 프로젝트는 연결된 데이터({', '.join(blockers)})가 "
                       "있어 삭제할 수 없습니다. 예산이 편성되지 않고 "
                       "거래·지출 등이 없는 프로젝트만 삭제할 수 있습니다.")
        else:
            with st.popover("🗑️ 프로젝트 삭제 (관리자)"):
                st.warning(f"'{prj['code']} {prj['name']}' 프로젝트를 "
                           "삭제합니다. 예산이 편성되지 않았고 연결된 "
                           "거래/지출/스케줄/발주/인보이스가 없어 삭제 가능합니다.")
                confirm = st.text_input("확인을 위해 프로젝트 코드를 입력하세요",
                                        key=f"pdel_{pid}")
                if st.button("삭제 확정", type="primary", key=f"pdelb_{pid}"):
                    if confirm.strip() == prj["code"]:
                        # 잔여 연결(예산변경이력/업체할당) 정리 후 삭제
                        db.table("budget_changes").delete() \
                            .eq("project_id", pid).execute()
                        db.table("project_vendors").delete() \
                            .eq("project_id", pid).execute()
                        # 견적 연결 해제
                        db.table("estimates").update({"project_id": None}) \
                            .eq("project_id", pid).execute()
                        db.table("projects").delete().eq("id", pid).execute()
                        st.session_state["prj_open"] = None
                        st.rerun()
                    else:
                        st.error("프로젝트 코드가 일치하지 않습니다.")


# ============================================================
# 예산 관리 (관리자만 변경, 변경 이력 기록)
# ============================================================
def budget_tab(db, prj, budgets, admin):
    vendor_opts = [""] + [v["name"] for v in helpers.load_vendors()]
    df = pd.DataFrame([{
        "_id": b["id"],
        "분류": helpers.account_by_id(b["account_id"])["name_kr"],
        "항목명": b["item_name"] or "",
        "협력업체": helpers.vendor_name_by_id(b.get("vendor_id")),
        "금액": float(b["amount"] or 0),
    } for b in budgets]) if budgets else pd.DataFrame(
        columns=["_id", "분류", "항목명", "협력업체", "금액"])

    if not admin:
        st.dataframe(df.drop(columns=["_id"]), use_container_width=True,
                     hide_index=True)
        st.info("예산 변경은 관리자만 가능합니다.")
        return

    st.caption("예산 수정/추가/삭제 후 변경 사유를 입력하고 저장하세요. "
               "모든 변경은 이력으로 기록됩니다.")

    # ---- 승인된 품의서에서 예산 불러오기 (해당 프로젝트 품의서만) ----
    approved = (db.table("proposals").select("*")
                .eq("status", "승인")
                .order("created_at", desc=True).execute().data)
    # 수주된 견적 id 집합 (견적 품의 필터용)
    awarded_est_ids = {e["id"] for e in
                       db.table("estimates").select("id")
                       .eq("status", "수주").execute().data}

    # 이 프로젝트의 품의서: 프로젝트명 일치 OR estimate_id 일치
    prj_est = prj.get("estimate_id")
    pool = []
    for p in approved:
        name_match = (p.get("project_name") or "").strip() == prj["name"].strip()
        est_match = (prj_est is not None
                     and p.get("estimate_id") is not None
                     and p.get("estimate_id") == prj_est)
        if not (name_match or est_match):
            continue
        # '견적 품의'는 해당 견적이 수주 승인된 경우에만 노출
        if (p.get("proposal_type") or "") == "견적 품의":
            eid = p.get("estimate_id")
            if eid is None or eid not in awarded_est_ids:
                continue
        pool.append(p)

    with st.expander("📜 승인된 품의서에서 예산 불러오기", expanded=bool(pool)):
        st.caption("이 프로젝트의 승인 완료된 품의서만 표시됩니다. "
                   "(견적 품의는 해당 견적이 수주 승인된 경우만 표시) "
                   "불러오면 아래 예산 표가 품의서의 직접비/공통비 금액으로 "
                   "채워집니다. (저장 시 변경 이력 기록)")
        if not pool:
            st.info(f"'{prj['name']}' 프로젝트와 자동 연결된 승인 품의서가 "
                    "없습니다. 프로젝트명이 품의서와 다르거나 견적 연결이 "
                    "없을 수 있습니다. 아래에서 직접 선택해 불러올 수 있습니다.")
            # 수동 선택: 전체 승인 품의서
            manual_pool = [p for p in approved
                           if (p.get("proposal_type") or "") != "견적 품의"
                           or p.get("estimate_id") in awarded_est_ids]
            if manual_pool:
                msel = st.selectbox(
                    "전체 승인 품의서에서 선택", manual_pool,
                    format_func=lambda p:
                    f"{p['doc_no']} · {p.get('proposal_type')} · "
                    f"{p.get('project_name') or p.get('title') or ''} "
                    f"(${float(p.get('order_amount') or 0):,.0f})",
                    key=f"manual_prop_{prj['id']}")
                preview_proposal_detail(msel)
                mc = st.columns(2)
                if mc[0].button("⬇️ 이 품의서로 예산 채우기",
                                key=f"mload_{prj['id']}"):
                    st.session_state[f"prop_budget_{prj['id']}"] = \
                        proposal_to_budget_df(msel, vendor_opts)
                    st.rerun()
                if mc[1].button("🔗 이 품의서를 프로젝트에 연결",
                                key=f"mlink_{prj['id']}",
                                help="품의서의 프로젝트명을 이 프로젝트로 맞추고 "
                                     "견적 연결을 설정합니다. 다음부터 자동 표시됩니다."):
                    upd = {"project_name": prj["name"]}
                    if msel.get("estimate_id"):
                        db.table("projects").update(
                            {"estimate_id": msel["estimate_id"]}
                        ).eq("id", prj["id"]).execute()
                    db.table("proposals").update(upd) \
                        .eq("id", msel["id"]).execute()
                    st.success("연결되었습니다. 이제 자동으로 표시됩니다.")
                    st.rerun()
        else:
            psel = st.selectbox(
                "품의서 선택", pool,
                format_func=lambda p:
                f"{p['doc_no']} · {p.get('proposal_type')} · "
                f"{p.get('title') or ''} (수주 ${float(p.get('order_amount') or 0):,.0f})",
                key=f"prop_sel_{prj['id']}")
            preview_proposal_detail(psel)
            if st.button("⬇️ 이 품의서로 예산 채우기", key=f"load_prop_{prj['id']}"):
                st.session_state[f"prop_budget_{prj['id']}"] = \
                    proposal_to_budget_df(psel, vendor_opts)
                st.rerun()

    # 품의서로 채운 데이터가 있으면 그것을 표 기본값으로
    preset_key = f"prop_budget_{prj['id']}"
    if preset_key in st.session_state:
        df = st.session_state[preset_key]
        st.success("품의서 금액을 불러왔습니다. 확인 후 '예산 저장'을 누르세요. "
                   "(필요시 표에서 직접 수정 가능)")

    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True,
        key=f"bud_{prj['id']}",
        column_config={
            "_id": None,  # 숨김
            "분류": st.column_config.SelectboxColumn(
                "분류", options=helpers.COST_MIDS, required=True),
            "항목명": st.column_config.TextColumn("항목명", required=True),
            "협력업체": st.column_config.SelectboxColumn(
                "협력업체", options=vendor_opts),
            "금액": st.column_config.NumberColumn("금액(USD)", format="%.2f"),
        })
    reason = st.text_input("변경 사유 *", key=f"bud_reason_{prj['id']}",
                           placeholder="예: 변경품의 2차 반영, 자재비 단가 인상")

    if st.button("💾 예산 저장 (관리자)", type="primary"):
        save_budget(db, prj, budgets, edited, reason)
        st.session_state.pop(f"prop_budget_{prj['id']}", None)


# 품의서의 직접비/공통비 금액 -> 예산 표 DataFrame
def proposal_to_budget_df(p, vendor_opts):
    rows = []
    s1 = p.get("sheet1_data") or {}
    mat_items = s1.get("material", [])
    out_items = s1.get("outsource", [])

    # 별첨1이 있으면 항목별(협력업체별)로 예산 생성
    used_s1 = False
    if mat_items or out_items:
        used_s1 = True
        for it in mat_items:
            amt = float(it.get("수량") or 0) * float(it.get("단가") or 0)
            if amt > 0:
                rows.append({"_id": None, "분류": "원재료",
                             "항목명": it.get("중분류") or "원재료",
                             "협력업체": it.get("대분류") or "",
                             "금액": amt})
        for it in out_items:
            amt = float(it.get("수량") or 0) * float(it.get("단가") or 0)
            if amt > 0:
                rows.append({"_id": None, "분류": "외주비",
                             "항목명": it.get("중분류") or "외주",
                             "협력업체": it.get("대분류") or "",
                             "금액": amt})

    # vendor_breakdown(구버전 외주 명세) — 별첨1을 안 썼을 때만
    vendor_out_total = 0.0
    if not used_s1:
        vb = p.get("vendor_breakdown") or []
        for x in vb:
            if (x.get("mid") or "") == "외주비":
                amt = float(x.get("amount") or 0)
                if amt > 0:
                    rows.append({"_id": None, "분류": "외주비",
                                 "항목명": x.get("item") or "외주",
                                 "협력업체": x.get("vendor") or "",
                                 "금액": amt})
                    vendor_out_total += amt

    # 직접경비/공통비 + 별첨1 안 쓴 경우의 재료비/외주비 잔여
    mapping = [
        ("원재료", "재료비", "material_cost"),
        ("외주비", "외주비", "outsourcing_cost"),
        ("직접경비", "직접경비", "direct_expense"),
        ("노무비", "노무비", "labor_cost"),
        ("제조간접경비", "제조간접경비", "mfg_overhead"),
        ("판관비", "판관비", "sga_cost"),
    ]
    for mid, label, field in mapping:
        amt = float(p.get(field) or 0)
        # 별첨1로 이미 처리한 원재료/외주비는 건너뜀
        if used_s1 and mid in ("원재료", "외주비"):
            continue
        if mid == "외주비":
            remain = amt - vendor_out_total
            if remain > 0.01:
                rows.append({"_id": None, "분류": "외주비",
                             "항목명": "외주(업체 미지정)", "협력업체": "",
                             "금액": remain})
            continue
        if amt > 0:
            rows.append({"_id": None, "분류": mid, "항목명": label,
                         "협력업체": "", "금액": amt})
    if not rows:
        return pd.DataFrame(columns=["_id", "분류", "항목명", "협력업체", "금액"])
    return pd.DataFrame(rows)


def save_budget(db, prj, budgets, edited: pd.DataFrame, reason: str):
    user = st.session_state["user"]
    orig = {b["id"]: b for b in budgets}
    changes = []
    seen_ids = set()

    for _, r in edited.iterrows():
        if not r.get("분류") or not r.get("항목명"):
            continue
        acc_id = helpers.account_id_by_name(str(r["분류"]), 2)
        amount = float(r.get("금액") or 0)
        vid = helpers.vendor_id_by_name(str(r.get("협력업체") or ""))
        bid = r.get("_id")
        bid = int(bid) if pd.notna(bid) and bid else None

        if bid and bid in orig:  # 기존 행
            seen_ids.add(bid)
            o = orig[bid]
            old_amt = float(o["amount"] or 0)
            db.table("budget_lines").update({
                "account_id": acc_id, "item_name": str(r["항목명"]),
                "vendor_id": vid, "amount": amount,
            }).eq("id", bid).execute()
            if abs(old_amt - amount) > 0.005:
                changes.append((str(r["분류"]), str(r["항목명"]),
                                old_amt, amount))
        else:  # 신규 행
            db.table("budget_lines").insert({
                "project_id": prj["id"], "account_id": acc_id,
                "item_name": str(r["항목명"]), "vendor_id": vid,
                "amount": amount,
            }).execute()
            changes.append((str(r["분류"]), str(r["항목명"]), 0, amount))

    # 삭제된 행
    for bid, o in orig.items():
        if bid not in seen_ids:
            db.table("budget_lines").delete().eq("id", bid).execute()
            changes.append((
                helpers.account_by_id(o["account_id"])["name_kr"],
                o["item_name"] or "", float(o["amount"] or 0), 0))

    if changes and not reason.strip():
        st.error("금액이 변경되었습니다. 변경 사유를 입력해주세요. "
                 "(항목/업체명만 바꾼 경우는 사유 없이 저장됩니다)")
    if changes:
        total_before = sum(c[2] for c in changes)
        total_after = sum(c[3] for c in changes)
        # 전체 예산 합계(변경분 외 포함) 계산
        all_before = sum(float(b["amount"] or 0) for b in budgets)
        # 변경 후 전체 = 변경 전 전체 + 변경분 증감
        delta = total_after - total_before
        detail = [{"분류": c[0], "항목": c[1],
                   "before": c[2], "after": c[3], "증감": c[3] - c[2]}
                  for c in changes]
        db.table("budget_changes").insert({
            "project_id": prj["id"],
            "account_name": f"예산 변경 ({len(changes)}개 항목)",
            "item_name": "; ".join(
                f"{c[0]}/{c[1]} {c[2]:,.0f}→{c[3]:,.0f}" for c in changes
            )[:500],
            "old_amount": all_before,
            "new_amount": all_before + delta,
            "total_before": all_before,
            "total_after": all_before + delta,
            "detail": detail,
            "reason": reason.strip() or "(사유 미입력)",
            "changed_by": user["id"],
        }).execute()
    st.success(f"예산 저장 완료 (변경 {len(changes)}개 항목을 1건으로 기록)")
    st.rerun()


# ============================================================
# 프로젝트 정보 (관리자 수정)
# ============================================================
def info_tab(db, prj, admin):
    if not admin:
        st.write(f"기간: {prj.get('start_date') or '-'} ~ {prj.get('end_date') or '-'}")
        st.write(f"계약금액: ${float(prj['contract_amount'] or 0):,.2f}")
        st.info("프로젝트 정보 수정은 관리자만 가능합니다.")
        return
    with st.form(f"prj_info_{prj['id']}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("프로젝트명", value=prj["name"])
        status = c2.selectbox("상태", ["진행중", "완료", "보류", "취소"],
                              index=["진행중", "완료", "보류", "취소"].index(prj["status"])
                              if prj["status"] in ["진행중", "완료", "보류", "취소"] else 0)
        location = c1.text_input("현장 위치", value=prj.get("location") or "")
        pm = c2.text_input("담당 PM", value=prj.get("pm") or "")
        c3, c4 = st.columns(2)
        start = c3.text_input("시작일 (YYYY-MM-DD)", value=prj.get("start_date") or "")
        end = c4.text_input("종료일 (YYYY-MM-DD)", value=prj.get("end_date") or "")
        contract = st.number_input("계약금액 (USD) - 변경 시 이력 기록",
                                   value=float(prj["contract_amount"] or 0),
                                   step=1000.0, format="%.2f")
        reason = st.text_input("계약금액 변경 사유 (금액 변경 시)")
        ok = st.form_submit_button("저장 (관리자)", type="primary")
    if ok:
        old_contract = float(prj["contract_amount"] or 0)
        db.table("projects").update({
            "name": name, "status": status, "location": location, "pm": pm,
            "start_date": start or None, "end_date": end or None,
            "contract_amount": contract,
        }).eq("id", prj["id"]).execute()
        if abs(old_contract - contract) > 0.005:
            db.table("budget_changes").insert({
                "project_id": prj["id"], "account_name": "계약금액",
                "item_name": "계약금액", "old_amount": old_contract,
                "new_amount": contract,
                "reason": reason.strip() or "(사유 미입력)",
                "changed_by": st.session_state["user"]["id"],
            }).execute()
        st.success("저장되었습니다.")
        st.rerun()


# ============================================================
# 예산 변경 이력
# ============================================================
def history_tab(db, pid):
    hist = (db.table("budget_changes").select("*, app_users(name)")
            .eq("project_id", pid)
            .order("changed_at", desc=True).execute().data)
    if not hist:
        st.info("예산 변경 이력이 없습니다.")
        return

    # 요약 표 (한 저장 = 한 행)
    df = pd.DataFrame([{
        "일시": str(h["changed_at"])[:16].replace("T", " "),
        "변경자": (h.get("app_users") or {}).get("name", "-"),
        "구분": h.get("account_name") or "-",
        "총예산 전": f"${float(h.get('total_before') or h.get('old_amount') or 0):,.0f}",
        "총예산 후": f"${float(h.get('total_after') or h.get('new_amount') or 0):,.0f}",
        "증감": f"${(float(h.get('total_after') or h.get('new_amount') or 0) - float(h.get('total_before') or h.get('old_amount') or 0)):+,.0f}",
        "사유": h.get("reason") or "",
    } for h in hist])
    st.dataframe(df, use_container_width=True, hide_index=True)

    # 각 변경 건의 항목별 상세 (펼침)
    st.caption("각 변경 건의 항목별 증감 상세:")
    for h in hist:
        detail = h.get("detail")
        if not detail:
            continue
        when = str(h["changed_at"])[:16].replace("T", " ")
        who = (h.get("app_users") or {}).get("name", "-")
        with st.expander(f"📋 {when} · {who} · {h.get('reason') or ''}"):
            st.dataframe(pd.DataFrame([{
                "분류": d.get("분류"), "항목": d.get("항목"),
                "변경 전": f"${float(d.get('before') or 0):,.2f}",
                "변경 후": f"${float(d.get('after') or 0):,.2f}",
                "증감": f"${float(d.get('증감') or 0):+,.2f}",
            } for d in detail]), use_container_width=True, hide_index=True)


def preview_proposal_detail(p):
    """예산 불러오기 전 품의서 + 별첨 세부항목 미리보기"""
    import proposal_sheets as ps
    with st.expander("👁️ 품의서 내용 미리보기 (별첨 세부항목 포함)",
                     expanded=False):
        # 품의서 요약 (집행내역 형태)
        cur = "원" if p.get("currency") == "KRW" else "USD"
        order = float(p.get("order_amount") or 0)
        mat = float(p.get("material_cost") or 0)
        out = float(p.get("outsourcing_cost") or 0)
        dexp = float(p.get("direct_expense") or 0)
        lab = float(p.get("labor_cost") or 0)
        mfg = float(p.get("mfg_overhead") or 0)
        sga = float(p.get("sga_cost") or 0)
        c_sub = mat + out + dexp
        d_sub = lab + mfg + sga
        total = c_sub + d_sub
        st.markdown(f"**집행 내역** (단위: {cur})")
        summary = pd.DataFrame([
            {"구분": "(A) 수주금액", "금액": order, "비율": "100%"},
            {"구분": "재료비", "금액": mat,
             "비율": f"{mat/order*100:.0f}%" if order else "-"},
            {"구분": "외주비", "금액": out,
             "비율": f"{out/order*100:.0f}%" if order else "-"},
            {"구분": "직접경비", "금액": dexp,
             "비율": f"{dexp/order*100:.0f}%" if order else "-"},
            {"구분": "(C) 직접비 소계", "금액": c_sub,
             "비율": f"{c_sub/order*100:.0f}%" if order else "-"},
            {"구분": "노무비", "금액": lab, "비율": ""},
            {"구분": "제조간접경비", "금액": mfg, "비율": ""},
            {"구분": "판관비", "금액": sga, "비율": ""},
            {"구분": "(D) 간접비 소계", "금액": d_sub,
             "비율": f"{d_sub/order*100:.0f}%" if order else "-"},
            {"구분": "(E) 총원가", "금액": total,
             "비율": f"{total/order*100:.0f}%" if order else "-"},
        ])
        st.dataframe(
            summary.style.format({"금액": "{:,.0f}"}),
            use_container_width=True, hide_index=True)

        # 별첨1: 제작비용 (협력업체별)
        s1 = p.get("sheet1_data") or {}
        mat_items = s1.get("material", [])
        out_items = s1.get("outsource", [])
        if mat_items or out_items:
            st.markdown("**📎 별첨1 · 제작비용 (협력업체별)**")
            rows1 = []
            for it in mat_items:
                amt = float(it.get("수량") or 0) * float(it.get("단가") or 0)
                rows1.append({"구분": "원재료", "협력업체": it.get("대분류") or "",
                              "품목": it.get("중분류") or "", "수량": it.get("수량"),
                              "단가": it.get("단가"), "금액": amt})
            for it in out_items:
                amt = float(it.get("수량") or 0) * float(it.get("단가") or 0)
                rows1.append({"구분": "외주비", "협력업체": it.get("대분류") or "",
                              "품목": it.get("중분류") or "", "수량": it.get("수량"),
                              "단가": it.get("단가"), "금액": amt})
            if rows1:
                st.dataframe(
                    pd.DataFrame(rows1).style.format(
                        {"금액": "{:,.0f}", "단가": "{:,.0f}"}),
                    use_container_width=True, hide_index=True)

        # 별첨2: 직접경비
        s2 = p.get("sheet2_data") or []
        s2_nonzero = [r for r in s2 if float(r.get("금액") or 0) > 0]
        if s2_nonzero:
            st.markdown("**📎 별첨2 · 직접경비**")
            st.dataframe(
                pd.DataFrame(s2_nonzero)[["구분", "금액", "비고"]].style.format(
                    {"금액": "{:,.0f}"}),
                use_container_width=True, hide_index=True)

        # 별첨3: 현지운영비
        s3 = p.get("sheet3_data") or []
        s3_nonzero = [r for r in s3 if float(r.get("금액") or 0) > 0]
        if s3_nonzero:
            st.markdown("**📎 별첨3 · 현지운영비**")
            st.dataframe(
                pd.DataFrame(s3_nonzero)[["구분", "금액", "비고"]].style.format(
                    {"금액": "{:,.0f}"}),
                use_container_width=True, hide_index=True)
