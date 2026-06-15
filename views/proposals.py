"""품의서 관리: 견적 기준값 불러오기, 실시간 자동연산, 상신→승인/거절, 엑셀 출력"""
import pandas as pd
import streamlit as st
from datetime import date, datetime

import auth
import helpers
from db import get_db
from proposal_excel import build_proposal_excel

P_TYPES = ["견적 품의", "선집행 품의", "본품의", "변경품의"]
P_BADGE = {"작성중": "🟡 작성중", "상신": "🟠 상신(결재 대기)",
           "승인": "✅ 승인", "거절": "❌ 거절"}

MID_FIELD = {"원재료": "material_cost", "외주비": "outsourcing_cost",
             "직접경비": "direct_expense", "노무비": "labor_cost",
             "제조간접경비": "mfg_overhead", "판관비": "sga_cost"}


def next_doc_no(db):
    prefix = f"미국법인-{date.today().strftime('%Y%m%d')}-"
    rows = (db.table("proposals").select("doc_no")
            .like("doc_no", f"{prefix}%").execute().data)
    return f"{prefix}{len(rows) + 1}"


def estimate_basis(db, est):
    """견적 최신 버전에서 품의서 기초값 + 협력업체별 외주 명세 추출"""
    vers = (db.table("estimate_versions").select("*")
            .eq("estimate_id", est["id"])
            .order("version_no", desc=True).limit(1).execute().data)
    vals = {f: 0.0 for f in MID_FIELD.values()}
    order = 0.0
    vendor_breakdown = []  # [{vendor, vendor_id, mid, item, amount}]
    if vers:
        order = float(vers[0]["order_amount"] or 0)
        lines = (db.table("estimate_lines").select("*")
                 .eq("version_id", vers[0]["id"]).execute().data)
        for l in lines:
            mid = helpers.mid_name_of(l["account_id"])
            if mid in MID_FIELD:
                vals[MID_FIELD[mid]] += float(l["amount"] or 0)
            # 직접비 중 협력업체가 지정된 라인은 업체 명세로 보존
            if mid in helpers.COST_MIDS_DIRECT and l.get("vendor_id"):
                vendor_breakdown.append({
                    "vendor": helpers.vendor_name_by_id(l["vendor_id"]),
                    "vendor_id": l["vendor_id"], "mid": mid,
                    "item": l["item_name"],
                    "amount": float(l["amount"] or 0),
                })
    total = sum(vals.values())
    return {
        "vendor_breakdown": vendor_breakdown,
        "title": f"{est['title']} - 실행품의 건",
        "project_name": est["title"],
        "client": est.get("client") or "",
        "currency": "USD",
        "order_amount": order,
        "target_profit": order - total,
        **vals,
    }


def render():
    helpers.page_title("품의서 관리")
    st.info("🏢 **품의서는 본사 내부 승인용 문서**입니다. "
            "견적서(고객 제출용)에서 기준값을 불러와 작성하고, "
            "관리자 승인/거절로 집행 결재를 관리합니다.")
    db = get_db()
    editable = auth.can_edit("proposals")

    if st.session_state.get("prop_open"):
        detail_screen(db, st.session_state["prop_open"], editable)
    else:
        list_screen(db, editable)


# ============================================================
def list_screen(db, editable):
    if editable:
        with st.expander("➕ 새 품의서 작성"):
            # ---- 방법 1: 견적서에서 불러오기 ----
            st.markdown("**① 견적서 기준값 불러와서 작성** (권장)")
            ests = (db.table("estimates").select("*")
                    .order("created_at", desc=True).execute().data)
            # 견적별로 이미 만들어진 품의 종류 집계
            all_props = (db.table("proposals")
                         .select("estimate_id,proposal_type").execute().data)
            prop_types_by_est = {}
            for pp in all_props:
                eid = pp.get("estimate_id")
                if eid is not None:
                    prop_types_by_est.setdefault(eid, set()).add(
                        pp.get("proposal_type"))

            c = st.columns([1.3, 2.5, 1])
            # 종류를 먼저 선택
            ptype1 = c[0].selectbox("품의 종류", P_TYPES, index=2, key="pt1")

            # 종류별 견적 필터링:
            # 견적품의/선집행품의/본품의 = 같은 종류 품의가 아직 없는 견적만
            # 변경품의 = 본품의가 이미 있는 견적 (기존 것도 표시)
            ONCE_TYPES = ("견적 품의", "선집행 품의", "본품의")
            filtered = []
            for e in ests:
                made = prop_types_by_est.get(e["id"], set())
                if ptype1 in ONCE_TYPES:
                    if ptype1 not in made:
                        filtered.append(e)
                elif ptype1 == "변경품의":
                    # 변경품의는 본품의가 있는 견적에 추가 (없어도 허용)
                    filtered.append(e)
                else:
                    filtered.append(e)

            est_sel = c[1].selectbox(
                "견적 선택", filtered,
                format_func=lambda e:
                f"{e['estimate_no']} {e['title']} [{e['status']}]"
                + (" · 품의: " + ",".join(prop_types_by_est.get(e["id"], []))
                   if prop_types_by_est.get(e["id"]) else "")) \
                if filtered else None
            if not filtered:
                c[1].caption(f"'{ptype1}'를 새로 만들 수 있는 견적이 없습니다.")

            if c[2].button("불러와 작성", type="primary", disabled=not est_sel):
                basis = estimate_basis(db, est_sel)
                vb = basis.pop("vendor_breakdown", [])
                row = db.table("proposals").insert({
                    "doc_no": next_doc_no(db),
                    "proposal_type": ptype1,
                    "copy_to": "구매팀, 재무팀",
                    "created_by": st.session_state["user"]["id"],
                    "vendor_breakdown": vb,
                    "estimate_id": est_sel["id"],
                    **basis,
                }).execute().data[0]
                st.session_state["prop_open"] = row["id"]
                st.rerun()

            st.divider()
            # ---- 방법 2: 빈 양식 ----
            st.markdown("**② 빈 양식으로 작성**")
            c = st.columns([2.5, 1.3, 1])
            title = c[0].text_input("제목")
            ptype2 = c[1].selectbox("품의 종류", P_TYPES, index=1, key="pt2")
            if c[2].button("작성 시작"):
                if not title:
                    st.error("제목을 입력하세요.")
                else:
                    row = db.table("proposals").insert({
                        "doc_no": next_doc_no(db), "title": title,
                        "proposal_type": ptype2,
                        "copy_to": "구매팀, 재무팀",
                        "created_by": st.session_state["user"]["id"],
                    }).execute().data[0]
                    st.session_state["prop_open"] = row["id"]
                    st.rerun()

    props = (db.table("proposals").select("*")
             .order("created_at", desc=True).execute().data)
    st.subheader(f"품의서 목록 ({len(props)})")
    if not props:
        st.info("작성된 품의서가 없습니다.")
        return
    for p in props:
        c = st.columns([2, 1.3, 3, 1.6, 1.2, 0.9])
        c[0].write(f"**{p['doc_no']}**")
        c[1].write(p["proposal_type"])
        c[2].write(p["title"])
        c[3].write(P_BADGE.get(p["status"], p["status"]))
        c[4].write(str(p["created_at"])[:10])
        if c[5].button("열기", key=f"prop_{p['id']}"):
            st.session_state["prop_open"] = p["id"]
            st.rerun()


# ============================================================
def detail_screen(db, pid, editable):
    rows = db.table("proposals").select("*").eq("id", pid).execute().data
    if not rows:
        st.session_state["prop_open"] = None
        st.rerun()
    p = rows[0]
    admin = auth.is_admin()
    locked = p["status"] in ("상신", "승인") or not editable

    top = st.columns([1, 4.5, 2.5])
    if top[0].button("← 목록"):
        st.session_state["prop_open"] = None
        st.rerun()
    top[1].subheader(f"{p['doc_no']}  ·  {p['proposal_type']}")
    top[2].markdown(f"### {P_BADGE.get(p['status'], p['status'])}")
    if p["status"] == "거절" and p.get("result_note"):
        st.error(f"거절 사유: {p['result_note']}")

    k = f"p{pid}_"

    # ---- 기본 정보 (실시간 위젯) ----
    st.markdown("**기본 정보**")
    c = st.columns([2.4, 1.2, 1.2])
    title = c[0].text_input("제목", value=p["title"], disabled=locked, key=k+"t")
    ptype = c[1].selectbox("품의 종류", P_TYPES,
                           index=P_TYPES.index(p["proposal_type"]),
                           disabled=locked, key=k+"pt")
    currency = c[2].selectbox("통화", ["USD", "KRW"],
                              index=["USD", "KRW"].index(p["currency"]),
                              disabled=locked, key=k+"cur")
    cur = "원" if currency == "KRW" else "USD"
    c = st.columns(2)
    project_name = c[0].text_input("1. Project 명", p.get("project_name") or "", disabled=locked, key=k+"pn")
    project_no = c[1].text_input("2. Project NO", p.get("project_no") or "", disabled=locked, key=k+"pno")
    client = c[0].text_input("3. 고객명", p.get("client") or "", disabled=locked, key=k+"cl")
    work_name = c[1].text_input("4. 공사명", p.get("work_name") or "", disabled=locked, key=k+"wn")
    qty = c[0].text_input("5. 수량 (예: 1개월)", p.get("qty") or "", disabled=locked, key=k+"q")
    order_note = c[1].text_input("수주금액 비고 (환율 등)", p.get("order_amount_note") or "", disabled=locked, key=k+"on")
    payment_terms = c[0].text_input("7. 결제 조건", p.get("payment_terms") or "", disabled=locked, key=k+"pt2")
    po_info = c[1].text_input("8. P/O 여부", p.get("po_info") or "", disabled=locked, key=k+"po")
    delivery = c[0].text_input("9. 납기 일자", p.get("delivery_date") or "", disabled=locked, key=k+"dd")
    copy_to = c[1].text_input("사본 배포", p.get("copy_to") or "", disabled=locked, key=k+"ct")
    opinion = st.text_input("의견", p.get("opinion") or "", disabled=locked, key=k+"op")

    # ---- 집행 내역: 값 변경 시 전체 자동 연산 ----
    st.markdown(f"**10. 집행 내역** (단위: {cur}) — 값을 바꾸면 아래 합계가 즉시 재계산됩니다")
    cc = st.columns(4)
    order = cc[0].number_input("(A) 수주금액", value=float(p.get("order_amount") or 0),
                               step=1000.0, format="%.0f", disabled=locked, key=k+"a")
    mat = cc[1].number_input("재료비", value=float(p.get("material_cost") or 0),
                             step=1000.0, format="%.0f", disabled=locked, key=k+"m")
    out = cc[2].number_input("외주비", value=float(p.get("outsourcing_cost") or 0),
                             step=1000.0, format="%.0f", disabled=locked, key=k+"o")
    dexp = cc[3].number_input("직접경비", value=float(p.get("direct_expense") or 0),
                              step=1000.0, format="%.0f", disabled=locked, key=k+"de")
    cc = st.columns(4)
    lab = cc[0].number_input("노무비", value=float(p.get("labor_cost") or 0),
                             step=100.0, format="%.0f", disabled=locked, key=k+"l")
    mfg = cc[1].number_input("제조간접경비", value=float(p.get("mfg_overhead") or 0),
                             step=100.0, format="%.0f", disabled=locked, key=k+"mf")
    sga = cc[2].number_input("판관비", value=float(p.get("sga_cost") or 0),
                             step=100.0, format="%.0f", disabled=locked, key=k+"s")
    res = cc[3].number_input("(F) 예비비", value=float(p.get("reserve") or 0),
                             step=100.0, format="%.0f", disabled=locked, key=k+"r")

    c_sub, d_sub = mat + out + dexp, lab + mfg + sga
    total_e = c_sub + d_sub
    total_g = total_e + res

    auto_profit = st.checkbox("(B) 이익목표 자동계산 = A − G",
                              value=True, disabled=locked, key=k+"ap")
    if auto_profit:
        profit = order - total_g
    else:
        profit = st.number_input("(B) 이익 목표 금액",
                                 value=float(p.get("target_profit") or 0),
                                 step=1000.0, format="%.0f",
                                 disabled=locked, key=k+"b")

    def pr(x):
        return f"{x / order * 100:.0f}%" if order else "-"

    # ---- 집행 내역 표 (첨부 양식: 대분류 > 중분류 > 업체) ----
    vb = p.get("vendor_breakdown") or []
    out_by_vendor = {}
    for x in vb:
        if (x.get("mid") or "") == "외주비":
            v = x.get("vendor") or "-"
            out_by_vendor[v] = out_by_vendor.get(v, 0.0) + float(x.get("amount") or 0)

    GREEN = "background-color:#C6E0B4"
    rows = []

    def add(gubun, dae, jung, amt, ratio_base=None, note="", level=0):
        ratio = (amt / order * 100) if order else 0
        rows.append({
            "구분": gubun, "대분류": dae, "중분류": jung,
            "품의": amt, "비율(%)": f"{ratio:.0f}%" if order else "-",
            "비고": note, "_lv": level,
        })

    # 수주금액
    rows.append({"구분": "", "대분류": "수주금액", "중분류": "",
                 "품의": order, "비율(%)": "100%", "비고": "", "_lv": 9})
    # 직접비
    add("총원가", "원재료", "소계", mat, note="")
    if out_by_vendor:
        for vname2, vamt in out_by_vendor.items():
            add("총원가", "외주비", vname2, vamt)
        # 업체 미지정분
        remain = out - sum(out_by_vendor.values())
        if remain > 0.01:
            add("총원가", "외주비", "기타(미지정)", remain)
    add("총원가", "외주비", "소계", out, note="")
    add("총원가", "직접경비", "소계", dexp, note="")
    add("총원가", "", "(C) 소계", mat + out + dexp, note="", level=8)
    # 간접비
    add("총원가", "간접비(공통비)", "노무비", lab, note="자금수지 수치 반영")
    add("총원가", "간접비(공통비)", "제조간접경비", mfg, note="자금수지 수치 반영")
    add("총원가", "간접비(공통비)", "판관비", sga, note="자금수지 수치 반영")
    add("총원가", "", "(D) 소계", lab + mfg + sga, note="", level=8)
    add("총원가", "", "(E) 총원가 계(C+D)", total_e, note="수주액 대비", level=8)
    if res:
        add("총원가", "", "(F) 예비비", res, level=8)
    add("총원가", "", "(G) 총예정원가", total_g, note="수주액 대비", level=8)
    rows.append({"구분": "", "대분류": "영업이익", "중분류": "",
                 "품의": profit, "비율(%)": pr(profit), "비고": "", "_lv": 9})

    tdf = pd.DataFrame(rows)

    st.markdown(f"**집행 내역** (단위: {cur})")
    # HTML 표로 렌더링 (다크/라이트 무관하게 대비 확실)
    html = ["<table style='width:100%;border-collapse:collapse;"
            "font-size:13px;text-align:center'>"]
    html.append(
        "<tr style='background:#1F4E79;color:#fff;font-weight:700'>"
        "<th style='padding:6px;border:1px solid #ccc'>구분</th>"
        "<th style='padding:6px;border:1px solid #ccc'>대분류</th>"
        "<th style='padding:6px;border:1px solid #ccc'>중분류</th>"
        "<th style='padding:6px;border:1px solid #ccc'>품의</th>"
        "<th style='padding:6px;border:1px solid #ccc'>비율(%)</th>"
        "<th style='padding:6px;border:1px solid #ccc'>비고</th></tr>")
    for r in rows:
        lv = r["_lv"]
        if lv == 9:        # 수주금액/영업이익
            bg, fw = "#A9D08E", "700"
        elif lv == 8:      # 소계
            bg, fw = "#FFE699", "700"
        else:
            bg, fw = "#FFFFFF", "400"
        cells = [r["구분"], r["대분류"], r["중분류"],
                 f"{r['품의']:,.0f}", r["비율(%)"], r["비고"]]
        aligns = ["center", "left", "left", "right", "center", "left"]
        tds = "".join(
            f"<td style='padding:5px 8px;border:1px solid #ccc;"
            f"text-align:{a};color:#000;font-weight:{fw}'>{c}</td>"
            for c, a in zip(cells, aligns))
        html.append(f"<tr style='background:{bg}'>{tds}</tr>")
    html.append("</table>")
    st.markdown("".join(html), unsafe_allow_html=True)

    # 핵심 지표 (간단 요약)
    m = st.columns(5)
    m[0].metric("(A) 수주금액", f"{order:,.0f}")
    m[1].metric("(C) 직접비", f"{mat+out+dexp:,.0f}", pr(mat+out+dexp))
    m[2].metric("(D) 간접비", f"{lab+mfg+sga:,.0f}", pr(lab+mfg+sga))
    m[3].metric("(G) 총예정원가", f"{total_g:,.0f}", pr(total_g))
    m[4].metric("영업이익", f"{profit:,.0f}", pr(profit))

    current = {
        "title": title, "proposal_type": ptype, "currency": currency,
        "project_name": project_name, "project_no": project_no,
        "client": client, "work_name": work_name, "qty": qty,
        "order_amount_note": order_note, "payment_terms": payment_terms,
        "po_info": po_info, "delivery_date": delivery,
        "copy_to": copy_to, "opinion": opinion,
        "order_amount": order, "target_profit": profit,
        "material_cost": mat, "outsourcing_cost": out,
        "direct_expense": dexp, "labor_cost": lab,
        "mfg_overhead": mfg, "sga_cost": sga, "reserve": res,
    }

    st.divider()
    b = st.columns(4)
    if not locked and b[0].button("💾 저장", type="primary"):
        db.table("proposals").update(current).eq("id", pid).execute()
        st.success("저장되었습니다.")
        st.rerun()

    # ---- 워크플로우 ----
    if p["status"] in ("작성중", "거절") and editable:
        if b[1].button("📤 상신 (결재 요청)"):
            db.table("proposals").update(
                {**current, "status": "상신", "result_note": None}
            ).eq("id", pid).execute()
            st.rerun()
    elif p["status"] == "상신":
        if admin:
            if b[1].button("✅ 승인 (관리자)", type="primary"):
                db.table("proposals").update({
                    "status": "승인",
                    "decided_by": st.session_state["user"]["id"],
                    "decided_at": datetime.now().isoformat(),
                }).eq("id", pid).execute()
                st.rerun()
            with b[2].popover("❌ 거절"):
                reason = st.text_input("거절 사유", key=k+"rj")
                if st.button("거절 확정", key=k+"rjb"):
                    db.table("proposals").update({
                        "status": "거절", "result_note": reason,
                        "decided_by": st.session_state["user"]["id"],
                        "decided_at": datetime.now().isoformat(),
                    }).eq("id", pid).execute()
                    st.rerun()
        else:
            st.info("관리자 결재 대기 중입니다.")
    elif p["status"] == "승인":
        umap = {u["id"]: u["name"] for u in
                db.table("app_users").select("id,name").execute().data}
        st.success(f"승인 완료 — {umap.get(p.get('decided_by'), '-')} / "
                   f"{str(p.get('decided_at') or '')[:16].replace('T', ' ')}")

    # ---- 엑셀 출력 (화면의 현재 값 기준) ----
    umap = {u["id"]: u["name"] for u in
            db.table("app_users").select("id,name").execute().data}
    xl = {**p, **current}
    b[3].download_button(
        "⬇️ 품의서 엑셀 출력",
        data=build_proposal_excel(xl, umap.get(p.get("created_by"), "")),
        file_name=helpers.safe_filename(p["doc_no"], p.get("project_name") or p.get("title"), "품의서") + ".xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True)

    # ---- 관리자: 품의서 삭제 (승인 전까지만) ----
    if admin and p["status"] != "승인":
        st.divider()
        with st.popover("🗑️ 품의서 삭제 (관리자)"):
            st.warning(f"'{p['doc_no']} {p['title']}' 품의서가 영구 삭제됩니다.")
            confirm = st.text_input("확인을 위해 문서번호를 입력하세요",
                                    key=k+"del")
            if st.button("삭제 확정", type="primary", key=k+"delb"):
                if confirm.strip() == p["doc_no"]:
                    db.table("proposals").delete().eq("id", pid).execute()
                    st.session_state["prop_open"] = None
                    st.rerun()
                else:
                    st.error("문서번호가 일치하지 않습니다.")
