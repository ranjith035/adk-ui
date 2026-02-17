import hmac
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import streamlit as st

DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_APP = "research_team"
DEFAULT_USER_PIN = "111111"
MAX_SAVED_MESSAGES = 500
BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = BASE_DIR / "users.json"
HISTORY_DIR = BASE_DIR / "data" / "chat_history"
HISTORY_LOCK = threading.Lock()

st.set_page_config(
    page_title="ADK Multi-Agent Workspace",
    page_icon=":robot_face:",
    layout="wide",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_username(raw: str) -> str:
    lowered = raw.strip().lower()
    lowered = re.sub(r"\s+", "", lowered)
    return re.sub(r"[^a-z0-9@._-]", "", lowered)


def safe_filename(value: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", value)


def is_valid_pin(input_pin: str, expected_pin: str) -> bool:
    return hmac.compare_digest(input_pin.strip(), expected_pin.strip())


def load_allowed_users(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        raise RuntimeError(f"Missing allowlist file: {path}")

    raw = json.loads(path.read_text(encoding="utf-8-sig"))

    if isinstance(raw, dict):
        source = raw.get("users") or raw.get("allowed_users")
    elif isinstance(raw, list):
        source = raw
    else:
        source = None

    if not isinstance(source, list):
        raise RuntimeError("users.json must contain a list under 'users' or 'allowed_users'.")

    allowed: dict[str, dict[str, str]] = {}
    for item in source:
        if isinstance(item, str):
            username = item
            display_name = item
            pin = DEFAULT_USER_PIN
        elif isinstance(item, dict):
            username = str(item.get("username", "")).strip()
            display_name = str(item.get("display_name") or username).strip()
            pin = str(item.get("pin") or DEFAULT_USER_PIN).strip()
        else:
            continue

        normalized = normalize_username(username)
        if not normalized:
            continue

        allowed[normalized] = {
            "username": username,
            "display_name": display_name,
            "pin": pin,
        }

    if not allowed:
        raise RuntimeError("No valid users found in users.json.")

    return allowed


def history_path(normalized_user: str) -> Path:
    return HISTORY_DIR / f"{safe_filename(normalized_user)}.json"


def load_user_state(normalized_user: str) -> dict[str, Any]:
    path = history_path(normalized_user)
    if not path.exists():
        return {"session_id": "s_" + uuid.uuid4().hex[:10], "messages": []}

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"session_id": "s_" + uuid.uuid4().hex[:10], "messages": []}

    session_id = state.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        session_id = "s_" + uuid.uuid4().hex[:10]

    parsed_messages: list[dict[str, str]] = []
    for item in state.get("messages", []):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            parsed_messages.append(
                {
                    "role": role,
                    "content": content,
                    "timestamp": str(item.get("timestamp", "")),
                }
            )

    return {"session_id": session_id, "messages": parsed_messages[-MAX_SAVED_MESSAGES:]}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def save_user_state(normalized_user: str, session_id: str, messages: list[dict[str, Any]]) -> None:
    payload = {
        "user": normalized_user,
        "session_id": session_id,
        "updated_at": now_iso(),
        "messages": messages[-MAX_SAVED_MESSAGES:],
    }
    with HISTORY_LOCK:
        atomic_write_json(history_path(normalized_user), payload)


def check_server(url: str) -> tuple[bool, str]:
    try:
        response = requests.get(f"{url.rstrip('/')}/list-apps", timeout=5)
        if response.ok:
            return True, f"Online. Apps: {response.json()}"
        return False, f"Server responded with {response.status_code}"
    except Exception as exc:
        return False, f"Offline: {exc}"


def extract_text(events: Any) -> str:
    if not isinstance(events, list):
        return "No response events returned by ADK."

    parts: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        content = event.get("content") or {}
        if not isinstance(content, dict) or content.get("role") != "model":
            continue
        for part in content.get("parts", []):
            if isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))

    return "\n".join(parts).strip() or "Model responded without text content."


def parse_sse_events(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue

        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue

        try:
            item = json.loads(payload)
            if isinstance(item, dict):
                events.append(item)
        except json.JSONDecodeError:
            continue

    return events


def extract_retry_delay_seconds(response: requests.Response) -> float | None:
    try:
        payload = response.json()
    except ValueError:
        return None

    error_obj = payload.get("error")
    if not isinstance(error_obj, dict):
        return None

    details = error_obj.get("details")
    if not isinstance(details, list):
        return None

    for item in details:
        if not isinstance(item, dict):
            continue
        retry_delay = item.get("retryDelay")
        if not isinstance(retry_delay, str):
            continue

        match = re.match(r"^([0-9]+(?:\.[0-9]+)?)s$", retry_delay.strip())
        if not match:
            continue

        try:
            return float(match.group(1))
        except ValueError:
            return None

    return None


def format_quota_error(response: requests.Response) -> str:
    retry_secs = extract_retry_delay_seconds(response)
    retry_hint = f" Retry in about {retry_secs:.1f}s." if retry_secs is not None else ""
    return (
        "Gemini API quota exceeded (429 RESOURCE_EXHAUSTED). "
        "Your current plan limit has been reached." + retry_hint
    )


def ensure_session(api_base: str, app_name: str, user_id: str, session_id: str) -> str:
    url = f"{api_base.rstrip('/')}/apps/{app_name}/users/{user_id}/sessions"
    payload = {"sessionId": session_id}

    try:
        response = requests.post(url, json=payload, timeout=20)
    except requests.RequestException as exc:
        return f"Session create call failed: {exc}"

    if response.status_code in (200, 201):
        return "created"
    if response.status_code == 409:
        return "exists"
    return f"failed ({response.status_code}): {response.text[:300]}"


def run_agent(api_base: str, app_name: str, user_id: str, session_id: str, prompt: str) -> tuple[str, Any, str]:
    base = api_base.rstrip("/")
    session_status = ensure_session(base, app_name, user_id, session_id)
    if session_status.startswith("failed"):
        raise RuntimeError(f"Could not prepare session {session_id}: {session_status}")

    attempts = [
        (
            "POST /run (camelCase)",
            f"{base}/run",
            {
                "appName": app_name,
                "userId": user_id,
                "sessionId": session_id,
                "newMessage": {"role": "user", "parts": [{"text": prompt}]},
            },
            False,
        ),
        (
            "POST /run (snake_case)",
            f"{base}/run",
            {
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id,
                "new_message": {"role": "user", "parts": [{"text": prompt}]},
            },
            False,
        ),
        (
            "POST /run_sse (camelCase)",
            f"{base}/run_sse",
            {
                "appName": app_name,
                "userId": user_id,
                "sessionId": session_id,
                "newMessage": {"role": "user", "parts": [{"text": prompt}]},
                "streaming": False,
            },
            True,
        ),
        (
            "POST /run_sse (snake_case)",
            f"{base}/run_sse",
            {
                "app_name": app_name,
                "user_id": user_id,
                "session_id": session_id,
                "new_message": {"role": "user", "parts": [{"text": prompt}]},
                "streaming": False,
            },
            True,
        ),
    ]

    last_error = "No compatible ADK run endpoint found."
    for label, url, payload, is_sse in attempts:
        for retry in range(2):
            try:
                response = requests.post(url, json=payload, timeout=180)
                if response.status_code == 404:
                    last_error = f"{label} -> 404 ({response.text[:200]})"
                    break

                if response.status_code == 429:
                    if retry == 0:
                        retry_secs = extract_retry_delay_seconds(response)
                        if retry_secs is not None:
                            time.sleep(min(retry_secs, 10.0))
                            continue
                    raise RuntimeError(format_quota_error(response))

                response.raise_for_status()
                events = parse_sse_events(response.text) if is_sse else response.json()
                return extract_text(events), events, label
            except requests.RequestException as exc:
                last_error = f"{label} -> {exc}"
                break
            except ValueError as exc:
                last_error = f"{label} -> Invalid JSON response: {exc}"
                break

    raise RuntimeError(last_error)


def init_state() -> None:
    st.session_state.setdefault("authenticated", False)
    st.session_state.setdefault("username", "")
    st.session_state.setdefault("display_name", "")
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("session_id", "")
    st.session_state.setdefault("api_base", DEFAULT_API)
    st.session_state.setdefault("app_name", DEFAULT_APP)
    st.session_state.setdefault("auth_fail_count", 0)
    st.session_state.setdefault("last_events", [])
    st.session_state.setdefault("last_route", "")


def login_user(normalized_user: str, display_name: str) -> None:
    state = load_user_state(normalized_user)
    st.session_state.authenticated = True
    st.session_state.username = normalized_user
    st.session_state.display_name = display_name or normalized_user
    st.session_state.messages = state["messages"]
    st.session_state.session_id = state["session_id"]


def logout_user() -> None:
    if st.session_state.get("authenticated") and st.session_state.get("username"):
        save_user_state(
            st.session_state.username,
            st.session_state.session_id,
            st.session_state.messages,
        )

    st.session_state.authenticated = False
    st.session_state.username = ""
    st.session_state.display_name = ""
    st.session_state.messages = []
    st.session_state.session_id = ""
    st.session_state.last_events = []
    st.session_state.last_route = ""


def render_login(allowed_users: dict[str, dict[str, str]]) -> None:
    st.title("ADK Multi-Agent Workspace")
    st.write("Simple secure login.")

    left, center, right = st.columns([1.3, 2, 1.3])
    with center:
        with st.form("login_form", clear_on_submit=False):
            username_input = st.text_input("Username", placeholder="name@company.com")
            pin_input = st.text_input("PIN", type="password", placeholder="6-digit PIN")
            submitted = st.form_submit_button("Sign in", use_container_width=True, type="primary")

        st.caption(f"Allowlisted users loaded: {len(allowed_users)}")

    if not submitted:
        return

    normalized = normalize_username(username_input)
    user = allowed_users.get(normalized)
    expected_pin = user.get("pin", DEFAULT_USER_PIN) if user else DEFAULT_USER_PIN

    if user and is_valid_pin(pin_input, expected_pin):
        st.session_state.auth_fail_count = 0
        login_user(normalized, user["display_name"])
        st.rerun()

    st.session_state.auth_fail_count += 1
    st.error("Access denied: invalid username or PIN.")
    if st.session_state.auth_fail_count >= 3:
        st.warning("Multiple failed attempts. Verify username/PIN in users.json.")


def render_sidebar(server_ok: bool, server_status: str) -> None:
    with st.sidebar:
        st.header("Workspace")
        st.write(f"**User:** {st.session_state.display_name}")
        st.caption(f"ID: {st.session_state.username}")

        st.session_state.api_base = st.text_input("ADK API base URL", value=st.session_state.api_base)
        st.session_state.app_name = st.text_input("ADK app name", value=st.session_state.app_name)

        if server_ok:
            st.success(server_status)
        else:
            st.error(server_status)

        col1, col2 = st.columns(2)
        if col1.button("New Session", use_container_width=True):
            st.session_state.session_id = "s_" + uuid.uuid4().hex[:10]
            save_user_state(st.session_state.username, st.session_state.session_id, st.session_state.messages)

        if col2.button("Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.session_state.last_events = []
            save_user_state(st.session_state.username, st.session_state.session_id, st.session_state.messages)

        if st.button("Sign Out", use_container_width=True):
            logout_user()
            st.rerun()

        st.caption(f"Session: {st.session_state.session_id}")
        st.caption(f"Saved messages: {len(st.session_state.messages)}")


def render_chat() -> None:
    st.title("Chat")
    st.caption("Simple and functional interface.")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Type your message...")
    if not prompt:
        return

    user_message = {"role": "user", "content": prompt, "timestamp": now_iso()}
    st.session_state.messages.append(user_message)
    save_user_state(st.session_state.username, st.session_state.session_id, st.session_state.messages)

    with st.chat_message("user"):
        st.markdown(prompt)

    server_ok, _ = check_server(st.session_state.api_base)
    if not server_ok:
        reply = "ADK API server is offline. Start it with `python launcher.py` or `adk api_server`."
        events: Any = []
        route_used = "none"
    else:
        try:
            reply, events, route_used = run_agent(
                st.session_state.api_base,
                st.session_state.app_name,
                st.session_state.username,
                st.session_state.session_id,
                prompt,
            )
        except Exception as exc:
            reply = f"Request failed: {exc}"
            events = []
            route_used = "failed"

    assistant_message = {"role": "assistant", "content": reply, "timestamp": now_iso()}
    st.session_state.messages.append(assistant_message)
    st.session_state.last_events = events if isinstance(events, list) else []
    st.session_state.last_route = route_used
    save_user_state(st.session_state.username, st.session_state.session_id, st.session_state.messages)

    with st.chat_message("assistant"):
        st.markdown(reply)


init_state()

try:
    allowed_users = load_allowed_users(USERS_FILE)
except Exception as exc:
    st.error(f"Unable to load users.json: {exc}")
    st.stop()

if not st.session_state.authenticated:
    render_login(allowed_users)
    st.stop()

if st.session_state.username not in allowed_users:
    st.error("This account is no longer allowlisted. Contact admin.")
    if st.button("Sign out"):
        logout_user()
        st.rerun()
    st.stop()

if not st.session_state.session_id:
    restored = load_user_state(st.session_state.username)
    st.session_state.session_id = restored["session_id"]
    st.session_state.messages = restored["messages"]

server_ok, server_status = check_server(st.session_state.api_base)
render_sidebar(server_ok, server_status)
render_chat()
