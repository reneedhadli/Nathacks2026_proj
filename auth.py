"""
auth.py: login and account registration for the RA On-Shift Helper (Streamlit).

Run the app from this file:  streamlit run auth.py

Accounts live in the Supabase table "users", filled by the Create account tab.
No passwords are stored in this file.

Registration rules:
  - Everyone picks a role, Student or RA.
  - RA accounts need the invite code stored in the secret RA_INVITE_CODE.

Passwords in the database are salted and hashed with PBKDF2. The plain text
password is never stored.

Run setup_users_table.sql once in the Supabase SQL editor to create the table.
Settings come from .streamlit/secrets.toml (SUPABASE_URL, SUPABASE_KEY, RA_INVITE_CODE).
"""

import hashlib
import hmac
import re
import secrets

import streamlit as st

USERS_TABLE = "users"
USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,20}$")
MIN_PASSWORD_LENGTH = 6
PBKDF2_ROUNDS = 200_000


class AccountStoreError(Exception):
    """Raised when the account database cannot be reached."""


def _ra_invite_code() -> str:
    """Invite code for RA registration, read from secrets. Empty when not set."""
    try:
        return str(st.secrets.get("RA_INVITE_CODE", "")).strip()
    except Exception:
        return ""


def _pbkdf2(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        PBKDF2_ROUNDS,
    ).hex()


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _get_client():
    from supabase import create_client

    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def _fetch_db_user(username: str):
    """Return the stored row for a username, or None. Raises AccountStoreError."""
    try:
        response = (
            _get_client()
            .table(USERS_TABLE)
            .select("*")
            .eq("username", username)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise AccountStoreError(str(exc)) from exc
    return response.data[0] if response.data else None


# ---------------------------------------------------------------------------
# Login and registration logic
# ---------------------------------------------------------------------------

def authenticate(username: str, password: str):
    """
    Return the user dict on success, otherwise None.
    Raises AccountStoreError when the database cannot be reached.
    """
    username = username.strip().lower()

    row = _fetch_db_user(username)
    if row is None:
        # Hash anyway so response time does not reveal valid usernames.
        _pbkdf2(password, "00" * 16)
        return None

    if hmac.compare_digest(row["password_hash"], _pbkdf2(password, row["salt"])):
        # RAs are shown by username. Students use their display name.
        if row["role"] == "ra":
            name = row["username"]
        else:
            name = row.get("display_name") or row["username"]
        return {"username": row["username"], "role": row["role"], "name": name}
    return None


def register_account(username, display_name, password, role, invite_code=""):
    """Create an account in the database. Returns (success, message)."""
    username = username.strip().lower()
    display_name = display_name.strip() or username

    if role not in ("student", "ra"):
        return False, "Choose a role."
    if role == "ra":
        display_name = username
    if not USERNAME_PATTERN.match(username):
        return False, "Usernames use 3 to 20 lowercase letters, numbers, or underscores."
    if len(password) < MIN_PASSWORD_LENGTH:
        return False, f"Passwords need at least {MIN_PASSWORD_LENGTH} characters."
    if role == "ra":
        expected = _ra_invite_code()
        if not expected:
            return False, "RA registration is not set up. Add RA_INVITE_CODE to the secrets."
        if not hmac.compare_digest(
            invite_code.strip().encode("utf-8"), expected.encode("utf-8")
        ):
            return False, "The RA invite code is not valid."

    try:
        if _fetch_db_user(username) is not None:
            return False, "This username is taken."

        salt = secrets.token_hex(16)
        _get_client().table(USERS_TABLE).insert(
            {
                "username": username,
                "display_name": display_name,
                "role": role,
                "salt": salt,
                "password_hash": _pbkdf2(password, salt),
            }
        ).execute()
    except AccountStoreError:
        return False, "Could not reach the account database. Check .streamlit/secrets.toml."
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "23505" in str(exc):
            return False, "This username is taken."
        return False, "Could not save the account. Check that the users table exists."

    return True, "Account created. Open the Sign in tab to log in."


# ---------------------------------------------------------------------------
# Pages and helpers
# ---------------------------------------------------------------------------

def _render_sign_in_tab():
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username", placeholder="")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", use_container_width=True)

    if not submitted:
        return

    if not username or not password:
        st.warning("Enter both a username and a password.")
        return

    try:
        user = authenticate(username, password)
    except AccountStoreError:
        st.error("Could not reach the account database. Check .streamlit/secrets.toml.")
        return

    if user:
        st.session_state["user"] = user
        st.rerun()
    else:
        st.error("Incorrect username or password.")


def _render_register_tab():
    role_label = st.radio("I am a", ["Student", "RA"], horizontal=True, key="reg_role")
    display_name = ""
    if role_label == "Student":
        display_name = st.text_input("Display name", key="reg_display_name")
    username = st.text_input("Username", key="reg_username")
    password = st.text_input("Password", type="password", key="reg_password")
    confirm = st.text_input("Confirm password", type="password", key="reg_confirm")

    invite_code = ""
    if role_label == "RA":
        invite_code = st.text_input("RA invite code", type="password", key="reg_invite")

    if st.button("Create account", use_container_width=True, key="reg_submit"):
        if not username or not password:
            st.warning("Enter a username and a password.")
        elif password != confirm:
            st.error("The two passwords do not match.")
        else:
            ok, message = register_account(
                username,
                display_name,
                password,
                "ra" if role_label == "RA" else "student",
                invite_code,
            )
            if ok:
                st.success(message)
            else:
                st.error(message)


def show_login():
    """Render the sign in and registration tabs. Sets st.session_state['user'] on login."""
    st.markdown(
        """
        <style>
        /* Area outside the login block. Change this color to restyle it. */
        .stApp,
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"] {
            background-color: #ededed !important;
        }
        [data-testid="stHeader"] { background: transparent !important; }

        /* Login block, moved toward the center of the page */
        .block-container {
            max-width: 440px !important;
            margin-top: 12vh !important;
            margin-bottom: 100px !important;
            padding: 2rem 2rem 2.5rem 2rem !important;
            background-color: #ffffff;
            border-radius: 0;
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.12);
        }

        /* Green borders across the top and bottom of the login page */
        .stApp::before,
        .stApp::after {
            content: "";
            position: fixed;
            left: 0;
            right: 0;
            height: 60px;
            background: #275d38;
            z-index: 999999;
            pointer-events: none;
        }
        .stApp::before { top: 0; }
        .stApp::after { bottom: 0; }

        /* Input bars and buttons: light blue fill with black text */
        .stTextInput div[data-baseweb="input"],
        .stTextInput div[data-baseweb="input"] div,
        .stTextInput div[data-baseweb="base-input"],
        .stTextInput [data-testid="stTextInputRootElement"],
        .stTextInput [data-testid="stTextInputRootElement"] div,
        .stTextInput input,
        .stTextInput button {
            background-color: #e8f0fe !important;
            color: #000000 !important;
        }
        .stTextInput input::placeholder { color: #555555 !important; }
        .stTextInput button svg { fill: #000000 !important; }

        .stFormSubmitButton button,
        .stButton button {
            background-color: #e8f0fe !important;
            color: #000000 !important;
            border: 1px solid #b9c9e6 !important;
        }
        .stFormSubmitButton button p,
        .stButton button p { color: #000000 !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("RA On-Shift Helper")

    sign_in_tab, register_tab = st.tabs(["Sign in", "Create account"])
    with sign_in_tab:
        _render_sign_in_tab()
    with register_tab:
        _render_register_tab()



def require_login(allowed_roles=None):
    """
    Gate a page. Returns the logged-in user dict.
    Stops the script and shows the login form if nobody is signed in.
    Pass allowed_roles=["ra"] to restrict a page to certain roles.
    """
    user = st.session_state.get("user")

    if user is None:
        show_login()
        st.stop()

    if allowed_roles and user["role"] not in allowed_roles:
        st.error("Your account does not have access to this page.")
        show_user_sidebar(user)
        st.stop()

    return user


def show_user_sidebar(user):
    """
    Sidebar block with the signed-in name, role, and a logout button.
    Draws once per page run, so a second call from a page file is ignored.
    """
    run_id = st.session_state.get("_run_id")
    if run_id is not None and st.session_state.get("_sidebar_run") == run_id:
        return
    st.session_state["_sidebar_run"] = run_id

    with st.sidebar:
        st.markdown(f"**{user['name']}**")
        st.caption(f"Role: {user['role'].upper()}")
        if st.button("Log out", use_container_width=True, key="logout_btn"):
            st.session_state.pop("user", None)
            st.rerun()


def main():
    """
    Entry point: streamlit run auth.py
    Shows the login form first. After sign-in, students are sent to the
    student report page in student_app.py. RAs are sent to ra_app.py.
    """
    st.set_page_config(page_title="RA On-Shift Helper", page_icon="🏠")
    st.session_state["_run_id"] = st.session_state.get("_run_id", 0) + 1
    user = require_login()
    if user is None:
        # Happens when the file is run with plain python instead of streamlit.
        print("Run this with: streamlit run auth.py")
        return
    show_user_sidebar(user)

    if user["role"] == "student":
        pages = [st.Page("student_app.py", title="Report an Incident", icon="📝")]
    else:
        pages = [st.Page("ra_app.py", title="RA Dashboard", icon="🗺️")]

    st.navigation(pages).run()


if __name__ == "__main__":
    main()
#SUPABASE_URL="https://krayyvfhfmqfcsnidlyk.supabase.co/"
#SUPABASE_KEY="sb_publishable_8ZuNvfC62iTlwD7Aah9vxw_6xcDkGLs"
#cd “c:\Users\LENOVO\OneDrive\Desktop\Github\Nathacks2026_proj”
#streamlit run auth.py