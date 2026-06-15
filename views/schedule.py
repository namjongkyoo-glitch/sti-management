"""프로젝트 스케줄 관리: 계획 대비 실적 간트차트 + 회사 양식 엑셀 출력"""
import altair as alt
import pandas as pd
import streamlit as st
from datetime import date

import auth
import helpers
from db import get_db
from excel_reports import build_schedule_excel


def render():
    helpers.page_title("프로젝트 스케줄 관리")
    db = get_db()
    editable = auth.can_edit("schedule")

    prjs = (db.table("projects").select("*")
            .eq("is_common_pool", False).neq("status", "취소")
            .order("code").execute().data)
    if not prjs:
        st.info("수주된 프로젝트가 없습니다.")
        return
    prj = st.selectbox("프로젝트", prjs,
                       format_func=lambda p: f"{p['code']} {p['name']} ({p['status']})")

    items = (db.table("schedule_items").select("*")
             .eq("project_id", prj["id"]).order("sort_order").execute().data)

    # ---- 입력 표 ----
    df = pd.DataFrame([{
        "공정명": i["task_name"], "구분": i.get("category") or "",
        "담당": i.get("owner") or "",
        "계획 시작": pd.to_datetime(i["plan_start"]).date() if i.get("plan_start") else None,
        "계획 종료": pd.to_datetime(i["plan_end"]).date() if i.get("plan_end") else None,
        "실적 시작": pd.to_datetime(i["actual_start"]).date() if i.get("actual_start") else None,
        "실적 종료": pd.to_datetime(i["actual_end"]).date() if i.get("actual_end") else None,
        "진행률": float(i.get("progress") or 0),
        "비고": i.get("notes") or "",
    } for i in items]) if items else pd.DataFrame(
        columns=["공정명", "구분", "담당", "계획 시작", "계획 종료",
                 "실적 시작", "실적 종료", "진행률", "비고"])

    st.markdown("**공정 입력** (실적 종료가 비어 있으면 진행 중으로 간주합니다)")
    edited = st.data_editor(
        df, num_rows="dynamic", use_container_width=True, hide_index=True,
        disabled=not editable, key=f"sch_{prj['id']}",
        column_config={
            "공정명": st.column_config.TextColumn("공정명", required=True),
            "계획 시작": st.column_config.DateColumn("계획 시작"),
            "계획 종료": st.column_config.DateColumn("계획 종료"),
            "실적 시작": st.column_config.DateColumn("실적 시작"),
            "실적 종료": st.column_config.DateColumn("실적 종료"),
            "진행률": st.column_config.NumberColumn(
                "진행률(%)", min_value=0, max_value=100, format="%.0f"),
        })

    if editable and st.button("💾 스케줄 저장", type="primary"):
        db.table("schedule_items").delete().eq("project_id", prj["id"]).execute()
        rows = []
        for i, r in edited.iterrows():
            if not r.get("공정명") or pd.isna(r.get("공정명")):
                continue
            def d(v):
                return str(v)[:10] if (v is not None and not pd.isna(v)) else None
            rows.append({
                "project_id": prj["id"], "task_name": str(r["공정명"]),
                "category": str(r.get("구분") or ""),
                "owner": str(r.get("담당") or ""),
                "plan_start": d(r.get("계획 시작")),
                "plan_end": d(r.get("계획 종료")),
                "actual_start": d(r.get("실적 시작")),
                "actual_end": d(r.get("실적 종료")),
                "progress": float(r.get("진행률") or 0),
                "notes": str(r.get("비고") or ""), "sort_order": i,
            })
        if rows:
            db.table("schedule_items").insert(rows).execute()
        st.success("저장되었습니다.")
        st.rerun()

    # ---- 간트차트 (계획 vs 실적) ----
    bars = []
    order = []
    for _, r in edited.iterrows():
        name = r.get("공정명")
        if not name or pd.isna(name):
            continue
        order.append(name)
        if pd.notna(r.get("계획 시작")) and pd.notna(r.get("계획 종료")):
            bars.append({"공정명": name, "유형": "계획",
                         "시작": pd.to_datetime(r["계획 시작"]),
                         "종료": pd.to_datetime(r["계획 종료"]),
                         "진행률": f"{float(r.get('진행률') or 0):.0f}%"})
        if pd.notna(r.get("실적 시작")):
            a_end = r["실적 종료"] if pd.notna(r.get("실적 종료")) else date.today()
            bars.append({"공정명": name, "유형": "실적",
                         "시작": pd.to_datetime(r["실적 시작"]),
                         "종료": pd.to_datetime(a_end),
                         "진행률": f"{float(r.get('진행률') or 0):.0f}%"})
    if bars:
        st.markdown("**간트차트 — 계획(파랑) 대비 실적(초록)**")
        bdf = pd.DataFrame(bars)
        plan = (alt.Chart(bdf[bdf["유형"] == "계획"])
                .mark_bar(height=18, color="#9DC3E6", cornerRadius=2)
                .encode(y=alt.Y("공정명:N", sort=order, title=None),
                        x=alt.X("시작:T", title=None), x2="종료:T",
                        tooltip=["공정명", "유형", "시작", "종료", "진행률"]))
        actual = (alt.Chart(bdf[bdf["유형"] == "실적"])
                  .mark_bar(height=7, color="#548235", cornerRadius=2)
                  .encode(y=alt.Y("공정명:N", sort=order, title=None),
                          x="시작:T", x2="종료:T",
                          tooltip=["공정명", "유형", "시작", "종료", "진행률"]))
        today_rule = (alt.Chart(pd.DataFrame({"d": [pd.Timestamp(date.today())]}))
                      .mark_rule(color="red", strokeDash=[4, 3])
                      .encode(x="d:T"))
        st.altair_chart((plan + actual + today_rule)
                        .properties(height=max(120, 34 * len(order))),
                        use_container_width=True)
    else:
        st.info("계획/실적 날짜를 입력하면 간트차트가 표시됩니다.")

    # ---- 엑셀 출력 ----
    xl_items = []
    for _, r in edited.iterrows():
        if not r.get("공정명") or pd.isna(r.get("공정명")):
            continue
        def dv(v):
            return None if (v is None or pd.isna(v)) else str(v)[:10]
        xl_items.append({
            "task_name": r["공정명"], "category": r.get("구분") or "",
            "owner": r.get("담당") or "",
            "plan_start": dv(r.get("계획 시작")), "plan_end": dv(r.get("계획 종료")),
            "actual_start": dv(r.get("실적 시작")), "actual_end": dv(r.get("실적 종료")),
            "progress": float(r.get("진행률") or 0), "notes": r.get("비고") or "",
        })
    if xl_items:
        st.download_button(
            "⬇️ 스케줄 엑셀 출력 (회사 양식, 주단위 간트)",
            data=build_schedule_excel(prj, xl_items),
            file_name=helpers.safe_filename(prj["code"], prj["name"], "Schedule") + ".xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
