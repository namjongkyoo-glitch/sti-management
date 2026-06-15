"""로그인 / 인증 / 권한 모듈"""
import bcrypt
import streamlit as st
from db import get_db


# ----------------------------------------------------------
# 비밀번호 해시
# ----------------------------------------------------------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def check_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ----------------------------------------------------------
# 사용자
# ----------------------------------------------------------
def count_users() -> int:
    db = get_db()
    res = db.table("app_users").select("id", count="exact").execute()
    return res.count or 0


def create_user(login_id: str, name: str, password: str,
                role: str = "user", email: str = "") -> dict:
    db = get_db()
    res = db.table("app_users").insert({
        "login_id": login_id.strip(),
        "name": name.strip(),
        "email": email.strip(),
        "role": role,
        "password_hash": hash_password(password),
    }).execute()
    return res.data[0]


def login(login_id: str, password: str):
    """성공 시 사용자 dict, 실패 시 None"""
    db = get_db()
    res = (db.table("app_users").select("*")
           .eq("login_id", login_id.strip())
           .eq("is_active", True)
           .execute())
    if not res.data:
        return None
    user = res.data[0]
    if not user.get("password_hash"):
        return None
    if check_password(password, user["password_hash"]):
        user.pop("password_hash", None)
        return user
    return None


def reset_password(user_id: str, new_password: str):
    db = get_db()
    db.table("app_users").update(
        {"password_hash": hash_password(new_password)}
    ).eq("id", user_id).execute()


# ----------------------------------------------------------
# 권한
# ----------------------------------------------------------
# Admin/Account 전용 페이지 (일반 사용자는 권한 매트릭스와 무관하게 접근 불가)
RESTRICTED_PAGES = {"expenses", "funds", "cashplan", "employees", "payroll"}
ADMIN_ONLY_PAGES = {"admin"}

ROLE_LABEL = {"admin": "Admin", "account": "Account", "viewer": "관찰자", "user": "일반"}


def get_all_pages() -> list[dict]:
    db = get_db()
    return (db.table("pages").select("*")
            .order("sort_order").execute().data)


def get_user_permissions(user: dict) -> dict:
    """{page_code: {'can_view':bool,'can_edit':bool}}
    - admin   : 전체 페이지
    - account : Admin 페이지 제외 전체 (자금/직원/급여 포함)
    - user    : 권한 매트릭스 적용, 단 자금집행/자금현황/직원/급여/Admin은 항상 불가
    """
    pages = get_all_pages()
    role = user.get("role", "user")
    if role == "admin":
        return {p["code"]: {"can_view": True, "can_edit": True} for p in pages}
    if role == "account":
        return {p["code"]: {"can_view": True, "can_edit": True}
                for p in pages if p["code"] not in ADMIN_ONLY_PAGES}
    if role == "viewer":
        # 관찰자: 전체 조회 가능(Admin 설정 제외), 입력/수정/삭제 불가
        return {p["code"]: {"can_view": True, "can_edit": False}
                for p in pages if p["code"] not in ADMIN_ONLY_PAGES}
    # 일반 사용자
    db = get_db()
    rows = (db.table("page_permissions").select("*")
            .eq("user_id", user["id"]).execute().data)
    blocked = RESTRICTED_PAGES | ADMIN_ONLY_PAGES
    perms = {r["page_code"]: {"can_view": r["can_view"],
                              "can_edit": r["can_edit"]}
             for r in rows if r["page_code"] not in blocked}
    return perms


def can_view(page_code: str) -> bool:
    perms = st.session_state.get("perms", {})
    return perms.get(page_code, {}).get("can_view", False)


def can_edit(page_code: str) -> bool:
    perms = st.session_state.get("perms", {})
    return perms.get(page_code, {}).get("can_edit", False)


def is_admin() -> bool:
    user = st.session_state.get("user")
    return bool(user and user["role"] == "admin")


def is_viewer() -> bool:
    user = st.session_state.get("user")
    return bool(user and user.get("role") == "viewer")


# ----------------------------------------------------------
# 2단계 인증 (TOTP - MS Authenticator / Google Authenticator)
# ----------------------------------------------------------
import pyotp


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, login_id: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=login_id, issuer_name="STI 경영관리")


def verify_totp(secret: str, code: str) -> bool:
    try:
        return pyotp.TOTP(secret).verify(str(code).strip(), valid_window=1)
    except Exception:
        return False


def enable_totp(user_id: str, secret: str):
    db = get_db()
    db.table("app_users").update(
        {"totp_secret": secret, "totp_enabled": True}
    ).eq("id", user_id).execute()


def reset_totp(user_id: str):
    db = get_db()
    db.table("app_users").update(
        {"totp_secret": None, "totp_enabled": False}
    ).eq("id", user_id).execute()
