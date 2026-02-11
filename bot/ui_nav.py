from __future__ import annotations

from typing import Any

STACK_KEY = "nav_stack"
STATE_KEY = "fsm_state"


def get_state(user_data: dict[str, Any], default: str = "idle") -> str:
    return str(user_data.get(STATE_KEY) or default)


def set_state(user_data: dict[str, Any], state: str) -> None:
    user_data[STATE_KEY] = state


def stack(user_data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = user_data.get(STACK_KEY)
    if isinstance(raw, list):
        return raw
    user_data[STACK_KEY] = []
    return user_data[STACK_KEY]


def push_screen(user_data: dict[str, Any], screen: str, payload: dict[str, Any] | None = None) -> None:
    st = stack(user_data)
    item = {"screen": screen, "payload": payload or {}}
    if st and st[-1] == item:
        return
    st.append(item)


def pop_screen(user_data: dict[str, Any]) -> dict[str, Any] | None:
    st = stack(user_data)
    if not st:
        return None
    return st.pop()


def reset_stack(user_data: dict[str, Any]) -> None:
    user_data[STACK_KEY] = []
