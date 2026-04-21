import json
import os

STATE_FILE = "backfill/state/progress.json"


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def mark_chunk_done(state: dict, table: str, chunk_key: str):
    state.setdefault(table, {})
    state[table][chunk_key] = "done"
    save_state(state)


def is_chunk_done(state: dict, table: str, chunk_key: str) -> bool:
    return state.get(table, {}).get(chunk_key) == "done"
