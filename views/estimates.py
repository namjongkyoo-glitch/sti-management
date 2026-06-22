"""견적 관리: 입력 -> 승인 -> 출력 -> 제출 -> 변경견적 -> 수주(프로젝트 전환/예산편성)"""
import pandas as pd
import streamlit as st
from datetime import date

import auth
import helpers
from db import get_db
from excel_reports import build_estimate_excel
SHEET_NOTE = "[속지 자동합산]"

STATUS_BADGE = {
    "작성중": "🟡 작성중", "승인대기": "🟠 승인대기", "승인": "🟢 승인",
    "제출": "🔵 제출", "수주": "✅ 수주", "실주": "❌ 실주", "보류": "⚪ 보류",
}


def render():
    helpers.page_title("견적 관리")
    st.info("📤 **견적서는 고객사 제출용 문서**입니다. "
            "여기서 산출한 금액을 바탕으로 본사 승인은 **품의서 관리**에서 진행하세요.")
    db = get_db()
    editable = auth.can_edit("estimates")

    if st.session_state.get("est_open"):
        detail_screen(db, st.session_state["est_open"], editable)
    else:
        tab1, tab2, tab3, tab4 = st.tabs(
            ["📑 견적 목록", "📊 수주 대기 현황", "🏆 수주 완료 현황", "🏢 고객사 관리"])
        with tab1:
            list_screen(db, editable)
        with tab2:
            pipeline_screen(db)
        with tab3:
            awarded_screen(db)
        with tab4:
            clients_screen(db, editable)


# ============================================================
# 수주 완료 현황 (연도별 리스트 + 합계)
# ============================================================
def _latest_amounts(db, est_id):
    """견적의 최신 버전 기준 (수주금액, 직접비, 간접비) 반환"""
    vers = (db.table("estimate_versions").select("*")
            .eq("estimate_id", est_id)
            .order("version_no", desc=True).limit(1).execute().data)
    if not vers:
        return 0.0, 0.0, 0.0
    v = vers[0]
    lines = (db.table("estimate_lines").select("*")
             .eq("version_id", v["id"]).execute().data)
    direct = indirect = 0.0
    for l in lines:
        mid = helpers.account_by_id(l["account_id"])["name_kr"]
        amt = float(l["amount"] or 0)
        if mid in helpers.COST_MIDS_DIRECT:
            direct += amt
        elif mid in helpers.COST_MIDS_INDIRECT:
            indirect += amt
    return float(v["order_amount"] or 0), direct, indirect


def awarded_screen(db):
    ests = (db.table("estimates").select("*")
            .eq("status", "수주").order("created_at").execute().data)
    if not ests:
        st.info("수주 완료된 견적이 없습니다.")
        return

    prj_map = {}
    pids = [e["project_id"] for e in ests if e.get("project_id")]
    if pids:
        for p in (db.table("projects").select("*").in_("id", pids).execute().data):
            prj_map[p["id"]] = p

    rows = []
    for e in ests:
        prj = prj_map.get(e.get("project_id"), {})
        award_date = str(prj.get("created_at") or e.get("created_at") or "")[:10]
        year = award_date[:4] if award_date else "-"
        order, direct, indirect = _latest_amounts(db, e["id"])
        total = direct + indirect
        rows.append({
            "연도": year, "수주일": award_date,
            "프로젝트": prj.get("code") or "-",
            "견적번호": e["estimate_no"], "프로젝트명": e["title"],
            "고객사": e.get("client") or "-",
            "수주금액": order, "직접비": direct, "공통비(간접비)": indirect,
            "총원가(예산)": total, "예상이익": order - total,
        })

    df = pd.DataFrame(rows)
    money_cols = ["수주금액", "직접비", "공통비(간접비)", "총원가(예산)", "예상이익"]

    # ---- 연도 선택 ----
    years = sorted(df["연도"].unique(), reverse=True)
    sel_year = st.selectbox("연도", ["전체"] + list(years))
    view = df if sel_year == "전체" else df[df["연도"] == sel_year]

    # ---- 합계 메트릭 ----
    m = st.columns(5)
    t_order = view["수주금액"].sum()
    t_total = view["총원가(예산)"].sum()
    t_profit = view["예상이익"].sum()
    m[0].metric("수주 총액", f"${t_order:,.0f}")
    m[1].metric("총 예산(원가)", f"${t_total:,.0f}")
    m[2].metric("총 공통비", f"${view['공통비(간접비)'].sum():,.0f}")
    m[3].metric("총 예상이익", f"${t_profit:,.0f}"
                + (f" ({t_profit/t_order*100:.1f}%)" if t_order else ""))
    m[4].metric("수주 건수", f"{len(view)}건")

    # ---- 연도별 요약 (전체 선택 시) ----
    if sel_year == "전체" and len(years) > 1:
        st.markdown("**연도별 합계**")
        ysum = (df.groupby("연도")
                .agg(건수=("견적번호", "count"),
                     수주금액=("수주금액", "sum"),
                     총원가=("총원가(예산)", "sum"),
                     예상이익=("예상이익", "sum"))
                .reset_index().sort_values("연도", ascending=False))
        for c in ["수주금액", "총원가", "예상이익"]:
            ysum[c] = ysum[c].map(lambda x: f"${x:,.0f}")
        st.dataframe(ysum, use_container_width=True, hide_index=True)

    # ---- 상세 리스트 + 합계 행 ----
    st.markdown("**수주 완료 리스트**")
    show = view.copy()
    sum_row = {"연도": "", "수주일": "", "프로젝트": "합계", "견적번호": "",
               "프로젝트명": "", "고객사": ""}
    for c in money_cols:
        sum_row[c] = view[c].sum()
    show = pd.concat([show, pd.DataFrame([sum_row])], ignore_index=True)
    for c in money_cols:
        show[c] = show[c].map(lambda x: f"${x:,.0f}")
    st.dataframe(show, use_container_width=True, hide_index=True)


# ============================================================
# 수주 대기 현황 (승인/제출된 견적 전체 - 수주·실주 전 단계)
# ============================================================
def pipeline_screen(db):
    ests = (db.table("estimates").select("*")
            .in_("status", ["승인", "제출"])
            .order("created_at").execute().data)
    if not ests:
        st.info("수주 대기 상태(승인 완료, 수주/실주 전)의 견적이 없습니다.")
        return

    rows = []
    for e in ests:
        vers = (db.table("estimate_versions").select("*")
                .eq("estimate_id", e["id"])
                .order("version_no", desc=True).limit(1).execute().data)
        if not vers:
            continue
        v = vers[0]
        lines = (db.table("estimate_lines").select("*")
                 .eq("version_id", v["id"]).execute().data)
        direct = indirect = 0.0
        for l in lines:
            mid = helpers.account_by_id(l["account_id"])["name_kr"]
            amt = float(l["amount"] or 0)
            if mid in helpers.COST_MIDS_DIRECT:
                direct += amt
            elif mid in helpers.COST_MIDS_INDIRECT:
                indirect += amt
        order = float(v["order_amount"] or 0)
        total = direct + indirect
        profit = order - total
        rows.append({
            "_id": e["id"], "견적번호": e["estimate_no"], "견적명": e["title"],
            "고객사": e.get("client") or "-",
            "버전": v["version_label"],
            "상태": "🔵 제출" if e["status"] == "제출" else "🟢 승인",
            "제출일": str(v.get("submitted_at") or "")[:10],
            "수주금액": order, "직접비": direct, "공통비(간접비)": indirect,
            "총원가(예산)": total, "예상이익": profit,
            "이익률": (profit / order * 100) if order else 0,
        })

    df = pd.DataFrame(rows)
    tot = {
        "수주금액": df["수주금액"].sum(), "직접비": df["직접비"].sum(),
        "공통비(간접비)": df["공통비(간접비)"].sum(),
        "총원가(예산)": df["총원가(예산)"].sum(),
        "예상이익": df["예상이익"].sum(),
    }

    m = st.columns(5)
    m[0].metric("수주 대기 총액", f"${tot['수주금액']:,.0f}")
    m[1].metric("총 예산(원가)", f"${tot['총원가(예산)']:,.0f}")
    m[2].metric("총 공통비", f"${tot['공통비(간접비)']:,.0f}")
    m[3].metric("총 예상이익", f"${tot['예상이익']:,.0f}")
    m[4].metric("대기 건수", f"{len(df)}건")

    show = df.drop(columns=["_id"]).copy()
    sum_row = {"견적번호": "합계", "견적명": "", "고객사": "", "버전": "",
               "상태": "",
               "제출일": "", "이익률":
               (tot["예상이익"] / tot["수주금액"] * 100) if tot["수주금액"] else 0,
               **tot}
    show = pd.concat([show, pd.DataFrame([sum_row])], ignore_index=True)
    for col in ["수주금액", "직접비", "공통비(간접비)", "총원가(예산)", "예상이익"]:
        show[col] = show[col].map(lambda x: f"${x:,.0f}")
    show["이익률"] = show["이익률"].map(lambda x: f"{x:.1f}%")
    st.dataframe(show, use_container_width=True, hide_index=True)

    # ---- 관리자: 수주 승인 / 실주(실패) 처리 ----
    if auth.is_admin():
        st.divider()
        st.markdown("**수주 결과 입력 (관리자)**")
        sel = st.selectbox(
            "대상 견적", rows,
            format_func=lambda r: f"{r['견적번호']} {r['견적명']} (${r['수주금액']:,.0f})")
        c1, c2 = st.columns(2)
        if c1.button("🏆 수주 승인", type="primary", use_container_width=True):
            st.session_state["est_open"] = sel["_id"]
            st.session_state["est_award"] = True
            st.rerun()
        if c2.button("❌ 실주(실패) 처리", use_container_width=True):
            db.table("estimates").update(
                {"status": "실주"}).eq("id", sel["_id"]).execute()
            st.success(f"{sel['견적번호']} 실주 처리되었습니다.")
            st.rerun()
    else:
        st.caption("수주 승인 / 실주 처리는 관리자만 가능합니다.")


# ============================================================
# 고객사 관리
# ============================================================
def clients_screen(db, editable):
    if editable:
        with st.expander("➕ 고객사 등록"):
            with st.form("new_client", clear_on_submit=True):
                c1, c2 = st.columns(2)
                name = c1.text_input("고객사명 *")
                contact = c2.text_input("담당자")
                phone = c1.text_input("전화")
                email = c2.text_input("이메일")
                address = st.text_input("주소")
                notes = st.text_area("비고", height=68)
                ok = st.form_submit_button("등록", type="primary")
            if ok:
                if not name:
                    st.error("고객사명을 입력하세요.")
                else:
                    try:
                        db.table("clients").insert({
                            "name": name, "contact": contact, "phone": phone,
                            "email": email, "address": address, "notes": notes,
                        }).execute()
                        helpers.clear_caches()
                        st.success(f"'{name}' 등록 완료")
                        st.rerun()
                    except Exception:
                        st.error("등록 실패 — 이미 같은 이름의 고객사가 있는지 확인하세요.")

    clients = db.table("clients").select("*").order("name").execute().data
    st.subheader(f"고객사 목록 ({len(clients)})")
    if not clients:
        st.info("등록된 고객사가 없습니다.")
        return
    for c in clients:
        title = (f"{'✅' if c['is_active'] else '⛔'} **{c['name']}**"
                 f"  ·  {c.get('contact') or '-'}  ·  {c.get('phone') or '-'}")
        with st.expander(title):
            st.markdown(
                f"✉️ {c.get('email') or '-'}  \n"
                f"📍 {c.get('address') or '-'}  \n"
                f"📝 {c.get('notes') or '-'}")
            if editable:
                st.divider()
                with st.form(f"edit_c_{c['id']}"):
                    c1, c2 = st.columns(2)
                    name = c1.text_input("고객사명", value=c["name"])
                    contact = c2.text_input("담당자", value=c.get("contact") or "")
                    phone = c1.text_input("전화", value=c.get("phone") or "")
                    email = c2.text_input("이메일", value=c.get("email") or "")
                    address = st.text_input("주소", value=c.get("address") or "")
                    notes = st.text_area("비고", value=c.get("notes") or "", height=68)
                    active = st.checkbox("활성", value=c["is_active"])
                    ok = st.form_submit_button("수정 저장")
                if ok:
                    db.table("clients").update({
                        "name": name, "contact": contact, "phone": phone,
                        "email": email, "address": address, "notes": notes,
                        "is_active": active,
                    }).eq("id", c["id"]).execute()
                    helpers.clear_caches()
                    st.success("수정되었습니다.")
                    st.rerun()


# ============================================================
# 목록 화면
# ============================================================
def list_screen(db, editable):
    ests = (db.table("estimates").select("*")
            .order("created_at", desc=True).execute().data)

    if editable:
        with st.expander("➕ 새 견적 만들기"):
            client_names = [""] + [c["name"] for c in helpers.load_clients()]

            # ① 빈 견적으로 작성
            st.markdown("**① 새로 작성**")
            with st.form("new_est", clear_on_submit=True):
                c1, c2 = st.columns(2)
                title = c1.text_input("견적명 *")
                client = c2.selectbox("고객사 (고객사 관리 탭에서 등록)",
                                      client_names)
                ok = st.form_submit_button("생성", type="primary")
            if ok:
                if not title:
                    st.error("견적명을 입력하세요.")
                else:
                    no = helpers.next_no("estimates", "estimate_no", "Q")
                    est = db.table("estimates").insert({
                        "estimate_no": no, "title": title, "client": client,
                        "client_id": helpers.client_id_by_name(client),
                        "created_by": st.session_state["user"]["id"],
                    }).execute().data[0]
                    db.table("estimate_versions").insert({
                        "estimate_id": est["id"], "version_no": 1,
                        "version_label": "본견적",
                        "version_date": str(date.today()),
                    }).execute()
                    st.session_state["est_open"] = est["id"]
                    st.rerun()

            # ② 기존 프로젝트에서 정보 불러오기
            st.divider()
            st.markdown("**② 기존 프로젝트에서 불러오기** "
                        "(프로젝트명·고객사·예산을 견적으로 가져옵니다)")
            prjs = (db.table("projects").select("*")
                    .eq("is_common_pool", False)
                    .order("created_at", desc=True).execute().data)
            if not prjs:
                st.caption("등록된 프로젝트가 없습니다.")
            else:
                pc = st.columns([3, 1])
                psel = pc[0].selectbox(
                    "프로젝트 선택", prjs,
                    format_func=lambda p: f"{p['code']} {p['name']} "
                    f"(${float(p.get('contract_amount') or 0):,.0f})",
                    key="est_from_prj")
                if pc[1].button("📥 불러와 생성", type="primary"):
                    no = helpers.next_no("estimates", "estimate_no", "Q")
                    est = db.table("estimates").insert({
                        "estimate_no": no, "title": psel["name"],
                        "client": psel.get("client"),
                        "client_id": helpers.client_id_by_name(
                            psel.get("client") or ""),
                        "project_id": psel["id"],
                        "created_by": st.session_state["user"]["id"],
                    }).execute().data[0]
                    ver = db.table("estimate_versions").insert({
                        "estimate_id": est["id"], "version_no": 1,
                        "version_label": "본견적",
                        "version_date": str(date.today()),
                        "order_amount": float(psel.get("contract_amount") or 0),
                    }).execute().data[0]
                    # 프로젝트 예산 라인 -> 견적 라인으로 복사
                    blines = (db.table("budget_lines").select("*")
                              .eq("project_id", psel["id"]).execute().data)
                    if blines:
                        db.table("estimate_lines").insert([{
                            "version_id": ver["id"],
                            "account_id": b["account_id"],
                            "item_name": b.get("item_name") or "",
                            "vendor_id": b.get("vendor_id"),
                            "amount": b.get("amount") or 0,
                        } for b in blines]).execute()
                    st.session_state["est_open"] = est["id"]
                    st.rerun()

    st.subheader("견적 목록")
    if not ests:
        st.info("등록된 견적이 없습니다.")
        return
    spec = [1.5, 3, 2, 1.5, 1.5, 1]
    helpers.list_header(spec, ["견적번호", "견적명", "고객사", "상태",
                               "작성일", ""])
    for e in ests:
        with st.container(border=True):
            cols = st.columns(spec)
            cols[0].markdown(f"**{e['estimate_no']}**")
            cols[1].write(e["title"])
            cols[2].write(e.get("client") or "-")
            cols[3].markdown(STATUS_BADGE.get(e["status"], e["status"]))
            cols[4].write(str(e["created_at"])[:10])
            if cols[5].button("열기", key=f"open_{e['id']}"):
                st.session_state["est_open"] = e["id"]
                st.rerun()


# ============================================================
# 상세 화면
# ============================================================
def detail_screen(db, est_id, editable):
    est = db.table("estimates").select("*").eq("id", est_id).execute().data
    if not est:
        st.session_state["est_open"] = None
        st.rerun()
    est = est[0]
    versions = (db.table("estimate_versions").select("*")
                .eq("estimate_id", est_id).order("version_no").execute().data)

    top = st.columns([1, 5, 2])
    if top[0].button("← 목록"):
        st.session_state["est_open"] = None
        st.rerun()
    top[1].subheader(f"{est['estimate_no']}  {est['title']}")
    top[2].markdown(f"### {STATUS_BADGE.get(est['status'], est['status'])}")
    cap = st.columns([3, 2])
    cap[0].caption(f"고객사: {est.get('client') or '-'}")
    if editable and est["status"] in ("작성중", "승인대기"):
        with cap[1].popover("고객사 변경"):
            names = [""] + [c["name"] for c in helpers.load_clients()]
            cur = est.get("client") or ""
            sel = st.selectbox("고객사 선택", names,
                               index=names.index(cur) if cur in names else 0,
                               key=f"chg_client_{est_id}")
            if st.button("변경 저장", key=f"chg_client_btn_{est_id}"):
                db.table("estimates").update({
                    "client": sel,
                    "client_id": helpers.client_id_by_name(sel),
                }).eq("id", est_id).execute()
                st.rerun()

    # ---- 버전 선택 ----
    labels = [f"{v['version_label']} ({v['version_date']}) - {v['status']}"
              for v in versions]
    vidx = st.selectbox("버전", range(len(versions)),
                        index=len(versions) - 1,
                        format_func=lambda i: labels[i])
    ver = versions[vidx]
    # 수주/실주 전이면 어느 단계(작성중/승인대기/승인/제출)든 수정 가능
    pre_award = est["status"] not in ("수주", "실주")
    ver_editable = editable and pre_award
    if ver_editable and ver["status"] != "작성중":
        st.warning(f"이 견적은 '{ver['status']}' 상태입니다. 수정하면 변경 이력이 "
                   "기록되며, 저장 시 '작성중'으로 되돌아가 재승인이 필요합니다.")

    # ---- 수주금액 + 항목 입력 ----
    order_amount = st.number_input(
        "수주금액 (USD)", min_value=0.0, step=1000.0,
        value=float(ver["order_amount"] or 0),
        disabled=not ver_editable, format="%.2f")

    lines = (db.table("estimate_lines").select("*")
             .eq("version_id", ver["id"]).order("sort_order").execute().data)
    vendor_opts = [""] + [v["name"] for v in helpers.load_vendors()]
    df = pd.DataFrame([{
        "분류": helpers.account_by_id(l["account_id"])["name_kr"],
        "항목명": l["item_name"],
        "협력업체": helpers.vendor_name_by_id(l.get("vendor_id")),
        "금액": float(l["amount"] or 0),
        "비고": l.get("notes") or "",
    } for l in lines]) if lines else pd.DataFrame(
        columns=["분류", "항목명", "협력업체", "금액", "비고"])

    st.markdown("**원가 항목** (직접비는 항목 단위로, 간접비는 분류 단위로 입력)")
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, key=f"ed_{ver['id']}",
        disabled=not ver_editable,
        column_config={
            "분류": st.column_config.SelectboxColumn(
                "분류", options=helpers.COST_MIDS, required=True),
            "항목명": st.column_config.TextColumn("항목명", required=True),
            "협력업체": st.column_config.SelectboxColumn(
                "협력업체", options=vendor_opts),
            "금액": st.column_config.NumberColumn("금액(USD)", format="%.2f"),
            "비고": st.column_config.TextColumn("비고"),
        })

    if ver_editable:
        # ---- 노무비/판관비 % 자동 계산 (수주금액 기준) ----
        def _cur_pct(mid_name):
            # 이미 입력된 값이 있으면 그 비율, 없으면 기본 15%
            if df.empty:
                return 15.0
            v = df[df["분류"] == mid_name]["금액"].sum()
            if v and order_amount:
                return round(float(v) / float(order_amount) * 100, 1)
            # 해당 분류 행 자체가 없으면 기본 15%
            has_row = (df["분류"] == mid_name).any()
            return 0.0 if has_row else 15.0

        pc = st.columns([1.2, 1.2, 3])
        labor_pct = pc[0].number_input(
            "노무비 (수주금액의 %)", 0.0, 100.0, step=0.5,
            value=_cur_pct("노무비"), key=f"lp_{ver['id']}")
        sga_pct = pc[1].number_input(
            "판관비 (수주금액의 %)", 0.0, 100.0, step=0.5,
            value=_cur_pct("판관비"), key=f"sp_{ver['id']}")
        pc[2].caption("기본 15%로 설정되어 있으며, 요율을 바꾸면 저장 시 "
                      "'수주금액 × %'로 노무비/판관비 행이 자동 갱신됩니다. "
                      "(직접 입력하려면 0으로 두고 표에 행 추가)")

        # 비작성중(승인대기/승인/제출) 상태면 변경 사유 입력
        change_reason = ""
        if ver["status"] != "작성중":
            change_reason = st.text_input(
                "변경 사유 (이력 기록용) *", key=f"crsn_{ver['id']}",
                placeholder="예: 외주비 단가 조정, 수주금액 변경 등")

        if st.button("💾 저장", type="primary"):
            if ver["status"] != "작성중" and not change_reason.strip():
                st.error("변경 사유를 입력하세요.")
            else:
                old_order = float(ver["order_amount"] or 0)
                old_lines = (db.table("estimate_lines").select("*")
                             .eq("version_id", ver["id"]).execute().data)
                old_total = sum(float(l["amount"] or 0) for l in old_lines)

                save_lines(db, ver["id"], order_amount, edited,
                           labor_pct, sga_pct)

                new_lines = (db.table("estimate_lines").select("amount")
                             .eq("version_id", ver["id"]).execute().data)
                new_total = sum(float(l["amount"] or 0) for l in new_lines)
                changes = []
                if abs(old_order - order_amount) > 0.01:
                    changes.append(("수주금액", f"${old_order:,.0f}",
                                    f"${order_amount:,.0f}"))
                if abs(old_total - new_total) > 0.01:
                    changes.append(("원가 합계", f"${old_total:,.0f}",
                                    f"${new_total:,.0f}"))
                if not changes:
                    changes.append(("항목 수정", "", ""))
                for field, ov, nv in changes:
                    db.table("estimate_changes").insert({
                        "version_id": ver["id"],
                        "changed_by": st.session_state["user"]["id"],
                        "field": field, "old_value": ov, "new_value": nv,
                        "reason": change_reason or "작성 중 수정",
                    }).execute()

                if ver["status"] != "작성중":
                    db.table("estimate_versions").update(
                        {"status": "작성중"}).eq("id", ver["id"]).execute()
                    db.table("estimates").update(
                        {"status": "작성중"}).eq("id", est["id"]).execute()
                    st.success("저장되었습니다. 변경 이력이 기록되었고 "
                               "'작성중'으로 되돌아갔습니다 (재승인 필요).")
                else:
                    st.success("저장되었습니다.")
                st.rerun()

    # ---- 원가 요약 ----
    show_summary(edited, order_amount)

    # ---- 속지 (Management / 기타) ----
    st.divider()
    sheets_section(db, ver, ver_editable)

    # ---- 별첨 (제작비용/직접경비/현지운영비) ----
    st.divider()
    attachments_section(db, ver, ver_editable)

    # ---- 변경 이력 ----
    chgs = (db.table("estimate_changes").select("*")
            .eq("version_id", ver["id"])
            .order("changed_at", desc=True).execute().data)
    if chgs:
        with st.expander(f"📝 변경 이력 ({len(chgs)}건)"):
            umap = {u["id"]: u["name"] for u in
                    db.table("app_users").select("id,name").execute().data}
            st.dataframe(pd.DataFrame([{
                "일시": str(c["changed_at"])[:16].replace("T", " "),
                "변경자": umap.get(c.get("changed_by"), "-"),
                "항목": c.get("field") or "",
                "이전": c.get("old_value") or "",
                "변경": c.get("new_value") or "",
                "사유": c.get("reason") or "",
            } for c in chgs]), use_container_width=True, hide_index=True)

    st.divider()

    # ---- 워크플로우 / 출력 ----
    workflow_buttons(db, est, versions, ver, editable)
    excel_button(db, est, versions)

    # ---- 관리자: 견적 삭제 (수주 전 + 품의서 미생성 시) ----
    if auth.is_admin():
        st.divider()
        blockers = []
        if est["status"] == "수주":
            blockers.append("이미 수주되어 프로젝트로 전환됨")
        # 이 견적으로 만든 품의서가 있는지
        linked_props = (db.table("proposals").select("doc_no")
                        .eq("estimate_id", est_id).execute().data)
        # estimate_id 없이 이름으로 연결된 품의서도 점검
        if not linked_props:
            linked_props = [
                p for p in db.table("proposals")
                .select("doc_no,project_name").execute().data
                if (p.get("project_name") or "").strip() == est["title"].strip()]
        if linked_props:
            blockers.append(f"연결된 품의서 {len(linked_props)}건")

        if blockers:
            st.caption(f"🔒 이 견적은 삭제할 수 없습니다 ({', '.join(blockers)}). "
                       "수주 전이고 품의서가 생성되지 않은 견적만 삭제 가능합니다. "
                       "품의서가 연결된 경우 품의서를 먼저 삭제하세요.")
        else:
            with st.popover("🗑️ 견적 삭제 (관리자)"):
                st.warning(f"'{est['estimate_no']} {est['title']}' 견적과 "
                           "모든 버전/항목이 영구 삭제됩니다.")
                confirm = st.text_input("확인을 위해 견적번호를 입력하세요",
                                        key=f"del_{est_id}")
                if st.button("삭제 확정", type="primary", key=f"delb_{est_id}"):
                    if confirm.strip() == est["estimate_no"]:
                        db.table("estimates").delete().eq("id", est_id).execute()
                        st.session_state["est_open"] = None
                        st.rerun()
                    else:
                        st.error("견적번호가 일치하지 않습니다.")


def save_lines(db, version_id, order_amount, edited: pd.DataFrame,
               labor_pct: float = 0, sga_pct: float = 0):
    db.table("estimate_versions").update(
        {"order_amount": order_amount}).eq("id", version_id).execute()

    rows = []
    direct_sum = 0.0
    for i, r in edited.iterrows():
        if not _t(r.get("분류")) or not _t(r.get("항목명")):
            continue
        mid = _t(r["분류"])
        # % 자동계산 대상 분류는 수동 행 제외 (자동 행으로 대체)
        if (labor_pct > 0 and mid == "노무비") or \
           (sga_pct > 0 and mid == "판관비"):
            continue
        acc_id = helpers.account_id_by_name(mid, 2)
        if not acc_id:
            continue
        amt = _f(r.get("금액"))
        if mid in helpers.COST_MIDS_DIRECT:
            direct_sum += amt
        rows.append({
            "version_id": version_id, "account_id": acc_id,
            "item_name": str(r["항목명"]),
            "vendor_id": helpers.vendor_id_by_name(str(r.get("협력업체") or "")),
            "amount": amt,
            "notes": str(r.get("비고") or ""), "sort_order": i,
        })
    # 자동 계산 행 추가 (수주금액 × %)
    auto = [("노무비", labor_pct), ("판관비", sga_pct)]
    for mid, pct in auto:
        if pct > 0:
            rows.append({
                "version_id": version_id,
                "account_id": helpers.account_id_by_name(mid, 2),
                "item_name": mid, "vendor_id": None,
                "amount": round(float(order_amount or 0) * pct / 100, 2),
                "notes": f"수주금액의 {pct:g}% 자동계산",
                "sort_order": 900 + len(rows),
            })

    # 속지 자동합산 라인은 보존 (save_lines가 지우지 않음)
    existing = (db.table("estimate_lines").select("*")
                .eq("version_id", version_id).execute().data)
    sheet_lines = [l for l in existing if (l.get("notes") or "") == SHEET_NOTE]

    # 수동 입력 행도 없고 속지 합산도 없으면 = 빈 저장 -> 기존 보존 (데이터 보호)
    if not rows and not sheet_lines and existing:
        return  # 실수로 빈 표 저장 시 기존 라인 삭제 방지

    # 속지 합산 외의 일반 라인만 교체
    non_sheet_ids = [l["id"] for l in existing
                     if (l.get("notes") or "") != SHEET_NOTE]
    for lid in non_sheet_ids:
        db.table("estimate_lines").delete().eq("id", lid).execute()
    if rows:
        db.table("estimate_lines").insert(rows).execute()


def show_summary(edited: pd.DataFrame, order_amount: float):
    def mid_sum(mid):
        if edited.empty:
            return 0.0
        m = edited[edited["분류"] == mid]["금액"]
        return float(m.fillna(0).sum()) if not m.empty else 0.0

    c = sum(mid_sum(m) for m in helpers.COST_MIDS_DIRECT)
    d = sum(mid_sum(m) for m in helpers.COST_MIDS_INDIRECT)
    profit = order_amount - c - d

    def pct(x):
        return f"{x / order_amount * 100:.0f}%" if order_amount else "-"

    rows = [("수주금액", order_amount, "100%" if order_amount else "-")]
    rows += [(m, mid_sum(m), pct(mid_sum(m))) for m in helpers.COST_MIDS_DIRECT]
    rows.append(("직접비 소계 (C)", c, pct(c)))
    rows += [(m, mid_sum(m), pct(mid_sum(m))) for m in helpers.COST_MIDS_INDIRECT]
    rows.append(("간접비 소계 (D)", d, pct(d)))
    rows.append(("총원가 계 (C+D)", c + d, pct(c + d)))
    rows.append(("영업이익", profit, pct(profit)))
    sdf = pd.DataFrame(rows, columns=["구분", "금액(USD)", "비율"])
    sdf["금액(USD)"] = sdf["금액(USD)"].map(lambda x: f"{x:,.2f}")
    st.markdown("**원가 요약**")
    st.dataframe(sdf, use_container_width=True, hide_index=True)


# ============================================================
# 상태 워크플로우
# ============================================================
def set_status(db, est, ver, ver_status, est_status=None, extra=None):
    upd = {"status": ver_status}
    if extra:
        upd.update(extra)
    db.table("estimate_versions").update(upd).eq("id", ver["id"]).execute()
    if est_status:
        db.table("estimates").update({"status": est_status}).eq("id", est["id"]).execute()
    st.rerun()


def workflow_buttons(db, est, versions, ver, editable):
    user = st.session_state["user"]
    admin = auth.is_admin()
    c = st.columns(4)

    if est["status"] in ("수주", "실주"):
        st.info(f"이 견적은 '{est['status']}' 처리되어 더 이상 변경할 수 없습니다."
                + (f" (프로젝트 연결됨)" if est.get("project_id") else ""))
        return

    if ver["status"] == "작성중" and editable:
        if c[0].button("📤 승인 요청"):
            set_status(db, est, ver, "승인대기", "승인대기")

    elif ver["status"] == "승인대기":
        if admin:
            if c[0].button("✅ 승인", type="primary"):
                from datetime import datetime
                set_status(db, est, ver, "승인", "승인", {
                    "approved_by": user["id"],
                    "approved_at": datetime.now().isoformat()})
            if c[1].button("↩️ 반려 (작성중으로)"):
                set_status(db, est, ver, "작성중", "작성중")
        else:
            st.info("관리자 승인 대기 중입니다.")

    elif ver["status"] == "승인" and editable:
        if c[0].button("📨 제출 처리", type="primary"):
            from datetime import datetime
            set_status(db, est, ver, "제출", "제출",
                       {"submitted_at": datetime.now().isoformat()})

    elif ver["status"] == "제출":
        admin = auth.is_admin()
        if editable and c[0].button("🔄 변경견적 생성"):
            create_new_version(db, est, versions)
        if admin:
            if c[1].button("🏆 수주 승인 (관리자)", type="primary"):
                st.session_state["est_award"] = True
            if c[2].button("❌ 실주 처리 (관리자)"):
                db.table("estimates").update({"status": "실주"}).eq("id", est["id"]).execute()
                st.rerun()
        else:
            st.info("수주/실주 처리는 관리자만 가능합니다.")

    if st.session_state.get("est_award"):
        award_form(db, est, ver)


def create_new_version(db, est, versions):
    last = versions[-1]
    n = last["version_no"] + 1
    new_ver = db.table("estimate_versions").insert({
        "estimate_id": est["id"], "version_no": n,
        "version_label": f"변경견적 {n-1}차",
        "version_date": str(date.today()),
        "order_amount": last["order_amount"],
    }).execute().data[0]
    lines = (db.table("estimate_lines").select("*")
             .eq("version_id", last["id"]).execute().data)
    if lines:
        db.table("estimate_lines").insert([{
            "version_id": new_ver["id"], "account_id": l["account_id"],
            "item_name": l["item_name"], "vendor_id": l.get("vendor_id"),
            "amount": l["amount"], "notes": l.get("notes"),
            "sort_order": l["sort_order"],
        } for l in lines]).execute()
    # 속지 복사
    sheets = (db.table("estimate_sheets").select("*")
              .eq("version_id", last["id"]).execute().data)
    for s in sheets:
        ns = db.table("estimate_sheets").insert({
            "version_id": new_ver["id"], "sheet_name": s["sheet_name"],
            "account_mid": s["account_mid"], "sort_order": s["sort_order"],
        }).execute().data[0]
        items = (db.table("estimate_sheet_items").select("*")
                 .eq("sheet_id", s["id"]).order("sort_order").execute().data)
        if items:
            db.table("estimate_sheet_items").insert([{
                "sheet_id": ns["id"], "section": i.get("section"),
                "description": i.get("description"), "unit": i.get("unit"),
                "qty1": i.get("qty1"), "qty2": i.get("qty2"),
                "price": i.get("price"), "remark": i.get("remark"),
                "sort_order": i["sort_order"],
            } for i in items]).execute()
    db.table("estimates").update({"status": "작성중"}).eq("id", est["id"]).execute()
    st.rerun()


# ============================================================
# 수주 처리: 프로젝트 생성 + 예산 편성
# ============================================================
def award_form(db, est, ver):
    st.markdown("### 🏆 수주 처리")

    # 기존 예산 미편성 프로젝트 (연결 후보)
    all_prj = (db.table("projects").select("*")
               .eq("is_common_pool", False)
               .neq("status", "취소").execute().data)
    blines = db.table("budget_lines").select("project_id").execute().data
    budgeted_ids = {b["project_id"] for b in blines}
    # 예산 미편성 + 견적 미연결 프로젝트만 후보
    unbudgeted = [p for p in all_prj
                  if p["id"] not in budgeted_ids
                  and not p.get("estimate_id")]

    mode = st.radio(
        "처리 방식",
        ["새 프로젝트 생성", "기존 프로젝트에 연결 (예산 미편성 건)"],
        horizontal=True,
        help="예산은 항상 품의서 승인 후 프로젝트 예산 관리에서 편성합니다.")

    if mode == "기존 프로젝트에 연결 (예산 미편성 건)":
        if not unbudgeted:
            st.info("예산 미편성 + 견적 미연결 상태의 프로젝트가 없습니다. "
                    "'새 프로젝트 생성'을 사용하세요.")
            if st.button("취소"):
                st.session_state.pop("est_award", None)
                st.rerun()
            return
        with st.form("award_link"):
            psel = st.selectbox(
                "연결할 프로젝트", unbudgeted,
                format_func=lambda p: f"{p['code']} {p['name']} "
                f"[{p['status']}]")
            ok = st.form_submit_button("이 프로젝트에 수주 연결", type="primary")
            cancel = st.form_submit_button("취소")
        if cancel:
            st.session_state.pop("est_award", None)
            st.rerun()
        if ok:
            db.table("projects").update({
                "estimate_id": est["id"],
                "contract_amount": ver["order_amount"],
                "client": est.get("client") or psel.get("client"),
            }).eq("id", psel["id"]).execute()
            db.table("estimates").update(
                {"status": "수주", "project_id": psel["id"]}
            ).eq("id", est["id"]).execute()
            st.session_state.pop("est_award", None)
            st.success(f"{psel['code']} 프로젝트에 수주 연결 완료! "
                       "(예산은 품의서 승인 후 편성하세요)")
            st.rerun()
        return

    # ---- 새 프로젝트 생성 ----
    st.caption("예산은 편성하지 않고 프로젝트만 생성합니다. "
               "품의서 승인 후 프로젝트 > 예산 관리에서 편성하세요.")
    with st.form("award"):
        code = st.text_input("프로젝트 코드",
                             value=helpers.next_no("projects", "code", "P"))
        name = st.text_input("프로젝트명", value=est["title"])
        c1, c2 = st.columns(2)
        location = c1.text_input("현장 위치")
        pm = c2.text_input("담당 PM")
        c3, c4 = st.columns(2)
        start = c3.date_input("시작일", value=date.today())
        end = c4.date_input("종료일", value=None)
        ok = st.form_submit_button("프로젝트 생성", type="primary")
        cancel = st.form_submit_button("취소")
    if cancel:
        st.session_state.pop("est_award", None)
        st.rerun()
    if ok:
        prj = db.table("projects").insert({
            "code": code, "name": name, "client": est.get("client"),
            "location": location, "pm": pm,
            "contract_amount": ver["order_amount"],
            "start_date": str(start), "end_date": str(end) if end else None,
            "estimate_id": est["id"],
        }).execute().data[0]
        # 협력업체 할당만 견적에서 가져옴 (예산은 품의서 승인 후 편성)
        lines = (db.table("estimate_lines").select("vendor_id,amount")
                 .eq("version_id", ver["id"]).execute().data)
        v_amounts = {}
        for l in lines:
            if l.get("vendor_id"):
                v_amounts[l["vendor_id"]] = \
                    v_amounts.get(l["vendor_id"], 0) + float(l["amount"] or 0)
        if v_amounts:
            db.table("project_vendors").insert([
                {"project_id": prj["id"], "vendor_id": vid,
                 "contract_amount": amt}
                for vid, amt in v_amounts.items()]).execute()
        db.table("estimates").update(
            {"status": "수주", "project_id": prj["id"]}
        ).eq("id", est["id"]).execute()
        st.session_state.pop("est_award", None)
        st.success(f"프로젝트 {code} 생성 완료! 예산은 품의서 승인 후 "
                   "프로젝트 > 예산 관리에서 편성하세요.")
        st.rerun()


# ============================================================
# 엑셀 출력
# ============================================================
def excel_button(db, est, versions):
    import re as _re
    _safe = _re.sub(r'[\\/*?:\[\]<>|"]', "", str(est.get("title") or "")).strip()
    _base = f"{est['estimate_no']}_{_safe}" if _safe else est["estimate_no"]
    st.markdown("#### 📄 출력")
    st.caption("📌 견적서 = **고객 제출용** 문서입니다. 본사 승인은 "
               "품의서 관리에서 별도로 진행하세요.")
    col1, col2, col3 = st.columns(3)

    # ---- 1) 견적 원가표 (내부용, 버전 비교) ----
    lines_map = {}
    for v in versions:
        ls = (db.table("estimate_lines").select("*")
              .eq("version_id", v["id"]).order("sort_order").execute().data)
        lines_map[v["id"]] = [{
            "mid": helpers.account_by_id(l["account_id"])["name_kr"],
            "item": l["item_name"],
            "amount": float(l["amount"] or 0),
        } for l in ls]
    data = build_estimate_excel(est, versions, lines_map)
    col1.download_button(
        "⬇️ 원가표 (내부용·버전비교)", data=data,
        file_name=f"{_base}_견적원가표.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True)

    # ---- 견적서 데이터 준비 ----
    from quotation_excel import build_quotation_excel
    latest = versions[-1]
    direct_items = [
        {"item": l["item"], "amount": l["amount"]}
        for l in lines_map[latest["id"]]
        if l["mid"] in helpers.COST_MIDS_DIRECT
    ]
    est_q = dict(est)
    est_q["_order_amount"] = float(latest["order_amount"] or 0)
    est_q["_direct_items"] = direct_items
    est_q["_sheets"] = load_sheets_data(db, latest["id"])
    # 별첨 (제작비용/직접경비/현지운영비)
    est_q["sheet1_data"] = latest.get("sheet1_data")
    est_q["sheet2_data"] = latest.get("sheet2_data")
    est_q["sheet3_data"] = latest.get("sheet3_data")

    # ---- 2) 견적서 (내부 검토용: 원가/이익 표시) ----
    qdata_internal = build_quotation_excel(est_q, customer=False)
    col2.download_button(
        "⬇️ 견적서 (내부 검토용)", data=qdata_internal,
        file_name=f"{_base}_Quotation_내부용.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True)

    # ---- 3) 견적서 (고객 제출용: 원가/이익 숨김) ----
    qdata_customer = build_quotation_excel(est_q, customer=True)
    col3.download_button(
        "⬇️ 견적서 (고객 제출용)", data=qdata_customer,
        file_name=f"{_base}_Quotation_고객용.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True, type="primary")
    st.caption("· **고객 제출용**: 갑지에 총액(Grand Total)만 표시 — "
               "직접비·이익(Profit)·간접비 등 내부 원가는 숨겨집니다.  \n"
               "· **내부 검토용**: 직접비/Profit/간접비 구조까지 모두 포함 + "
               "속지 상세 시트.")


# ============================================================
# 속지 (Management / 기타) 관리
# ============================================================

MGMT_TEMPLATE = [
    ("1. Labor", "ON FIELD MANAGER", "Month", 1, 0, 15330, ""),
    ("1. Labor", "Project Control Manager", "Month", 1, 0, 15330, ""),
    ("1. Labor", "Quality management", "Month", 1, 0, 15330, ""),
    ("1. Labor", "Safety management", "Month", 1, 0, 15330, ""),
    ("1. Labor", "BIM, Drawing", "Month", 1, 0, 25000, ""),
    ("1. Labor", "Overhead costs", "EA", 1, 0, 10305, ""),
    ("2. Safety", "Provide safety gear/PPE/supplies", "Month", 0, 0, 2000, ""),
    ("2. Safety", "OSHA Training", "Month", 0, 0, 1000, ""),
    ("3. Subcontractor installation", "", "EA", 0, 0, 0, ""),
    ("3. Subcontractor installation", "", "EA", 0, 0, 0, ""),
]


def _f(v):
    """NaN/None/빈값 -> 0.0 안전 변환"""
    try:
        f = float(v)
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def _t(v):
    """NaN/None -> '' 안전 문자열"""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def item_amount(q1, q2, price):
    """수량이 0(미입력)이면 곱하지 않음. 단가 0이면 0."""
    p = _f(price)
    if p == 0:
        return 0.0
    a = p
    if _f(q1):
        a *= _f(q1)
    if _f(q2):
        a *= _f(q2)
    return a


def load_sheets_data(db, version_id):
    sheets = (db.table("estimate_sheets").select("*")
              .eq("version_id", version_id).order("sort_order").execute().data)
    out = []
    for s in sheets:
        items = (db.table("estimate_sheet_items").select("*")
                 .eq("sheet_id", s["id"]).order("sort_order").execute().data)
        out.append({"name": s["sheet_name"], "account": s["account_mid"],
                    "items": items})
    return out


def sync_sheets_to_lines(db, version_id):
    """속지 합계를 견적 항목(직접비)으로 자동 반영"""
    db.table("estimate_lines").delete()         .eq("version_id", version_id).eq("notes", SHEET_NOTE).execute()
    rows = []
    for i, s in enumerate(load_sheets_data(db, version_id)):
        total = sum(item_amount(it.get("qty1"), it.get("qty2"),
                                it.get("price")) for it in s["items"])
        acc_id = helpers.account_id_by_name(s["account"], 2)
        if acc_id:
            rows.append({
                "version_id": version_id, "account_id": acc_id,
                "item_name": s["name"], "vendor_id": None,
                "amount": round(total, 2), "notes": SHEET_NOTE,
                "sort_order": 800 + i,
            })
    if rows:
        db.table("estimate_lines").insert(rows).execute()


def sheets_section(db, ver, editable):
    st.markdown("#### 📑 속지 (Management / 기타 Detail Sheet)")
    st.caption("속지 합계는 지정한 계정으로 견적 직접비에 자동 합산되고, "
               "견적서 엑셀에 시트별로 출력됩니다. "
               "금액 = 단가 × Q'ty① × Q'ty② (0은 미입력으로 간주)")

    sheets = (db.table("estimate_sheets").select("*")
              .eq("version_id", ver["id"]).order("sort_order").execute().data)

    # ---- 속지 추가 ----
    if editable:
        c = st.columns([1.6, 1.6, 1.2, 1])
        if c[0].button("➕ Management 속지 추가 (기본 단가)",
                       disabled=any(s["sheet_name"] == "Management"
                                    for s in sheets)):
            s = db.table("estimate_sheets").insert({
                "version_id": ver["id"], "sheet_name": "Management",
                "account_mid": "직접경비", "sort_order": 0,
            }).execute().data[0]
            db.table("estimate_sheet_items").insert([{
                "sheet_id": s["id"], "section": t[0], "description": t[1],
                "unit": t[2], "qty1": t[3], "qty2": t[4], "price": t[5],
                "remark": t[6], "sort_order": i,
            } for i, t in enumerate(MGMT_TEMPLATE)]).execute()
            sync_sheets_to_lines(db, ver["id"])
            st.rerun()
        new_name = c[1].text_input("기타 속지 이름", key=f"sn_{ver['id']}",
                                   placeholder="예: Labor & Materials")
        new_acc = c[2].selectbox("계정", helpers.COST_MIDS_DIRECT,
                                 index=1, key=f"sa_{ver['id']}")
        if c[3].button("➕ 속지 생성"):
            if not new_name.strip():
                st.error("속지 이름을 입력하세요.")
            else:
                db.table("estimate_sheets").insert({
                    "version_id": ver["id"], "sheet_name": new_name.strip(),
                    "account_mid": new_acc, "sort_order": len(sheets) + 1,
                }).execute()
                st.rerun()

    if not sheets:
        st.info("속지가 없습니다. (속지 없이도 견적 작성 가능)")
        return

    sel = st.selectbox("속지 선택", sheets,
                       format_func=lambda s:
                       f"{s['sheet_name']}  →  직접비 > {s['account_mid']}",
                       key=f"ssel_{ver['id']}")

    items = (db.table("estimate_sheet_items").select("*")
             .eq("sheet_id", sel["id"]).order("sort_order").execute().data)
    idf = pd.DataFrame([{
        "구분": i.get("section") or "", "DESCRIPTION": i.get("description") or "",
        "Unit": i.get("unit") or "", "Q'ty①": float(i.get("qty1") or 0),
        "Q'ty②": float(i.get("qty2") or 0), "단가": float(i.get("price") or 0),
        "비고": i.get("remark") or "",
    } for i in items]) if items else pd.DataFrame(
        columns=["구분", "DESCRIPTION", "Unit", "Q'ty①", "Q'ty②", "단가", "비고"])

    edited_i = st.data_editor(
        idf, num_rows="dynamic", use_container_width=True, hide_index=True,
        disabled=not editable, key=f"sed_{sel['id']}",
        column_config={
            "Q'ty①": st.column_config.NumberColumn("Q'ty①", format="%.2f"),
            "Q'ty②": st.column_config.NumberColumn("Q'ty②", format="%.2f"),
            "단가": st.column_config.NumberColumn("단가($)", format="%.2f"),
        })

    total = sum(item_amount(r.get("Q'ty①"), r.get("Q'ty②"), r.get("단가"))
                for _, r in edited_i.iterrows())
    mc = st.columns([1.4, 1.4, 2.2])
    mc[0].metric(f"'{sel['sheet_name']}' 합계", f"${total:,.2f}")
    acc_sel = mc[1].selectbox("합산 계정 (직접비)", helpers.COST_MIDS_DIRECT,
                              index=helpers.COST_MIDS_DIRECT.index(
                                  sel["account_mid"])
                              if sel["account_mid"] in helpers.COST_MIDS_DIRECT
                              else 2,
                              disabled=not editable, key=f"acc_{sel['id']}")

    if editable:
        bc = st.columns([1.2, 1.2, 3])
        if bc[0].button("💾 속지 저장 (직접비 자동 반영)", type="primary",
                        key=f"ssv_{sel['id']}"):
            db.table("estimate_sheet_items").delete()                 .eq("sheet_id", sel["id"]).execute()
            rows = []
            for i, r in edited_i.iterrows():
                if not _t(r.get("DESCRIPTION")) and not _t(r.get("구분")):
                    continue
                rows.append({
                    "sheet_id": sel["id"],
                    "section": _t(r.get("구분")),
                    "description": _t(r.get("DESCRIPTION")),
                    "unit": _t(r.get("Unit")),
                    "qty1": _f(r.get("Q'ty①")),
                    "qty2": _f(r.get("Q'ty②")),
                    "price": _f(r.get("단가")),
                    "remark": _t(r.get("비고")), "sort_order": i,
                })
            if rows:
                db.table("estimate_sheet_items").insert(rows).execute()
            db.table("estimate_sheets").update(
                {"account_mid": acc_sel}).eq("id", sel["id"]).execute()
            sync_sheets_to_lines(db, ver["id"])
            st.success(f"저장 완료 — 직접비 > {acc_sel}에 "
                       f"${total:,.2f} 자동 반영되었습니다.")
            st.rerun()
        with bc[1].popover("🗑️ 속지 삭제"):
            st.warning(f"'{sel['sheet_name']}' 속지를 삭제합니다.")
            if st.button("삭제 확정", key=f"sdel_{sel['id']}"):
                db.table("estimate_sheets").delete()                     .eq("id", sel["id"]).execute()
                sync_sheets_to_lines(db, ver["id"])
                st.rerun()


def attachments_section(db, ver, editable):
    """견적 별첨: 제작비용(원재료/외주비)/직접경비/현지운영비"""
    import proposal_sheets as ps
    st.markdown("**📎 별첨 (제작비용 / 직접경비 / 현지운영비)**")
    st.caption("실행품의 양식의 별첨입니다. 입력하면 견적서 내부검토용 "
               "엑셀에 별첨 시트로 포함됩니다.")

    s1 = ver.get("sheet1_data") or ps.empty_sheet1()
    s2 = ver.get("sheet2_data") or ps.empty_sheet2()
    s3 = ver.get("sheet3_data") or ps.empty_sheet3()
    vid = ver["id"]

    colcfg_make = {
        "대분류": st.column_config.TextColumn("대분류"),
        "중분류": st.column_config.TextColumn("중분류(품목)"),
        "수량": st.column_config.NumberColumn("수량", default=1),
        "단가": st.column_config.NumberColumn("단가", format="%.0f"),
        "비고": st.column_config.TextColumn("비고"),
    }
    colcfg_exp = {
        "구분": st.column_config.TextColumn("구분", disabled=True),
        "금액": st.column_config.NumberColumn("금액", format="%.0f"),
        "비고": st.column_config.TextColumn("비고"),
    }

    with st.expander("별첨1 · 제작비용 (원재료/외주비)"):
        st.caption("원재료")
        m_ed = st.data_editor(pd.DataFrame(s1.get("material", [])),
                              num_rows="dynamic", use_container_width=True,
                              disabled=not editable, key=f"est_s1m_{vid}",
                              column_config=colcfg_make)
        st.caption("외주비")
        o_ed = st.data_editor(pd.DataFrame(s1.get("outsource", [])),
                              num_rows="dynamic", use_container_width=True,
                              disabled=not editable, key=f"est_s1o_{vid}",
                              column_config=colcfg_make)
        s1 = {"material": m_ed.to_dict("records"),
              "outsource": o_ed.to_dict("records")}
        mat, out = ps.sheet1_totals(s1)
        st.info(f"재료비 합계: {mat:,.0f}  ·  외주비 합계: {out:,.0f}")

    with st.expander("별첨2 · 직접경비"):
        s2_ed = st.data_editor(pd.DataFrame(s2), use_container_width=True,
                               disabled=not editable, key=f"est_s2_{vid}",
                               column_config=colcfg_exp, hide_index=True)
        s2 = s2_ed.to_dict("records")
        st.info(f"직접경비 합계: {ps.sheet_total(s2):,.0f}")

    with st.expander("별첨3 · 현지운영비"):
        s3_ed = st.data_editor(pd.DataFrame(s3), use_container_width=True,
                               disabled=not editable, key=f"est_s3_{vid}",
                               column_config=colcfg_exp, hide_index=True)
        s3 = s3_ed.to_dict("records")
        st.info(f"현지운영비 합계: {ps.sheet_total(s3):,.0f}")

    if editable and st.button("💾 별첨 저장", key=f"est_att_save_{vid}"):
        db.table("estimate_versions").update({
            "sheet1_data": s1, "sheet2_data": s2, "sheet3_data": s3,
        }).eq("id", vid).execute()
        st.success("별첨이 저장되었습니다.")
        st.rerun()
