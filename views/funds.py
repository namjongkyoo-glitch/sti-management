"""자금 현황: 전체 / 프로젝트별 / 계정별 매트릭스 / 협력업체별 지급 현황"""
import pandas as pd
import streamlit as st

import helpers
from db import get_db


def mid_of(acc: dict) -> dict:
    return acc if acc["level"] <= 2 else helpers.account_by_id(acc["parent_id"])


def mid_name(acc_id) -> str:
    acc = helpers.account_by_id(acc_id)
    return mid_of(acc)["name_kr"] if acc else "-"


def render():
    helpers.page_title("자금 현황")
    db = get_db()

    projects = db.table("projects").select("*").execute().data
    txs = db.table("transactions").select("*").execute().data
    reqs = (db.table("expense_requests").select("*")
            .in_("status", ["요청", "승인"]).execute().data)
    budgets = db.table("budget_lines").select("*").execute().data

    prj_map = {p["id"]: p for p in projects}
    tdf = pd.DataFrame(txs) if txs else pd.DataFrame(
        columns=["tx_type", "project_id", "account_id", "vendor_id",
                 "amount", "tx_date", "description"])
    if not tdf.empty:
        tdf["amount"] = tdf["amount"].astype(float)
        # 반환(환불)은 지출의 취소 -> 음수 지출로 변환하여 모든 집계에서 차감
        mask_refund = tdf["tx_type"] == "반환"
        tdf.loc[mask_refund, "amount"] = -tdf.loc[mask_refund, "amount"]
        tdf.loc[mask_refund, "tx_type"] = "지출"

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs(
        ["📊 전체 현황", "📋 자금 요약 검토", "📑 PJT별 수주/손익",
         "🏦 대출 현황", "🏗️ 프로젝트별 현황", "🧮 계정별 집행 매트릭스",
         "🤝 협력업체별 지급 현황"])
    with tab1:
        overall_tab(tdf, reqs, prj_map)
    with tab2:
        summary_review_tab(db, tdf, prj_map)
    with tab3:
        pjt_pl_tab(db, tdf, projects)
    with tab4:
        loan_status_tab(db)
    with tab5:
        project_tab(db, tdf, reqs, projects, budgets)
    with tab6:
        matrix_tab(tdf, projects)
    with tab7:
        vendor_tab(db, tdf, reqs, prj_map)


# ============================================================
# 탭1: 전체 현황
# ============================================================
def overall_tab(tdf, reqs, prj_map):
    income = tdf[tdf["tx_type"] == "수입"]["amount"].sum() if not tdf.empty else 0
    expense = tdf[tdf["tx_type"] == "지출"]["amount"].sum() if not tdf.empty else 0
    pending = sum(float(r["amount"] or 0) for r in reqs)
    bal = income - expense

    st.markdown("##### 💵 자금 요약")
    m = st.columns(4)
    helpers.metric_card(m[0], "총 수입", f"${income:,.0f}", "#22C55E", "📥")
    helpers.metric_card(m[1], "총 지출", f"${expense:,.0f}", "#EF4444", "📤")
    helpers.metric_card(m[2], "수지 (수입-지출)", f"${bal:,.0f}",
                        "#22C55E" if bal >= 0 else "#EF4444", "⚖️")
    helpers.metric_card(m[3], "승인/요청 대기 지출", f"${pending:,.0f}",
                        "#A855F7", "⏳")

    # ---- 통장별 잔액 ----
    bals = helpers.bank_balances_ordered()
    if bals:
        st.markdown("##### 🏦 통장별 잔액 (이체는 수지 미반영)")
        active_bals = [b for b in bals if b["account"]["is_active"]]
        ncol = 4
        for i in range(0, len(active_bals), ncol):
            cols = st.columns(ncol)
            for j, b in enumerate(active_bals[i:i + ncol]):
                amt = b["잔액"]
                helpers.metric_card(
                    cols[j], helpers.bank_label(b["account"]),
                    f"${amt:,.0f}", "#3B82F6" if amt >= 0 else "#EF4444", "💳")
        tot = sum(b["잔액"] for b in active_bals)
        helpers.metric_card(st.columns(4)[0], "통장 합계", f"${tot:,.0f}",
                            "#22C55E" if tot >= 0 else "#EF4444", "Σ")

    if not tdf.empty:
        st.markdown("**월별 수입/지출 추이**")
        mdf = tdf.copy()
        mdf["월"] = mdf["tx_date"].astype(str).str[:7]
        pivot = (mdf.groupby(["월", "tx_type"])["amount"].sum()
                 .unstack(fill_value=0).sort_index())
        for col in ["수입", "지출"]:
            if col not in pivot.columns:
                pivot[col] = 0
        st.bar_chart(pivot[["수입", "지출"]])

        st.markdown("**최근 거래 내역**")
        recent = mdf.sort_values("tx_date", ascending=False).head(20)
        st.dataframe(pd.DataFrame([{
            "일자": r["tx_date"],
            "구분": r["tx_type"],
            "프로젝트": prj_map.get(r.get("project_id"), {}).get("code", "-"),
            "계정": helpers.account_by_id(r["account_id"])["name_kr"]
            if pd.notna(r.get("account_id")) and r.get("account_id") else "-",
            "거래처": helpers.vendor_name_by_id(
                r.get("vendor_id") if pd.notna(r.get("vendor_id")) else None) or "-",
            "금액": f"${float(r['amount']):,.2f}",
            "내용": r.get("description") or "",
        } for _, r in recent.iterrows()]),
            use_container_width=True, hide_index=True)
    else:
        st.info("거래 내역이 없습니다.")


# ============================================================
# 탭2: 프로젝트별 현황
# ============================================================
def project_tab(db, tdf, reqs, projects, budgets):
    rows = []
    for p in projects:
        if p["status"] == "취소":
            continue
        pid = p["id"]
        if not tdf.empty:
            pin = tdf[(tdf["project_id"] == pid) & (tdf["tx_type"] == "수입")]["amount"].sum()
            pout = tdf[(tdf["project_id"] == pid) & (tdf["tx_type"] == "지출")]["amount"].sum()
        else:
            pin = pout = 0
        ppend = sum(float(r["amount"] or 0) for r in reqs
                    if r["project_id"] == pid)
        if p["is_common_pool"]:
            # 공통비 풀 예산 = 전 프로젝트 간접비 예산 합
            pbud = sum(float(b["amount"] or 0) for b in budgets
                       if mid_name(b["account_id"]) in helpers.COST_MIDS_INDIRECT)
        else:
            pbud = sum(float(b["amount"] or 0) for b in budgets
                       if b["project_id"] == pid)
        rows.append({
            "_id": pid,
            "프로젝트": f"{p['code']} {p['name']}",
            "상태": p["status"],
            "계약금액": float(p["contract_amount"] or 0),
            "예산": pbud, "수입": pin, "지출": pout, "대기": ppend,
            "예산 잔여": pbud - pout - ppend,
            "집행률": (pout / pbud * 100) if pbud else 0,
            "수지": pin - pout,
        })
    if not rows:
        st.info("프로젝트가 없습니다.")
        return

    df = pd.DataFrame(rows)
    show = df.drop(columns=["_id"]).copy()
    total = {"프로젝트": "합계", "상태": "", "집행률":
             (df["지출"].sum() / df["예산"].sum() * 100) if df["예산"].sum() else 0}
    for c in ["계약금액", "예산", "수입", "지출", "대기", "예산 잔여", "수지"]:
        total[c] = df[c].sum()
    show = pd.concat([show, pd.DataFrame([total])], ignore_index=True)
    for c in ["계약금액", "예산", "수입", "지출", "대기", "예산 잔여", "수지"]:
        show[c] = show[c].map(lambda x: f"${x:,.0f}")
    show["집행률"] = show["집행률"].map(lambda x: f"{x:.0f}%")
    st.dataframe(show, use_container_width=True, hide_index=True)

    # ---- 프로젝트 상세 (계정별 예산 vs 집행) ----
    st.divider()
    sel = st.selectbox("프로젝트 상세 (계정별 예산 대비 집행)", rows,
                       format_func=lambda r: r["프로젝트"])
    pid = sel["_id"]
    p = next(x for x in projects if x["id"] == pid)

    detail = []
    mids = helpers.COST_MIDS + ["금융비용"]
    for mid in mids:
        if p["is_common_pool"]:
            bud = sum(float(b["amount"] or 0) for b in budgets
                      if mid_name(b["account_id"]) == mid
                      and mid in helpers.COST_MIDS_INDIRECT)
        else:
            bud = sum(float(b["amount"] or 0) for b in budgets
                      if b["project_id"] == pid
                      and mid_name(b["account_id"]) == mid)
        if not tdf.empty:
            spent = tdf[(tdf["project_id"] == pid) & (tdf["tx_type"] == "지출")
                        & (tdf["account_id"].map(
                            lambda a: mid_name(a) == mid if pd.notna(a) else False))
                        ]["amount"].sum()
        else:
            spent = 0
        pend = sum(float(r["amount"] or 0) for r in reqs
                   if r["project_id"] == pid and mid_name(r["account_id"]) == mid)
        if bud or spent or pend:
            detail.append({
                "계정(중분류)": mid, "예산": bud, "지급완료": spent,
                "대기": pend, "잔여": bud - spent - pend,
                "집행률": ((spent + pend) / bud * 100) if bud else 0,
            })
    if detail:
        ddf = pd.DataFrame(detail)
        for c in ["예산", "지급완료", "대기", "잔여"]:
            ddf[c] = ddf[c].map(lambda x: f"${x:,.0f}")
        ddf["집행률"] = ddf["집행률"].map(lambda x: f"{x:.0f}%")
        st.dataframe(ddf, use_container_width=True, hide_index=True)
    else:
        st.info("이 프로젝트의 예산/집행 내역이 없습니다.")


# ============================================================
# 탭3: 계정별 집행 매트릭스 (행=계정, 열=프로젝트)
# ============================================================
def matrix_tab(tdf, projects):
    if tdf.empty or tdf[tdf["tx_type"] == "지출"].empty:
        st.info("지출 내역이 없습니다.")
        return
    out = tdf[tdf["tx_type"] == "지출"].copy()
    prj_map = {p["id"]: p["code"] for p in projects}
    out["프로젝트"] = out["project_id"].map(lambda x: prj_map.get(x, "-"))
    out["계정"] = out["account_id"].map(
        lambda a: mid_name(a) if pd.notna(a) else "-")
    pivot = (out.pivot_table(index="계정", columns="프로젝트",
                             values="amount", aggfunc="sum", fill_value=0))
    pivot["합계"] = pivot.sum(axis=1)
    pivot.loc["합계"] = pivot.sum()
    st.caption("프로젝트별 · 계정(중분류)별 지급 완료 금액")
    st.dataframe(pivot.style.format("${:,.0f}"), use_container_width=True)


# ============================================================
# 탭4: 협력업체별 지급 현황
# ============================================================
def vendor_tab(db, tdf, reqs, prj_map):
    pvs = db.table("project_vendors").select("*").execute().data
    vendors = helpers.load_vendors(active_only=False)
    if not vendors:
        st.info("등록된 협력업체가 없습니다.")
        return

    pay = tdf[(tdf["tx_type"] == "지출") & tdf["vendor_id"].notna()] \
        if not tdf.empty else pd.DataFrame(columns=["vendor_id", "project_id", "amount"])

    rows = []
    for v in vendors:
        vid = v["id"]
        contracts = [pv for pv in pvs if pv["vendor_id"] == vid]
        c_total = sum(float(pv["contract_amount"] or 0) for pv in contracts)
        paid = pay[pay["vendor_id"] == vid]["amount"].sum() if not pay.empty else 0
        pend = sum(float(r["amount"] or 0) for r in reqs
                   if r.get("vendor_id") == vid)
        if not (contracts or paid or pend):
            continue
        rows.append({"v": v, "contracts": contracts, "계약합계": c_total,
                     "지급합계": paid, "대기": pend,
                     "잔여": c_total - paid - pend})

    if not rows:
        st.info("협력업체 계약/지급 내역이 없습니다.")
        return

    sdf = pd.DataFrame([{
        "협력업체": r["v"]["name"], "공종": r["v"].get("trade") or "-",
        "프로젝트 수": len(r["contracts"]),
        "계약합계": r["계약합계"], "지급합계": r["지급합계"],
        "대기": r["대기"], "잔여(계약-지급)": r["잔여"],
    } for r in rows])
    total = {"협력업체": "합계", "공종": "", "프로젝트 수": sdf["프로젝트 수"].sum()}
    for c in ["계약합계", "지급합계", "대기", "잔여(계약-지급)"]:
        total[c] = sdf[c].sum()
    sdf = pd.concat([sdf, pd.DataFrame([total])], ignore_index=True)
    for c in ["계약합계", "지급합계", "대기", "잔여(계약-지급)"]:
        sdf[c] = sdf[c].map(lambda x: f"${x:,.0f}")
    st.dataframe(sdf, use_container_width=True, hide_index=True)

    st.divider()
    sel = st.selectbox("협력업체 상세", rows,
                       format_func=lambda r: r["v"]["name"])
    v = sel["v"]
    st.markdown(f"### {v['name']}  ·  {v.get('trade') or '-'}")

    # 프로젝트별 계약 vs 지급
    prows = []
    vpay = pay[pay["vendor_id"] == v["id"]] if not pay.empty else pay
    pids = {pv["project_id"] for pv in sel["contracts"]}
    if not vpay.empty:
        pids |= set(vpay["project_id"].dropna().tolist())
    for pid in pids:
        prj = prj_map.get(pid, {})
        c_amt = sum(float(pv["contract_amount"] or 0)
                    for pv in sel["contracts"] if pv["project_id"] == pid)
        p_amt = vpay[vpay["project_id"] == pid]["amount"].sum() \
            if not vpay.empty else 0
        prows.append({
            "프로젝트": f"{prj.get('code','-')} {prj.get('name','')}",
            "상태": prj.get("status", "-"),
            "계약금액": f"${c_amt:,.0f}", "지급액": f"${p_amt:,.0f}",
            "잔여": f"${c_amt - p_amt:,.0f}",
        })
    if prows:
        st.markdown("**프로젝트별 현황 (진행 프로젝트 이력)**")
        st.dataframe(pd.DataFrame(prows), use_container_width=True, hide_index=True)

    if not vpay.empty:
        st.markdown("**지급 이력**")
        hist = vpay.sort_values("tx_date", ascending=False)
        st.dataframe(pd.DataFrame([{
            "지급일": r["tx_date"],
            "프로젝트": prj_map.get(r.get("project_id"), {}).get("code", "-"),
            "계정": helpers.account_by_id(r["account_id"])["name_kr"]
            if pd.notna(r.get("account_id")) and r.get("account_id") else "-",
            "금액": f"${float(r['amount']):,.2f}",
            "내용": r.get("description") or "",
        } for _, r in hist.iterrows()]),
            use_container_width=True, hide_index=True)


# ============================================================
# 탭: 자금 요약 검토 (입금/지출/잔액 - 연도별 + 합계)
# ============================================================
def _xlsx_download(df, filename, label):
    from io import BytesIO
    from datetime import date as _date
    buf = BytesIO()
    df.to_excel(buf, index=True)
    if filename.endswith(".xlsx"):
        filename = filename[:-5] + f"_{_date.today()}.xlsx"
    st.download_button(f"⬇️ {label}", data=buf.getvalue(), file_name=filename,
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet")


def summary_review_tab(db, tdf, prj_map):
    loans = db.table("loans").select("*").execute().data
    lpays = db.table("loan_payments").select("*").execute().data

    years = set()
    if not tdf.empty:
        years |= set(tdf["tx_date"].astype(str).str[:4])
    years |= {str(l.get("start_date") or "")[:4] for l in loans if l.get("start_date")}
    years |= {str(p.get("pay_date") or "")[:4] for p in lpays if p.get("pay_date")}
    years = sorted(y for y in years if y and y != "None")
    if not years:
        st.info("거래/대출 데이터가 없습니다.")
        return

    def tx_sum(year, tx_type, cond=None):
        if tdf.empty:
            return 0.0
        m = tdf[(tdf["tx_type"] == tx_type)
                & (tdf["tx_date"].astype(str).str[:4] == year)]
        if cond is not None and not m.empty:
            m = m[m.apply(cond, axis=1).fillna(False).astype(bool)]
        return float(m["amount"].sum())

    def is_direct(r):
        a = r.get("account_id")
        return bool(pd.notna(a) and a
                    and mid_name(a) in helpers.COST_MIDS_DIRECT)

    data = {}
    for y in years:
        pjt_in = tx_sum(y, "수입",
                        lambda r: bool(pd.notna(r.get("project_id"))
                                       and r.get("project_id")))
        etc_in = tx_sum(y, "수입") - pjt_in
        loan_in = sum(float(l["principal"] or 0) for l in loans
                      if str(l.get("start_date") or "")[:4] == y)
        direct_out = tx_sum(y, "지출", is_direct)
        common_out = tx_sum(y, "지출") - direct_out
        repay = sum(float(p["principal_paid"] or 0) for p in lpays
                    if str(p.get("pay_date") or "")[:4] == y)
        data[y] = {
            "입금|PJT 입금(기성 등)": pjt_in,
            "입금|기타 입금(자본금 등)": etc_in,
            "입금|대출 실행": loan_in,
            "입금|소계": pjt_in + etc_in + loan_in,
            "지출|PJT 직접비": direct_out,
            "지출|공통비(간접비·이자)": common_out,
            "지출|대출 상환(원금)": repay,
            "지출|소계": direct_out + common_out + repay,
        }

    rows_order = list(next(iter(data.values())).keys())
    # 누적 잔액 행
    cum = loan_cum = 0.0
    for y in years:
        cum += data[y]["입금|소계"] - data[y]["지출|소계"]
        issued = sum(float(l["principal"] or 0) for l in loans
                     if str(l.get("start_date") or "")[:4] <= y)
        repaid = sum(float(p["principal_paid"] or 0) for p in lpays
                     if str(p.get("pay_date") or "")[:4] <= y)
        data[y]["잔액|예금 잔액(누계)"] = cum
        data[y]["잔액|대출 잔액"] = issued - repaid
        data[y]["수지|총 자금수지"] = data[y]["입금|소계"] - data[y]["지출|소계"]
    rows_order += ["잔액|예금 잔액(누계)", "잔액|대출 잔액", "수지|총 자금수지"]

    # 합계 열
    total = {}
    for k in rows_order:
        if k.startswith("잔액"):
            total[k] = data[years[-1]][k]
        else:
            total[k] = sum(data[y][k] for y in years)

    out = pd.DataFrame({y: {k: data[y][k] for k in rows_order} for y in years})
    out["합계"] = pd.Series(total)
    out.index = [k.replace("|", " · ") for k in out.index]

    st.markdown(f"### 미국법인 자금 요약 검토  (단위: USD)")
    st.dataframe(out.style.format("${:,.0f}").map(
        lambda v: "", subset=None), use_container_width=True, height=460)
    _xlsx_download(out, "자금요약검토.xlsx", "엑셀 다운로드")
    st.caption("· PJT 입금: 프로젝트에 연결된 수입 / 기타 입금: 프로젝트 미지정 수입(자본금 등)  \n"
               "· 대출 실행/상환: 대출 관리 데이터 기준, 이자는 공통비에 포함  \n"
               "· 예금 잔액(누계): 시스템 등록 거래 기준 누적 수지")


# ============================================================
# 탭: PJT별 수주 / 직접비 / 손익
# ============================================================
def pjt_pl_tab(db, tdf, projects):
    budgets = db.table("budget_lines").select("*").execute().data
    prjs = [p for p in projects
            if not p["is_common_pool"] and p["status"] != "취소"]
    if not prjs:
        st.info("수주된 프로젝트가 없습니다.")
        return

    def bsum(pid, mids):
        return sum(float(b["amount"] or 0) for b in budgets
                   if b["project_id"] == pid and mid_name(b["account_id"]) in mids)

    def tsum(pid, tx_type, direct_only=None):
        if tdf.empty:
            return 0.0
        m = tdf[(tdf["project_id"] == pid) & (tdf["tx_type"] == tx_type)]
        if direct_only is True:
            m = m[m["account_id"].map(
                lambda a: bool(pd.notna(a) and a and
                               mid_name(a) in helpers.COST_MIDS_DIRECT))
                  .fillna(False).astype(bool)]
        return float(m["amount"].sum())

    recs = []
    for p in prjs:
        contract = float(p["contract_amount"] or 0)
        received = tsum(p["id"], "수입")
        direct_b = bsum(p["id"], helpers.COST_MIDS_DIRECT)
        direct_paid = tsum(p["id"], "지출", direct_only=True)
        common_b = bsum(p["id"], helpers.COST_MIDS_INDIRECT)
        recs.append({
            "고객사": p.get("client") or "기타",
            "프로젝트": p["name"], "코드": p["code"],
            "수주금액": contract, "입금액": received,
            "미수금": contract - received,
            "직접비(발주)": direct_b,
            "비율": (direct_b / contract * 100) if contract else 0,
            "기지급": direct_paid,
            "미지급": direct_b - direct_paid,
            "공통비": common_b,
            "(예상)손익": contract - direct_b - common_b,
        })

    money = ["수주금액", "입금액", "미수금", "직접비(발주)", "기지급",
             "미지급", "공통비", "(예상)손익"]
    df = pd.DataFrame(recs)

    # 고객사별 그룹 + 소계 + 합계
    out_rows = []
    hl = {}  # 행 index -> 'sub'|'total'
    for client, g in df.groupby("고객사", sort=True):
        first = True
        for _, r in g.iterrows():
            out_rows.append({"고객사": client if first else "",
                             "프로젝트": r["프로젝트"],
                             **{c: r[c] for c in money},
                             "비율": r["비율"]})
            first = False
        if len(g) > 1:
            sub = {"고객사": "", "프로젝트": "— 소계 —",
                   **{c: g[c].sum() for c in money}}
            sub["비율"] = (sub["직접비(발주)"] / sub["수주금액"] * 100) \
                if sub["수주금액"] else 0
            out_rows.append(sub)
            hl[len(out_rows) - 1] = "sub"
    tot = {"고객사": "", "프로젝트": "합계",
           **{c: df[c].sum() for c in money}}
    tot["비율"] = (tot["직접비(발주)"] / tot["수주금액"] * 100) \
        if tot["수주금액"] else 0
    out_rows.append(tot)
    hl[len(out_rows) - 1] = "total"

    raw = pd.DataFrame(out_rows)[
        ["고객사", "프로젝트", "수주금액", "입금액", "미수금",
         "직접비(발주)", "비율", "기지급", "미지급", "공통비", "(예상)손익"]]

    st.markdown("### PJT별 수주 / 직접비 / 손익  (단위: USD)")
    st.caption("직접비(발주) = 직접비 예산 · 기지급 = 직접비 지출완료 · "
               "공통비 = 간접비 예산 · 손익 = 수주 − 직접비 − 공통비")
    cols_order = ["고객사", "프로젝트", "수주금액", "입금액", "미수금",
                  "직접비(발주)", "비율", "기지급", "미지급", "공통비",
                  "(예상)손익"]
    disp_rows = []
    for r in out_rows:
        d = {"고객사": r["고객사"], "프로젝트": r["프로젝트"],
             "비율": f"{r['비율']:.0f}%"}
        for c in money:
            d[c] = f"${r[c]:,.0f}"
        disp_rows.append(d)
    helpers.styled_table(disp_rows, cols_order,
                         money_cols=money + ["비율"], highlight=hl)
    st.write("")
    _xlsx_download(raw.set_index(["고객사", "프로젝트"]),
                   "PJT별_수주손익.xlsx", "엑셀 다운로드")


# ============================================================
# 탭: 대출 현황 (만기일까지 예상 이자 포함)
# ============================================================
def loan_status_tab(db):
    from datetime import date as _date
    loans = db.table("loans").select("*").order("start_date").execute().data
    lpays = db.table("loan_payments").select("*").execute().data
    if not loans:
        st.info("등록된 대출이 없습니다. (대출 관리에서 등록)")
        return

    today = _date.today()
    rows = []
    for l in loans:
        lp = [p for p in lpays if p["loan_id"] == l["id"]]
        repaid = sum(float(p["principal_paid"] or 0) for p in lp)
        paid_int = sum(float(p["interest_paid"] or 0) for p in lp)
        balance = float(l["principal"] or 0) - repaid
        rate = float(l.get("interest_rate") or 0) / 100
        maturity = l.get("maturity_date")
        days_left = 0
        if maturity:
            try:
                days_left = max((
                    _date.fromisoformat(str(maturity)[:10]) - today).days, 0)
            except Exception:
                days_left = 0
        # 잔액 기준 단리 추정
        future_int = balance * rate * days_left / 365 if balance > 0 else 0
        monthly_int = balance * rate / 12 if balance > 0 else 0
        rows.append({
            "기관": l["lender"], "대출명": l.get("loan_name") or "-",
            "이율": rate * 100,
            "실행일": l.get("start_date") or "-",
            "만기일": maturity or "-",
            "잔여일수": days_left,
            "원금": float(l["principal"] or 0),
            "상환누계": repaid, "잔액": balance,
            "이자 지급누계": paid_int,
            "월 이자(추정)": monthly_int,
            "만기까지 잔여이자(추정)": future_int,
            "총 이자(지급+잔여)": paid_int + future_int,
            "상태": l["status"],
        })

    df = pd.DataFrame(rows)
    m = st.columns(5)
    m[0].metric("대출 잔액 합계", f"${df['잔액'].sum():,.0f}")
    m[1].metric("월 이자 부담(추정)", f"${df['월 이자(추정)'].sum():,.0f}")
    m[2].metric("만기까지 잔여이자(추정)", f"${df['만기까지 잔여이자(추정)'].sum():,.0f}")
    m[3].metric("이자 지급 누계", f"${df['이자 지급누계'].sum():,.0f}")
    m[4].metric("총 이자(지급+잔여)", f"${df['총 이자(지급+잔여)'].sum():,.0f}")

    show = df.copy()
    tot = {"기관": "합계", "대출명": "", "이율": None, "실행일": "",
           "만기일": "", "잔여일수": None, "상태": ""}
    for c in ["원금", "상환누계", "잔액", "이자 지급누계", "월 이자(추정)",
              "만기까지 잔여이자(추정)", "총 이자(지급+잔여)"]:
        tot[c] = df[c].sum()
    show = pd.concat([show, pd.DataFrame([tot])], ignore_index=True)
    for c in ["원금", "상환누계", "잔액", "이자 지급누계", "월 이자(추정)",
              "만기까지 잔여이자(추정)", "총 이자(지급+잔여)"]:
        show[c] = show[c].map(lambda x: f"${x:,.0f}" if pd.notna(x) else "")
    show["이율"] = show["이율"].map(
        lambda x: f"{x:.2f}%" if pd.notna(x) else "")
    show["잔여일수"] = show["잔여일수"].map(
        lambda x: f"{int(x)}일" if pd.notna(x) else "")
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.caption("· 잔여이자 추정 = 현재 잔액 × 연이율 × (오늘~만기 일수 / 365), 단리 기준  \n"
               "· 월 이자 추정 = 현재 잔액 × 연이율 ÷ 12  ·  "
               "원금 상환에 따라 실제 이자는 달라질 수 있습니다.")
    _xlsx_download(df.set_index("기관"), "대출현황.xlsx", "엑셀 다운로드")
