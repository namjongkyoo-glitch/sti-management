"""DB 백업/복원 + 소스코드 백업 유틸 (Admin 전용)"""
import json
import io
import os
import zipfile
from datetime import datetime

# 백업 대상 테이블 (의존성 순서 — 복원 시 이 순서로 insert)
BACKUP_TABLES = [
    "accounts", "pages", "app_users", "page_permissions",
    "clients", "vendors", "trades", "bank_accounts",
    "estimates", "estimate_versions", "estimate_lines",
    "estimate_sheets", "estimate_sheet_items", "estimate_changes",
    "proposals",
    "projects", "budget_lines", "budget_changes", "project_vendors",
    "schedule_items",
    "purchase_orders", "po_lines",
    "invoices", "invoice_lines",
    "expense_requests", "transactions", "bank_transfers",
    "loans", "loan_payments",
    "employees", "payroll_months", "payroll_lines",
    "cash_plans", "cash_plan_settings",
]


def fetch_all(db):
    """모든 테이블 데이터를 dict로 반환 (테이블 없으면 스킵)"""
    data = {}
    for t in BACKUP_TABLES:
        try:
            rows = db.table(t).select("*").execute().data
            data[t] = rows or []
        except Exception:
            data[t] = []  # 테이블 없으면 빈 값
    return data


def backup_json(db) -> bytes:
    data = fetch_all(db)
    payload = {
        "_meta": {
            "backup_at": datetime.now().isoformat(),
            "app": "STI 경영관리",
            "table_count": len(data),
            "row_counts": {t: len(rows) for t, rows in data.items()},
        },
        "data": data,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2,
                      default=str).encode("utf-8")


def backup_excel(db) -> bytes:
    import pandas as pd
    data = fetch_all(db)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        # 요약 시트
        summary = pd.DataFrame(
            [{"테이블": t, "행 수": len(rows)} for t, rows in data.items()])
        summary.to_excel(writer, sheet_name="_요약", index=False)
        for t, rows in data.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            # 시트명 31자 제한
            sheet = t[:31]
            df.to_excel(writer, sheet_name=sheet, index=False)
    return buf.getvalue()


def restore_json(db, payload_bytes: bytes, mode: str = "merge") -> dict:
    """
    JSON 백업으로 복원.
    mode='merge': 기존 유지하고 없는 것만 추가(upsert by id)
    mode='replace': 해당 테이블 비우고 전체 교체 (역순 삭제)
    """
    payload = json.loads(payload_bytes.decode("utf-8"))
    data = payload.get("data", payload)  # data 키 없으면 그대로
    result = {}

    if mode == "replace":
        # FK 역순으로 삭제
        for t in reversed(BACKUP_TABLES):
            if t in data:
                try:
                    db.table(t).delete().neq("id", -1).execute()
                except Exception as e:
                    result[t + "_delete"] = f"스킵: {e}"

    for t in BACKUP_TABLES:
        rows = data.get(t)
        if not rows:
            continue
        ok = 0
        try:
            # 배치 upsert
            db.table(t).upsert(rows).execute()
            ok = len(rows)
        except Exception:
            # 실패 시 행 단위 시도
            for r in rows:
                try:
                    db.table(t).upsert(r).execute()
                    ok += 1
                except Exception:
                    pass
        result[t] = f"{ok}/{len(rows)}행"
    return result


def backup_source(app_dir: str) -> bytes:
    """app_dir(사용자 PC의 sti_app 폴더)의 소스를 zip으로 묶음"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(app_dir):
            # 캐시/숨김 폴더 제외
            dirs[:] = [d for d in dirs
                       if d not in ("__pycache__", ".git", ".streamlit")]
            for f in files:
                if f.endswith((".pyc",)):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, app_dir)
                try:
                    zf.write(full, rel)
                except Exception:
                    pass
    return buf.getvalue()
