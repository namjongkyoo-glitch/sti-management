"""자금 계획(월별 자금수지): 계획 입력 + 실적 자동집계 + 통합 엑셀 출력"""
import pandas as pd
import streamlit as st
from datetime import date

import auth
import helpers
from db import get_db

FIN_ITEMS = ["차입금 발생", "차입금 상환", "현금 배당 지급"]
INV_ITEMS = ["고정자산 매각(입금)", "투자자산 매입(지출)", "고정자산 매입(지출)"]
PJT_EXP = "PJT경비"


# ------------------------------------------------------------
def month_list(start_ym: str, n: int):
    y, m = int(start_ym[:4]), int(start_ym[5:7])
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def ym_label(ym, current_ym):
    lab = f"{ym[2:4]}.{int(ym[5:7])}월"
    return lab if ym <= current_ym else lab + "(E)"


def load_settings(db):
    rows = db.table("cash_plan_settings").select("*").eq("id", 1).execute().data
    if rows:
        return rows[0]
    db.table("cash_plan_settings").insert(
        {"id": 1, "start_ym": str(date.today())[:7]}).execute()
    return {"start_ym": str(date.today())[:7], "months": 12,
            "opening_balance": 0}


def load_plan(db, months):
    rows = (db.table("cash_plans").select("*")
            .in_("ym", months).execute().data)
    d = {}
    for r in rows:
        d[(r["flow"], r.get("project_id"), r["label"], r["ym"])] = \
            float(r["amount"] or 0)
    return d


def save_flow(db, flow, months, rows):
    """rows: [{'project_id':..,'label':..,'vals':{ym:amt}}]"""
    db.table("cash_plans").delete().eq("flow", flow) \
        .in_("ym", months).execute()
    ins = []
    for r in rows:
        for ym, amt in r["vals"].items():
            if float(amt or 0):
                ins.append({"ym": ym, "flow": flow,
                            "project_id": r.get("project_id"),
                            "label": r["label"], "amount": float(amt)})
    if ins:
        db.table("cash_plans").insert(ins).execute()


# ------------------------------------------------------------
# 실적 자동 집계
# ------------------------------------------------------------
def mid_of_name(acc_id):
    acc = helpers.account_by_id(acc_id)
    if not acc:
        return None, None
    if acc["level"] <= 2:
        return acc["name_kr"], acc["name_kr"]
    parent = helpers.account_by_id(acc["parent_id"])
    return parent["name_kr"], acc["name_kr"]


def load_actuals(db, months):
    """월별 실적: 입금(프로젝트), 지출(프로젝트×업체), 공통비(계정), 대출"""
    txs = db.table("transactions").select(
        "tx_type,amount,tx_date,project_id,account_id,vendor_id").execute().data
    income, expense, common = {}, {}, {}
    for t in txs:
        ym = str(t["tx_date"])[:7]
        if ym not in months:
            continue
        amt = float(t["amount"] or 0)
        ttype = t["tx_type"]
        if ttype == "반환":
            amt = -amt  # 지출 차감
            ttype = "지출"
        if ttype == "수입":
            key = (t.get("project_id"), ym)
            income[key] = income.get(key, 0) + amt
        else:
            mid, _ = mid_of_name(t.get("account_id"))
            if mid in helpers.COST_MIDS_DIRECT:
                vname = helpers.vendor_name_by_id(t.get("vendor_id")) or PJT_EXP
                key = (t.get("project_id"), vname, ym)
                expense[key] = expense.get(key, 0) + amt
            else:
                _, detail = mid_of_name(t.get("account_id"))
                key = (detail or "기타", ym)
                common[key] = common.get(key, 0) + amt
    loans = db.table("loans").select("principal,start_date").execute().data
    lpays = db.table("loan_payments").select(
        "principal_paid,pay_date").execute().data
    issue = {}
    for l in loans:
        ym = str(l.get("start_date") or "")[:7]
        if ym in months:
            issue[ym] = issue.get(ym, 0) + float(l["principal"] or 0)
    repay = {}
    for p in lpays:
        ym = str(p.get("pay_date") or "")[:7]
        if ym in months:
            repay[ym] = repay.get(ym, 0) + float(p["principal_paid"] or 0)
    return income, expense, common, issue, repay


def current_loan_balance(db):
    loans = db.table("loans").select("principal").execute().data
    lpays = db.table("loan_payments").select("principal_paid").execute().data
    return (sum(float(l["principal"] or 0) for l in loans)
            - sum(float(p["principal_paid"] or 0) for p in lpays))


# ------------------------------------------------------------
def grid_editor(key, months, current_ym, fixed_cols, rows_df, editable,
                dynamic=False, col_cfg=None):
    cfg = {ym_label(ym, current_ym):
           st.column_config.NumberColumn(ym_label(ym, current_ym),
                                         format="%.0f")
           for ym in months}
    if col_cfg:
        cfg.update(col_cfg)
    return st.data_editor(
        rows_df, use_container_width=True, hide_index=True,
        num_rows="dynamic" if dynamic else "fixed",
        disabled=not editable, key=key, column_config=cfg)


def df_to_rows(edited, months, current_ym, id_resolver):
    rows = []
    for _, r in edited.iterrows():
        pid, label = id_resolver(r)
        if label is None:
            continue
        vals = {ym: float(r.get(ym_label(ym, current_ym)) or 0)
                for ym in months}
        rows.append({"project_id": pid, "label": label, "vals": vals})
    return rows


# ------------------------------------------------------------
def render():
    helpers.page_title("자금 계획 (월별 자금수지)")
    db = get_db()
    editable = auth.can_edit("cashplan")
    current_ym = str(date.today())[:7]

    s = load_settings(db)
    c = st.columns([1.2, 1, 1.4, 1])
    start_ym = c[0].text_input("시작 연월 (YYYY-MM)", value=s["start_ym"])
    n_months = int(c[1].number_input("개월 수", 3, 24, int(s["months"])))
    bank_now = sum(b["잔액"] for b in helpers.bank_balances()) \
        if helpers.load_bank_accounts(active_only=False) else 0.0
    opening = c[2].number_input("기초 시재 (계획 시작 시점)",
                                value=float(s["opening_balance"] or bank_now),
                                step=100.0, format="%.0f")
    if c[3].button("설정 저장"):
        db.table("cash_plan_settings").update({
            "start_ym": start_ym, "months": n_months,
            "opening_balance": opening}).eq("id", 1).execute()
        st.rerun()
    st.caption(f"현재 통장 잔액 합계: ${bank_now:,.0f} · "
               f"{current_ym} 이하 월은 실적(자동집계), 이후 월은 계획값(E)이 사용됩니다.")

    months = month_list(start_ym, n_months)
    plan = load_plan(db, months)
    a_in, a_exp, a_com, a_issue, a_repay = load_actuals(db, months)

    projects = (db.table("projects").select("*")
                .eq("is_common_pool", False).neq("status", "취소")
                .order("code").execute().data)
    prj_label = {p["id"]: f"{p['code']} {p['name']}" for p in projects}
    label_prj = {v: k for k, v in prj_label.items()}

    tabs = st.tabs(["💰 입금 계획(기성)", "💸 지출 계획(업체별)",
                    "🏢 공통비 계획", "🏦 재무/투자 계획", "📊 월별 수지 / 엑셀"])

    mcols = [ym_label(ym, current_ym) for ym in months]

    # ===== 탭1: 입금 계획 =====
    with tabs[0]:
        st.caption("프로젝트별 기성 회수 계획. 지난 달 실적은 '실적' 열 참고 "
                   "(자동집계, 수지 계산 시 지난 달은 실적이 우선 적용).")
        rows = []
        for p in projects:
            row = {"프로젝트": prj_label[p["id"]]}
            for ym in months:
                row[ym_label(ym, current_ym)] = plan.get(
                    ("영업입금", p["id"], "기성", ym), 0.0)
            row["실적합계"] = sum(a_in.get((p["id"], ym), 0) for ym in months)
            rows.append(row)
        row = {"프로젝트": "기타 입금(본사 등)"}
        for ym in months:
            row[ym_label(ym, current_ym)] = plan.get(
                ("영업입금", None, "기타", ym), 0.0)
        row["실적합계"] = sum(a_in.get((None, ym), 0) for ym in months)
        rows.append(row)
        df = pd.DataFrame(rows)
        edited = grid_editor("cp_in", months, current_ym, ["프로젝트"], df,
                             editable,
                             col_cfg={"프로젝트": st.column_config.TextColumn(
                                 "프로젝트", disabled=True),
                                 "실적합계": st.column_config.NumberColumn(
                                 "실적합계($)", disabled=True, format="%.0f")})
        if editable and st.button("💾 입금 계획 저장", key="sv_in"):
            def res(r):
                lab = r["프로젝트"]
                if lab == "기타 입금(본사 등)":
                    return None, "기타"
                return label_prj.get(lab), "기성"
            save_flow(db, "영업입금", months,
                      df_to_rows(edited, months, current_ym, res))
            st.success("저장되었습니다.")
            st.rerun()

    # ===== 탭2: 지출 계획 (프로젝트 × 업체) =====
    with tabs[1]:
        st.caption("프로젝트별·업체별 지출 계획. 업체/항목에 'PJT경비' 등 "
                   "자유 입력 가능. 행 추가로 계속 늘릴 수 있습니다.")
        existing = {}
        for (flow, pid, label, ym), amt in plan.items():
            if flow == "영업지출":
                existing.setdefault((pid, label), {})[ym] = amt
        rows = []
        for (pid, label), vals in sorted(
                existing.items(),
                key=lambda x: (prj_label.get(x[0][0], "zz"), x[0][1])):
            row = {"프로젝트": prj_label.get(pid, ""), "업체/항목": label}
            for ym in months:
                row[ym_label(ym, current_ym)] = vals.get(ym, 0.0)
            row["실적합계"] = sum(a_exp.get((pid, label, ym), 0)
                              for ym in months)
            rows.append(row)
        cols = ["프로젝트", "업체/항목"] + mcols + ["실적합계"]
        df = pd.DataFrame(rows, columns=cols) if rows else \
            pd.DataFrame(columns=cols)
        edited = grid_editor(
            "cp_exp", months, current_ym, ["프로젝트", "업체/항목"], df,
            editable, dynamic=True,
            col_cfg={"프로젝트": st.column_config.SelectboxColumn(
                "프로젝트", options=list(prj_label.values())),
                "업체/항목": st.column_config.TextColumn("업체/항목"),
                "실적합계": st.column_config.NumberColumn(
                "실적합계($)", disabled=True, format="%.0f")})
        if editable and st.button("💾 지출 계획 저장", key="sv_exp"):
            def res(r):
                lab = str(r.get("업체/항목") or "").strip()
                if not lab:
                    return None, None
                return label_prj.get(r.get("프로젝트")), lab
            save_flow(db, "영업지출", months,
                      df_to_rows(edited, months, current_ym, res))
            st.success("저장되었습니다.")
            st.rerun()

    # ===== 탭3: 공통비 계획 =====
    with tabs[2]:
        st.caption("공통비 세부계정별 월 예상 지출. 실적은 자금 집행 데이터에서 자동 집계.")
        details = [a["name_kr"] for a in helpers.load_accounts()
                   if a["level"] == 3 and a.get("is_common")]
        rows = []
        for d in details:
            row = {"계정": d}
            for ym in months:
                row[ym_label(ym, current_ym)] = plan.get(
                    ("공통비", None, d, ym), 0.0)
            row["실적합계"] = sum(a_com.get((d, ym), 0) for ym in months)
            rows.append(row)
        df = pd.DataFrame(rows)
        edited = grid_editor("cp_com", months, current_ym, ["계정"], df,
                             editable,
                             col_cfg={"계정": st.column_config.TextColumn(
                                 "계정", disabled=True),
                                 "실적합계": st.column_config.NumberColumn(
                                 "실적합계($)", disabled=True, format="%.0f")})
        if editable and st.button("💾 공통비 계획 저장", key="sv_com"):
            save_flow(db, "공통비", months,
                      df_to_rows(edited, months, current_ym,
                                 lambda r: (None, r["계정"])))
            st.success("저장되었습니다.")
            st.rerun()

    # ===== 탭4: 재무/투자 계획 =====
    with tabs[3]:
        st.caption("차입금 발생/상환 계획 (실적: 대출 관리 데이터 자동집계), "
                   "고정자산 매입/매각 계획.")
        rows = []
        for it in FIN_ITEMS + INV_ITEMS:
            flow = "재무" if it in FIN_ITEMS else "투자"
            row = {"항목": it}
            for ym in months:
                row[ym_label(ym, current_ym)] = plan.get(
                    (flow, None, it, ym), 0.0)
            if it == "차입금 발생":
                row["실적합계"] = sum(a_issue.get(ym, 0) for ym in months)
            elif it == "차입금 상환":
                row["실적합계"] = sum(a_repay.get(ym, 0) for ym in months)
            else:
                row["실적합계"] = 0.0
            rows.append(row)
        df = pd.DataFrame(rows)
        edited = grid_editor("cp_fin", months, current_ym, ["항목"], df,
                             editable,
                             col_cfg={"항목": st.column_config.TextColumn(
                                 "항목", disabled=True),
                                 "실적합계": st.column_config.NumberColumn(
                                 "실적합계($)", disabled=True, format="%.0f")})
        if editable and st.button("💾 재무/투자 계획 저장", key="sv_fin"):
            fin_rows, inv_rows = [], []
            for _, r in edited.iterrows():
                vals = {ym: float(r.get(ym_label(ym, current_ym)) or 0)
                        for ym in months}
                item = {"project_id": None, "label": r["항목"], "vals": vals}
                (fin_rows if r["항목"] in FIN_ITEMS else inv_rows).append(item)
            save_flow(db, "재무", months, fin_rows)
            save_flow(db, "투자", months, inv_rows)
            st.success("저장되었습니다.")
            st.rerun()

    # ===== 탭5: 월별 수지 + 엑셀 =====
    with tabs[4]:
        monthly_summary(db, months, current_ym, opening, plan,
                        a_in, a_exp, a_com, a_issue, a_repay,
                        projects, prj_label)


# ------------------------------------------------------------
def cell_val(plan, actual_map, key_plan, key_act, ym, current_ym):
    """지난 달은 실적, 미래 달은 계획"""
    if ym <= current_ym:
        return actual_map.get(key_act, 0.0)
    return plan.get(key_plan, 0.0)


def build_grid_data(db, months, current_ym, plan,
                    a_in, a_exp, a_com, a_issue, a_repay,
                    projects, prj_label):
    """엑셀/요약용 데이터 구성"""
    data = {"income": [], "expense": [], "common": [],
            "fin": {}, "inv": {}}
    # 입금
    for p in projects:
        vals = [cell_val(plan, a_in, ("영업입금", p["id"], "기성", ym),
                         (p["id"], ym), ym, current_ym) for ym in months]
        if any(vals):
            data["income"].append({"label": prj_label[p["id"]], "vals": vals})
    etc = [cell_val(plan, a_in, ("영업입금", None, "기타", ym),
                    (None, ym), ym, current_ym) for ym in months]
    if any(etc):
        data["income"].append({"label": "기타 입금(본사 등)", "vals": etc})
    # 지출 (계획행 + 실적행 합집합)
    keys = set()
    for (flow, pid, label, ym) in plan:
        if flow == "영업지출":
            keys.add((pid, label))
    for (pid, vname, ym) in a_exp:
        keys.add((pid, vname))
    for pid, label in sorted(keys, key=lambda x:
                             (prj_label.get(x[0], "zz"), x[1])):
        vals = [cell_val(plan, a_exp, ("영업지출", pid, label, ym),
                         (pid, label, ym), ym, current_ym) for ym in months]
        if any(vals):
            data["expense"].append({
                "prj": prj_label.get(pid, "-"), "label": label, "vals": vals})
    # 공통비
    details = [a["name_kr"] for a in helpers.load_accounts()
               if a["level"] == 3 and a.get("is_common")]
    for d in details:
        vals = [cell_val(plan, a_com, ("공통비", None, d, ym),
                         (d, ym), ym, current_ym) for ym in months]
        if any(vals):
            data["common"].append({"label": d, "vals": vals})
    # 재무/투자
    for it in FIN_ITEMS:
        amap = a_issue if it == "차입금 발생" else \
            (a_repay if it == "차입금 상환" else {})
        data["fin"][it] = [
            (amap.get(ym, 0.0) if ym <= current_ym
             else plan.get(("재무", None, it, ym), 0.0)) for ym in months]
    for it in INV_ITEMS:
        data["inv"][it] = [plan.get(("투자", None, it, ym), 0.0)
                           for ym in months]
    return data


def monthly_summary(db, months, current_ym, opening, plan,
                    a_in, a_exp, a_com, a_issue, a_repay,
                    projects, prj_label):
    data = build_grid_data(db, months, current_ym, plan,
                           a_in, a_exp, a_com, a_issue, a_repay,
                           projects, prj_label)
    n = len(months)
    inc = [sum(r["vals"][i] for r in data["income"]) for i in range(n)]
    exp = [sum(r["vals"][i] for r in data["expense"]) for i in range(n)]
    com = [sum(r["vals"][i] for r in data["common"]) for i in range(n)]
    fin = [data["fin"]["차입금 발생"][i] - data["fin"]["차입금 상환"][i]
           - data["fin"]["현금 배당 지급"][i] for i in range(n)]
    inv = [data["inv"][INV_ITEMS[0]][i] - data["inv"][INV_ITEMS[1]][i]
           - data["inv"][INV_ITEMS[2]][i] for i in range(n)]

    base, rows = [], []
    bal = opening
    for i in range(n):
        base.append(bal)
        net = inc[i] - exp[i] - com[i] + fin[i] + inv[i]
        bal = bal + net
        rows.append(net)
    sdf = pd.DataFrame({
        "구분": ["기초 시재", "영업 입금", "매입대(직접비)", "공통비",
               "재무활동 수지", "투자활동 수지", "월 수지", "기말 잔액"],
        **{ym_label(months[i], current_ym):
           [base[i], inc[i], exp[i], com[i], fin[i], inv[i], rows[i],
            base[i] + rows[i]] for i in range(n)},
    })
    show = sdf.copy()
    for c in show.columns[1:]:
        show[c] = show[c].map(lambda x: f"{x:,.0f}")
    st.markdown("**월별 자금수지 요약** (지난 달=실적 / 미래 달=계획)")
    st.dataframe(show, use_container_width=True, hide_index=True)

    # ---- 통합 엑셀 ----
    from cashplan_excel import build_cashplan_excel
    summary = build_summary_data(db)
    xls = build_cashplan_excel(months, current_ym, opening, data,
                               current_loan_balance(db), summary)
    st.download_button(
        "⬇️ 자금수지 통합 엑셀 다운로드 (요약 + 월별 자금수지)",
        data=xls, file_name=f"자금수지_통합_{date.today()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary")


def build_summary_data(db):
    """요약 시트용: 자금 요약 + PJT별 손익 (전체 실적 기준)"""
    txs = db.table("transactions").select(
        "tx_type,amount,project_id,account_id").execute().data
    budgets = db.table("budget_lines").select("*").execute().data
    projects = (db.table("projects").select("*")
                .eq("is_common_pool", False).neq("status", "취소")
                .execute().data)
    loans = db.table("loans").select("principal").execute().data
    lpays = db.table("loan_payments").select("principal_paid").execute().data

    pjt_in = etc_in = direct_out = common_out = 0.0
    for t in txs:
        amt = float(t["amount"] or 0)
        if t["tx_type"] == "수입":
            if t.get("project_id"):
                pjt_in += amt
            else:
                etc_in += amt
        else:
            mid, _ = mid_of_name(t.get("account_id"))
            if mid in helpers.COST_MIDS_DIRECT:
                direct_out += amt
            else:
                common_out += amt
    loan_issue = sum(float(l["principal"] or 0) for l in loans)
    loan_repay = sum(float(p["principal_paid"] or 0) for p in lpays)
    bank = sum(b["잔액"] for b in helpers.bank_balances()) \
        if helpers.load_bank_accounts(active_only=False) else 0.0

    def mname(aid):
        m, _ = mid_of_name(aid)
        return m

    pjt_rows = []
    for p in projects:
        contract = float(p["contract_amount"] or 0)
        received = sum(float(t["amount"] or 0) for t in txs
                       if t["tx_type"] == "수입"
                       and t.get("project_id") == p["id"])
        d_budget = sum(float(b["amount"] or 0) for b in budgets
                       if b["project_id"] == p["id"]
                       and mname(b["account_id"]) in helpers.COST_MIDS_DIRECT)
        d_paid = sum(float(t["amount"] or 0) for t in txs
                     if t["tx_type"] == "지출"
                     and t.get("project_id") == p["id"]
                     and mname(t.get("account_id")) in helpers.COST_MIDS_DIRECT)
        c_budget = sum(float(b["amount"] or 0) for b in budgets
                       if b["project_id"] == p["id"]
                       and mname(b["account_id"]) in helpers.COST_MIDS_INDIRECT)
        pjt_rows.append({
            "client": p.get("client") or "기타", "name": p["name"],
            "contract": contract, "received": received,
            "receivable": contract - received,
            "direct_budget": d_budget, "direct_paid": d_paid,
            "direct_unpaid": d_budget - d_paid, "common": c_budget,
            "profit": contract - d_budget - c_budget,
        })
    return {
        "pjt_in": pjt_in, "etc_in": etc_in, "loan_issue": loan_issue,
        "direct_out": direct_out, "common_out": common_out,
        "loan_repay": loan_repay, "bank": bank,
        "loan_balance": loan_issue - loan_repay, "pjt_rows": pjt_rows,
    }
