"""대시보드: 회사 전체 현황 (자금/통장/월별 수입지출은 Admin 전용)"""
import os
import pandas as pd
import streamlit as st
from datetime import date

import auth
import helpers
from db import get_db


def render():
    user = st.session_state.get("user", {})
    admin = auth.is_admin()
    _logo = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "sti_logo.png")
    cphd = st.columns([1, 9])
    if os.path.exists(_logo):
        cphd[0].image(_logo, width=55)
    cphd[1].title("대시보드")
    st.caption(f"{date.today()} · {user.get('name','')}님, 안녕하세요!")
    db = get_db()

    projects = db.table("projects").select("*").execute().data
    ests = db.table("estimates").select("status").execute().data
    pend_reqs = (db.table("expense_requests").select("amount")
                 .eq("status", "요청").execute().data)

    active_prj = [p for p in projects
                  if not p["is_common_pool"] and p["status"] == "진행중"]
    contract_sum = sum(float(p["contract_amount"] or 0) for p in active_prj)
    pending_est = sum(1 for e in ests if e["status"] == "제출")
    pend_amt = sum(float(r["amount"] or 0) for r in pend_reqs)

    # ---- 1행: 프로젝트/견적 (전체 공개) ----
    def metric_card(col, label, value, color, icon=""):
        col.markdown(
            f"<div style='background:linear-gradient(135deg,{color}22,{color}08);"
            f"border:1px solid {color}55;border-radius:12px;padding:14px 16px;'>"
            f"<div style='font-size:12px;color:#9aa4b2;margin-bottom:4px'>"
            f"{icon} {label}</div>"
            f"<div style='font-size:24px;font-weight:800;color:#fff'>{value}</div>"
            f"</div>", unsafe_allow_html=True)

    st.markdown("##### 📊 핵심 현황")
    c = st.columns(4)
    metric_card(c[0], "진행 중 프로젝트", f"{len(active_prj)}건", "#22C55E", "🏗️")
    metric_card(c[1], "진행 수주총액", f"${contract_sum:,.0f}", "#3B82F6", "💰")
    metric_card(c[2], "수주 대기 견적", f"{pending_est}건", "#F59E0B", "📋")
    metric_card(c[3], "승인 대기 지출",
                f"{len(pend_reqs)}건 · ${pend_amt:,.0f}", "#A855F7", "✅")
    st.write("")

    # ---- 진행 중 견적 현황 (전체 공개) ----
    open_ests = (db.table("estimates").select("*")
                 .in_("status", ["작성중", "승인대기", "승인", "제출"])
                 .order("created_at", desc=True).execute().data)
    if open_ests:
        st.divider()
        st.markdown("##### 📋 진행 중 견적 현황")
        EST_BADGE = {"작성중": "🟡 작성중", "승인대기": "🟠 승인대기",
                     "승인": "🟢 승인", "제출": "🔵 제출(수주대기)"}
        ids = [e["id"] for e in open_ests]
        vers = (db.table("estimate_versions")
                .select("estimate_id,version_no,version_label,order_amount")
                .in_("estimate_id", ids).execute().data)
        latest = {}
        for v in vers:
            cur = latest.get(v["estimate_id"])
            if cur is None or v["version_no"] > cur["version_no"]:
                latest[v["estimate_id"]] = v
        edf = pd.DataFrame([{
            "견적번호": e["estimate_no"],
            "견적명": e["title"],
            "고객사": e.get("client") or "-",
            "버전": latest.get(e["id"], {}).get("version_label", "-"),
            "수주금액": f"${float(latest.get(e['id'], {}).get('order_amount') or 0):,.0f}",
            "상태": EST_BADGE.get(e["status"], e["status"]),
        } for e in open_ests])
        st.dataframe(edf, use_container_width=True, hide_index=True,
                     height=min(300, 60 + 36 * len(edf)))

    # ---- 자금 데이터 (Admin 전용으로만 조회/표시) ----
    tdf = pd.DataFrame(columns=["tx_type", "amount", "tx_date",
                                "description", "project_id"])
    if admin:
        txs = db.table("transactions").select(
            "tx_type,amount,tx_date,description,project_id").execute().data
        if txs:
            tdf = pd.DataFrame(txs)
            tdf["amount"] = tdf["amount"].astype(float)
            _rf = tdf["tx_type"] == "반환"
            tdf.loc[_rf, "amount"] = -tdf.loc[_rf, "amount"]
            tdf.loc[_rf, "tx_type"] = "지출"

        loans = db.table("loans").select("principal").execute().data
        lpays = db.table("loan_payments").select("principal_paid").execute().data
        loan_balance = (sum(float(l["principal"] or 0) for l in loans)
                        - sum(float(p["principal_paid"] or 0) for p in lpays))

        this_month = str(date.today())[:7]
        if not tdf.empty:
            mdf = tdf[tdf["tx_date"].astype(str).str[:7] == this_month]
            m_in = mdf[mdf["tx_type"] == "수입"]["amount"].sum()
            m_out = mdf[mdf["tx_type"] == "지출"]["amount"].sum()
            t_in = tdf[tdf["tx_type"] == "수입"]["amount"].sum()
            t_out = tdf[tdf["tx_type"] == "지출"]["amount"].sum()
        else:
            m_in = m_out = t_in = t_out = 0

        # 2행: 자금 (Admin)
        st.divider()
        st.markdown("##### 💵 자금 현황")
        bal = t_in - t_out
        c = st.columns(4)
        metric_card(c[0], f"{this_month} 수입", f"${m_in:,.0f}", "#22C55E", "📥")
        metric_card(c[1], f"{this_month} 지출", f"${m_out:,.0f}", "#EF4444", "📤")
        metric_card(c[2], "누적 수지", f"${bal:,.0f}",
                    "#22C55E" if bal >= 0 else "#EF4444", "⚖️")
        metric_card(c[3], "대출 잔액", f"${loan_balance:,.0f}", "#F59E0B", "🏦")
        st.write("")

        # 통장별 잔액 (Admin)
        bals = helpers.bank_balances_ordered()
        if bals:
            st.markdown("##### 🏦 통장별 잔액")
            active_bals = [b for b in bals if b["account"]["is_active"]]
            ncol = 4
            rows_b = [active_bals[i:i + ncol]
                      for i in range(0, len(active_bals), ncol)]
            for rb in rows_b:
                cols = st.columns(ncol)
                for i, b in enumerate(rb):
                    amt = b["잔액"]
                    color = "#3B82F6" if amt >= 0 else "#EF4444"
                    metric_card(cols[i], helpers.bank_label(b["account"]),
                                f"${amt:,.0f}", color, "💳")
            total_bal = sum(b["잔액"] for b in active_bals)
            metric_card(st.columns(4)[0], "통장 합계", f"${total_bal:,.0f}",
                        "#22C55E" if total_bal >= 0 else "#EF4444", "Σ")

    # ---- 진행 중 프로젝트: 스케줄 (+자금: Admin만) ----
    if active_prj:
        st.divider()
        st.markdown("**🏗️ 진행 중 프로젝트 — 스케줄"
                    + (" / 자금 집행 요약**" if admin else " 요약**"))
        schedules = db.table("schedule_items").select(
            "project_id,plan_end,progress").execute().data
        budgets = (db.table("budget_lines").select("project_id,amount")
                   .execute().data) if admin else []
        today_s = str(date.today())

        for p in active_prj:
            sch = [s for s in schedules if s["project_id"] == p["id"]]
            n_sch = len(sch)
            avg_prog = (sum(float(s["progress"] or 0) for s in sch) / n_sch) \
                if n_sch else 0
            delayed = sum(1 for s in sch
                          if s.get("plan_end") and str(s["plan_end"]) < today_s
                          and float(s["progress"] or 0) < 100)
            with st.container(border=True):
                h = st.columns([3, 3.5, 3.5] if admin else [3, 7])
                h[0].markdown(f"**{p['code']} {p['name']}**  \n"
                              f"{p.get('client') or '-'}"
                              + (f" · 계약 ${float(p['contract_amount'] or 0):,.0f}"
                                 if admin else ""))
                with h[1]:
                    st.caption(f"📅 스케줄 — 공정 {n_sch}개 · "
                               f"평균 진행률 {avg_prog:.0f}%"
                               + (f" · 🔥지연 {delayed}건" if delayed else ""))
                    st.progress(min(avg_prog / 100, 1.0))
                if admin:
                    budget = sum(float(b["amount"] or 0) for b in budgets
                                 if b["project_id"] == p["id"])
                    if not tdf.empty:
                        spent = tdf[(tdf["project_id"] == p["id"])
                                    & (tdf["tx_type"] == "지출")]["amount"].sum()
                        received = tdf[(tdf["project_id"] == p["id"])
                                       & (tdf["tx_type"] == "수입")]["amount"].sum()
                    else:
                        spent = received = 0
                    contract = float(p["contract_amount"] or 0)
                    rate = (spent / budget) if budget else 0
                    with h[2]:
                        st.caption(f"💸 자금 — 예산 ${budget:,.0f} · "
                                   f"지출 ${spent:,.0f} ({rate*100:.0f}%) · "
                                   f"입금 ${received:,.0f} / "
                                   f"미수 ${contract - received:,.0f}")
                        st.progress(min(rate, 1.0))

    # ---- 최근 거래 + 월별 추이 (Admin 전용) ----
    if admin and not tdf.empty:
        st.divider()
        st.markdown("**💳 최근 거래**")
        prj_map = {p["id"]: p["code"] for p in projects}
        recent = tdf.sort_values("tx_date", ascending=False).head(8)
        for _, r in recent.iterrows():
            sign = "🔵 +" if r["tx_type"] == "수입" else "🔴 -"
            st.write(f"{sign}${float(r['amount']):,.0f} · "
                     f"{prj_map.get(r.get('project_id'), '-')} · "
                     f"{r['tx_date']} · {r.get('description') or ''}")

        st.divider()
        st.markdown("**📈 월별 수입/지출 추이**")
        mm = tdf.copy()
        mm["월"] = mm["tx_date"].astype(str).str[:7]
        pivot = (mm.groupby(["월", "tx_type"])["amount"].sum()
                 .unstack(fill_value=0).sort_index())
        for col in ["수입", "지출"]:
            if col not in pivot.columns:
                pivot[col] = 0
        st.bar_chart(pivot[["수입", "지출"]])
