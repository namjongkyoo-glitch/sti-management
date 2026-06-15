"""STI AD USA 통합 경영관리 시스템 - 메인 앱"""
import streamlit as st
import auth

import os
_LOGO = os.path.join(os.path.dirname(__file__), "sti_logo.png")
st.set_page_config(
    page_title="STI 경영관리",
    page_icon=_LOGO if os.path.exists(_LOGO) else "🏢",
    layout="wide")

# ----------------------------------------------------------
# 페이지 코드 -> 화면 함수 매핑
# ----------------------------------------------------------
from views import (dashboard, estimates, proposals, projects, schedule,
                   expenses, funds, cashplan, invoices, purchase, vendors,
                   loans, employees, payroll, admin)

VIEWS = {
    "dashboard": dashboard.render,
    "estimates": estimates.render,
    "proposals": proposals.render,
    "projects":  projects.render,
    "schedule":  schedule.render,
    "expenses":  expenses.render,
    "funds":     funds.render,
    "cashplan":  cashplan.render,
    "invoices":  invoices.render,
    "purchase":  purchase.render,
    "vendors":   vendors.render,
    "loans":     loans.render,
    "employees": employees.render,
    "payroll":   payroll.render,
    "admin":     admin.render,
}


# ----------------------------------------------------------
# 1) 최초 실행: 사용자가 없으면 관리자 계정 생성 화면
# ----------------------------------------------------------
def first_run_screen():
    if os.path.exists(_LOGO):
        st.image(_LOGO, width=120)
    st.title("STI 경영관리 시스템 - 초기 설정")
    st.info("등록된 사용자가 없습니다. 최초 관리자(Admin) 계정을 생성하세요.")
    with st.form("first_admin"):
        login_id = st.text_input("로그인 ID")
        name = st.text_input("이름")
        email = st.text_input("이메일 (선택)")
        pw1 = st.text_input("비밀번호", type="password")
        pw2 = st.text_input("비밀번호 확인", type="password")
        ok = st.form_submit_button("관리자 계정 생성", type="primary")
    if ok:
        if not login_id or not name or not pw1:
            st.error("로그인 ID, 이름, 비밀번호는 필수입니다.")
        elif pw1 != pw2:
            st.error("비밀번호가 일치하지 않습니다.")
        elif len(pw1) < 6:
            st.error("비밀번호는 6자 이상으로 해주세요.")
        else:
            auth.create_user(login_id, name, pw1, role="admin", email=email)
            st.success("관리자 계정이 생성되었습니다. 로그인해주세요.")
            st.rerun()


# ----------------------------------------------------------
# 2) 로그인 화면 (2단계 인증: MS Authenticator)
# ----------------------------------------------------------
def complete_login(user):
    st.session_state["user"] = user
    st.session_state["perms"] = auth.get_user_permissions(user)
    st.session_state.pop("pending_user", None)
    st.session_state.pop("totp_setup_secret", None)
    st.rerun()


def login_screen():
    if os.path.exists(_LOGO):
        st.image(_LOGO, width=120)
    st.title("STI 경영관리 시스템")
    pending = st.session_state.get("pending_user")
    col, _ = st.columns([1, 2])

    # ---- 1단계: ID / 비밀번호 ----
    if pending is None:
        with col:
            with st.form("login"):
                login_id = st.text_input("로그인 ID")
                password = st.text_input("비밀번호", type="password")
                ok = st.form_submit_button("로그인", type="primary",
                                           use_container_width=True)
            if ok:
                user = auth.login(login_id, password)
                if user:
                    st.session_state["pending_user"] = user
                    st.rerun()
                else:
                    st.error("로그인 ID 또는 비밀번호가 올바르지 않습니다.")
        return

    user = pending

    # ---- 2단계 (최초): Authenticator 등록 ----
    if not user.get("totp_enabled"):
        with col:
            st.subheader("🔐 2단계 인증 등록 (최초 1회)")
            st.markdown(
                "1. 휴대폰에서 **Microsoft Authenticator** 앱 설치  \n"
                "2. 앱에서 **+ 계정 추가 → 기타 계정(QR 코드 스캔)** 선택  \n"
                "3. 아래 QR 코드를 스캔  \n"
                "4. 앱에 표시되는 6자리 번호를 입력")
            if "totp_setup_secret" not in st.session_state:
                st.session_state["totp_setup_secret"] = auth.new_totp_secret()
            secret = st.session_state["totp_setup_secret"]
            uri = auth.totp_uri(secret, user["login_id"])
            try:
                import qrcode
                from io import BytesIO
                img = qrcode.make(uri)
                buf = BytesIO()
                img.save(buf, format="PNG")
                st.image(buf.getvalue(), width=220)
            except Exception:
                st.info("QR 생성 모듈(qrcode)이 없어 수동 등록 키만 표시합니다.")
            st.caption(f"QR 스캔이 어려우면 앱에 수동 입력: 계정명 임의, 키 = **{secret}**")
            code = st.text_input("인증번호 6자리", max_chars=6, key="setup_code")
            c1, c2 = st.columns(2)
            if c1.button("✅ 등록 및 로그인", type="primary",
                         use_container_width=True):
                if auth.verify_totp(secret, code):
                    auth.enable_totp(user["id"], secret)
                    user["totp_enabled"] = True
                    complete_login(user)
                else:
                    st.error("인증번호가 올바르지 않습니다. 다시 확인해주세요.")
            if c2.button("← 다른 계정으로", use_container_width=True):
                st.session_state.pop("pending_user", None)
                st.session_state.pop("totp_setup_secret", None)
                st.rerun()
        return

    # ---- 2단계 (등록 완료): 인증번호 입력 ----
    with col:
        st.subheader("🔐 2단계 인증")
        st.caption(f"{user['name']}님, Microsoft Authenticator 앱의 "
                   "6자리 인증번호를 입력하세요.")
        with st.form("totp"):
            code = st.text_input("인증번호 6자리", max_chars=6)
            ok = st.form_submit_button("확인", type="primary",
                                       use_container_width=True)
        if ok:
            if auth.verify_totp(user.get("totp_secret") or "", code):
                complete_login(user)
            else:
                st.error("인증번호가 올바르지 않습니다. "
                         "휴대폰 시간이 자동 설정인지 확인해주세요.")
        if st.button("← 다른 계정으로"):
            st.session_state.pop("pending_user", None)
            st.rerun()


# ----------------------------------------------------------
# 3) 메인 (로그인 후)
# ----------------------------------------------------------
def main_screen():
    user = st.session_state["user"]
    perms = st.session_state["perms"]
    # 일시적 네트워크 끊김(WinError 10035) 대비 재시도
    pages = None
    for attempt in range(3):
        try:
            pages = auth.get_all_pages()
            break
        except Exception:
            from db import reset_db
            reset_db()
            if attempt == 2:
                st.error("일시적인 연결 오류가 발생했습니다. "
                         "잠시 후 화면을 새로고침(F5)해 주세요.")
                if st.button("🔄 다시 시도"):
                    st.rerun()
                return

    # 접근 가능한 메뉴만 표시
    visible = [p for p in pages
               if perms.get(p["code"], {}).get("can_view")]

    with st.sidebar:
        lc = st.columns([1, 3])
        if os.path.exists(_LOGO):
            lc[0].image(_LOGO, width=60)
        lc[1].markdown("#### STI 경영관리")
        st.caption(f"👤 {user['name']} ({auth.ROLE_LABEL.get(user['role'], user['role'])})")
        if user.get("role") == "viewer":
            st.caption("👁️ 조회 전용 (입력·수정 불가)")
        st.divider()
        if not visible:
            st.warning("접근 가능한 페이지가 없습니다. 관리자에게 문의하세요.")
            choice = None
        else:
            labels = [p["name_kr"] for p in visible]
            idx = st.radio("메뉴", range(len(labels)),
                           format_func=lambda i: labels[i],
                           label_visibility="collapsed")
            choice = visible[idx]["code"]
        st.divider()
        if st.button("로그아웃", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    # 메뉴가 바뀌면 각 페이지의 상세 화면 상태 초기화 (첫 페이지로 이동)
    DETAIL_KEYS = ["est_open", "est_award", "prj_open", "po_open",
                   "inv_open", "prop_open", "sched_open", "emp_open",
                   "pay_open", "loan_open", "vendor_open"]
    if st.session_state.get("_last_menu") != choice:
        for k in DETAIL_KEYS:
            st.session_state.pop(k, None)
        st.session_state["_last_menu"] = choice

    if choice and choice in VIEWS:
        try:
            VIEWS[choice]()
        except Exception as e:
            msg = str(e)
            if "10035" in msg or "ReadError" in msg or "Connect" in msg:
                from db import reset_db
                reset_db()
                st.warning("일시적인 연결 오류가 발생했습니다. "
                           "방금 작업이 저장되지 않았을 수 있으니 "
                           "아래 버튼으로 다시 시도해 주세요.")
                if st.button("🔄 다시 시도"):
                    st.rerun()
            else:
                raise


# ----------------------------------------------------------
# 진입점
# ----------------------------------------------------------
if "user" not in st.session_state:
    if auth.count_users() == 0:
        first_run_screen()
    else:
        login_screen()
else:
    main_screen()
