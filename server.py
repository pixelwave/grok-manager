#!/usr/bin/env python3
"""Local portal for Grok sessions on this machine.

Reads session metadata from ~/.grok/sessions and can delete one session.
Bind address is localhost; nginx publishes it on the LAN.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ID_RE = re.compile(r"^[0-9a-f-]{36}$")
HOST = "127.0.0.1"
PORT = 18090
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT_DIR, "static")


def app_version() -> str:
    try:
        with open(os.path.join(ROOT_DIR, "VERSION"), encoding="utf-8") as handle:
            version = handle.read().strip()
    except OSError:
        return "0"
    return version or "0"


def machine_name() -> str:
    name = socket.gethostname().split(".")[0].strip()
    return html.escape(name or "this machine")
_LOCKS_GUARD = threading.Lock()


def sessions_root() -> str:
    return os.environ.get("GROK_SESSIONS_DIR") or os.path.expanduser("~/.grok/sessions")


def locks_path() -> str:
    return os.environ.get("GROK_LOCKS_PATH") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "locks.json"
    )


def load_locks() -> dict[str, set[str]]:
    try:
        with open(locks_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    session_items = data.get("sessions")
    folder_items = data.get("folders")
    if not isinstance(session_items, list):
        session_items = []
    if not isinstance(folder_items, list):
        folder_items = []
    return {
        "sessions": {
            item
            for item in session_items
            if isinstance(item, str) and ID_RE.fullmatch(item)
        },
        "folders": {
            item
            for item in folder_items
            if isinstance(item, str) and item.startswith("/")
        },
    }


def save_locks(locks: dict[str, set[str]]) -> None:
    path = locks_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    payload = {
        "sessions": sorted(locks["sessions"]),
        "folders": sorted(locks["folders"]),
    }
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def session_cwd(session_path: str) -> str | None:
    group_path = os.path.dirname(session_path)
    return group_cwd(group_path, os.path.basename(group_path))


def session_is_locked(session_id: str, cwd: str | None) -> bool:
    locks = load_locks()
    if session_id in locks["sessions"]:
        return True
    return bool(cwd) and cwd in locks["folders"]


def clear_session_lock(session_id: str) -> None:
    with _LOCKS_GUARD:
        locks = load_locks()
        if session_id not in locks["sessions"]:
            return
        locks["sessions"].discard(session_id)
        save_locks(locks)


def pins_path() -> str:
    return os.environ.get("GROK_PINS_PATH") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "pins.json"
    )


def load_pins() -> list[str]:
    try:
        with open(pins_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    items = data.get("sessions")
    if not isinstance(items, list):
        return []
    pins: list[str] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str) and ID_RE.fullmatch(item) and item not in seen:
            seen.add(item)
            pins.append(item)
    return pins


def machines_path() -> str:
    return os.environ.get("GROK_MACHINES_PATH") or os.path.join(ROOT_DIR, "machines.json")


def load_machines() -> list[dict]:
    try:
        with open(machines_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        return []
    items = data.get("machines")
    if not isinstance(items, list):
        return []
    machines: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        url = item.get("url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        key = name.casefold()
        if key in seen or machine_url(url) is None:
            continue
        seen.add(key)
        machines.append({"name": name, "url": url})
    return machines


def save_machines(machines: list[dict]) -> None:
    path = machines_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump({"machines": machines}, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def machine_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 500 or any(ord(char) < 32 for char in text):
        return None
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return text


def add_machine(name: object, url: object) -> tuple[int, dict]:
    if not isinstance(name, str):
        return 400, {"error": "Unknown machine"}
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > 80:
        return 400, {"error": "Enter a machine name"}
    target = machine_url(url)
    if target is None:
        return 400, {"error": "Enter a URL that starts with http:// or https://"}
    with _LOCKS_GUARD:
        machines = load_machines()
        if any(item["name"].casefold() == cleaned.casefold() for item in machines):
            return 409, {"error": "That machine is already listed"}
        if len(machines) >= 30:
            return 400, {"error": "Too many machines"}
        machines.append({"name": cleaned, "url": target})
        save_machines(machines)
    return 200, {"machines": machines}


def remove_machine(name: object) -> tuple[int, dict]:
    if not isinstance(name, str) or not name.strip():
        return 400, {"error": "Unknown machine"}
    key = " ".join(name.split()).casefold()
    with _LOCKS_GUARD:
        machines = load_machines()
        kept = [item for item in machines if item["name"].casefold() != key]
        if len(kept) == len(machines):
            return 404, {"error": "Machine not found"}
        save_machines(kept)
    return 200, {"machines": kept}


def save_pins(pins: list[str]) -> None:
    path = pins_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump({"sessions": pins}, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def clear_pin(session_id: str) -> None:
    with _LOCKS_GUARD:
        pins = load_pins()
        if session_id not in pins:
            return
        save_pins([item for item in pins if item != session_id])


def set_pin(ident: object, pinned: object) -> tuple[int, dict]:
    if not isinstance(ident, str) or pinned not in (True, False):
        return 400, {"error": "Unknown pin"}
    if not ID_RE.fullmatch(ident):
        return 400, {"error": "Invalid session id"}
    if pinned and find_session_dir(ident) is None:
        return 404, {"error": "Session not found"}
    with _LOCKS_GUARD:
        pins = [item for item in load_pins() if item != ident]
        if pinned:
            pins.insert(0, ident)
        save_pins(pins)
    return 200, {"ok": True}


def reorder_pins(order: object) -> tuple[int, dict]:
    if not isinstance(order, list):
        return 400, {"error": "Unknown pin"}
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in order:
        if not isinstance(item, str) or not ID_RE.fullmatch(item) or item in seen:
            return 400, {"error": "Invalid session id"}
        seen.add(item)
        cleaned.append(item)
    with _LOCKS_GUARD:
        current = load_pins()
        if set(cleaned) != set(current):
            return 409, {"error": "Pin list changed"}
        save_pins(cleaned)
    return 200, {"ok": True}


def set_lock(kind: object, ident: object, locked: object) -> tuple[int, dict]:
    if kind not in ("session", "folder") or not isinstance(ident, str) or locked not in (True, False):
        return 400, {"error": "Unknown lock"}
    with _LOCKS_GUARD:
        locks = load_locks()
        if kind == "session":
            if not ID_RE.fullmatch(ident):
                return 400, {"error": "Invalid session id"}
            if locked and find_session_dir(ident) is None:
                return 404, {"error": "Session not found"}
            if locked:
                locks["sessions"].add(ident)
            else:
                locks["sessions"].discard(ident)
        else:
            if not ident.startswith("/") or len(ident) > 512:
                return 400, {"error": "Unknown folder"}
            if locked and ident not in {group["cwd"] for group in list_groups()}:
                return 404, {"error": "Folder not found"}
            if locked:
                locks["folders"].add(ident)
            else:
                locks["folders"].discard(ident)
        save_locks(locks)
    return 200, {"ok": True}


def active_sessions_path() -> str:
    return os.environ.get("GROK_ACTIVE_SESSIONS") or os.path.expanduser(
        "~/.grok/active_sessions.json"
    )


def using_real_store() -> bool:
    real = os.path.realpath(os.path.expanduser("~/.grok/sessions"))
    try:
        current = os.path.realpath(sessions_root())
    except OSError:
        return False
    return current == real


def pid_alive(pid: object) -> bool:
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    try:
        os.kill(number, 0)
    except OSError:
        return False
    return True


def live_ids() -> set[str]:
    path = active_sessions_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    found: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        sid = row.get("session_id")
        if isinstance(sid, str) and pid_alive(row.get("pid")):
            found.add(sid)
    return found


def group_cwd(group_path: str, dirname: str) -> str:
    """Return the working directory recorded for a session group."""
    cwd = urllib.parse.unquote(dirname)
    marker = os.path.join(group_path, ".cwd")
    if os.path.isfile(marker):
        try:
            with open(marker, encoding="utf-8") as handle:
                recorded = handle.read().strip()
        except OSError:
            recorded = ""
        if recorded:
            cwd = recorded
    return cwd


def _title(summary: dict) -> str:
    for key in ("generated_title", "session_summary"):
        value = summary.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Untitled"


def _session_row(summary_path: str, live: set[str]) -> dict | None:
    try:
        with open(summary_path, encoding="utf-8") as handle:
            summary = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(summary, dict):
        return None
    info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
    sid = info.get("id")
    if not isinstance(sid, str) or not sid:
        sid = os.path.basename(os.path.dirname(summary_path))
    updated = summary.get("updated_at") if isinstance(summary.get("updated_at"), str) else ""
    created = summary.get("created_at") if isinstance(summary.get("created_at"), str) else ""
    model = summary.get("current_model_id")
    if not isinstance(model, str):
        model = None
    return {
        "id": sid,
        "title": _title(summary),
        "updatedAt": updated,
        "createdAt": created,
        "model": model,
        "live": sid in live,
    }


def list_groups() -> list[dict]:
    root = sessions_root()
    if not os.path.isdir(root):
        return []
    live = live_ids()
    locks = load_locks()
    pins = set(load_pins())
    grouped: dict[str, list[dict]] = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for name in names:
        group_path = os.path.join(root, name)
        if not os.path.isdir(group_path):
            continue
        cwd = group_cwd(group_path, name)
        try:
            children = os.listdir(group_path)
        except OSError:
            continue
        rows: list[dict] = []
        for child in children:
            session_path = os.path.join(group_path, child)
            summary_path = os.path.join(session_path, "summary.json")
            if not os.path.isdir(session_path) or not os.path.isfile(summary_path):
                continue
            row = _session_row(summary_path, live)
            if row is not None:
                row["locked"] = row["id"] in locks["sessions"]
                row["pinned"] = row["id"] in pins
                rows.append(row)
        if not rows:
            continue
        grouped.setdefault(cwd, []).extend(rows)

    groups = []
    for cwd in sorted(grouped):
        sessions = sorted(grouped[cwd], key=lambda row: row["updatedAt"], reverse=True)
        label = os.path.basename(cwd.rstrip(os.sep)) or cwd
        groups.append({
            "cwd": cwd,
            "label": label,
            "locked": cwd in locks["folders"],
            "sessions": sessions,
        })
    return groups


def find_session_dir(session_id: str) -> str | None:
    if not ID_RE.fullmatch(session_id):
        return None
    root = os.path.realpath(sessions_root())
    if not os.path.isdir(root):
        return None
    try:
        names = os.listdir(root)
    except OSError:
        return None
    for name in names:
        group_path = os.path.join(root, name)
        if not os.path.isdir(group_path):
            continue
        session_path = os.path.join(group_path, session_id)
        if not os.path.isdir(session_path):
            continue
        real = os.path.realpath(session_path)
        if os.path.basename(real) != session_id:
            continue
        if not real.startswith(root + os.sep):
            continue
        return real
    return None


def _official_delete(session_id: str) -> None:
    if not using_real_store():
        return
    try:
        subprocess.run(
            ["grok", "sessions", "delete", session_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def _drop_index_row(session_id: str) -> None:
    database = os.path.join(sessions_root(), "session_search.sqlite")
    if not os.path.isfile(database):
        return
    try:
        connection = sqlite3.connect(database, timeout=2)
    except sqlite3.Error:
        return
    try:
        connection.execute("DELETE FROM session_docs WHERE session_id = ?", (session_id,))
        connection.commit()
    except sqlite3.Error:
        return
    finally:
        connection.close()


def delete_session(session_id: str) -> tuple[int, str | None]:
    if not ID_RE.fullmatch(session_id):
        return 400, "Invalid session id"
    path = find_session_dir(session_id)
    if path is None:
        return 404, "Session not found"
    if session_id in live_ids():
        return 409, "This session is open in Grok"
    if session_is_locked(session_id, session_cwd(path)):
        return 409, "This session is locked"
    _official_delete(session_id)
    if os.path.isdir(path):
        shutil.rmtree(path)
    _drop_index_row(session_id)
    if os.path.isdir(path):
        return 500, "Could not delete session"
    clear_session_lock(session_id)
    clear_pin(session_id)
    return 204, None


def rename_session(session_id: object, title: object) -> tuple[int, dict]:
    if not isinstance(session_id, str) or not ID_RE.fullmatch(session_id):
        return 400, {"error": "Invalid session id"}
    if not isinstance(title, str):
        return 400, {"error": "Enter a name"}
    title = title.strip()
    if not title or "\n" in title or "\r" in title:
        return 400, {"error": "Enter a name"}
    if len(title) > 200:
        return 400, {"error": "Name is too long"}
    path = find_session_dir(session_id)
    if path is None:
        return 404, {"error": "Session not found"}
    if session_id in live_ids():
        return 409, {"error": "This session is open in Grok"}
    summary_path = os.path.join(path, "summary.json")
    try:
        with open(summary_path, encoding="utf-8") as handle:
            summary = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return 404, {"error": "Session not found"}
    if not isinstance(summary, dict):
        return 404, {"error": "Session not found"}
    summary["generated_title"] = title
    summary["title_is_manual"] = True
    try:
        _write_json(summary_path, summary)
    except OSError:
        return 500, {"error": "Could not rename that session"}
    _set_index_title(os.path.join(sessions_root(), "session_search.sqlite"), session_id, title)
    return 200, {"title": title}


def _set_index_title(database: str, session_id: str, title: str) -> None:
    if not database or not os.path.isfile(database):
        return
    try:
        connection = sqlite3.connect(database, timeout=2)
    except sqlite3.Error:
        return
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(session_docs)")}
        if "title" not in columns:
            return
        connection.execute("UPDATE session_docs SET title = ? WHERE session_id = ?", (title, session_id))
        connection.commit()
    except sqlite3.Error:
        return
    finally:
        connection.close()


def _post_error(path: str) -> str:
    if path == "/api/sessions/delete":
        return "Choose at least one session"
    if path == "/api/sessions/move":
        return "Unknown folder"
    if path == "/api/sessions/rename":
        return "Enter a name"
    if path == "/api/pins":
        return "Unknown pin"
    if path == "/api/machines":
        return "Unknown machine"
    if path == "/api/skills":
        return "Unknown skill"
    return "Unknown lock"


SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")


def skills_dir() -> str:
    return os.environ.get("GROK_SKILLS_DIR") or os.path.expanduser("~/.grok/skills")


def grok_config_path() -> str:
    return os.environ.get("GROK_CONFIG") or os.path.expanduser("~/.grok/config.toml")


def _first_sentence(text: str) -> str:
    text = " ".join(text.split())
    for index, char in enumerate(text):
        if char == "." and (index == len(text) - 1 or text[index + 1] == " "):
            return text[: index + 1]
    return text


def _display_sentence(text: str) -> str:
    sentence = _first_sentence(text)
    if len(sentence) <= 140:
        return sentence
    colon = sentence.find(":")
    if colon >= 20:
        head = sentence[:colon].strip()
        if len(head) <= 140:
            return head
    cut = sentence[:140].rstrip()
    space = cut.rfind(" ")
    if space > 40:
        cut = cut[:space].rstrip()
    return cut + "…"


def _skill_summary(path: str) -> str | None:
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    rest = text[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        return None
    short = ""
    description = ""
    folding = False
    for line in rest[:end].splitlines():
        if folding:
            if line.startswith(" ") or line.startswith("\t"):
                description = f"{description} {line.strip()}".strip()
                continue
            folding = False
        if ":" not in line or line.strip().startswith("#"):
            continue
        key, value = line.strip().split(":", 1)
        key = key.strip()
        value = value.strip()
        if key == "short-description":
            short = value.strip("\"'")
        elif key == "description":
            if value in ("", ">", "|", ">-", "|-"):
                folding = True
                description = ""
            else:
                description = value.strip("\"'")
    if short.strip():
        return " ".join(short.split())
    return _display_sentence(description)


def _read_disabled(path: str) -> list[str]:
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return []
    start = next((i for i, line in enumerate(lines) if line.strip() == "[skills]"), -1)
    if start < 0:
        return []
    end = len(lines)
    for index in range(start + 1, len(lines)):
        trim = lines[index].strip()
        if trim.startswith("[") and trim.endswith("]"):
            end = index
            break
    for index in range(start + 1, end):
        if lines[index].strip().startswith("disabled") and "=" in lines[index]:
            blob = lines[index]
            cursor = index
            while blob.count("[") > blob.count("]") and cursor + 1 < end:
                cursor += 1
                blob += "\n" + lines[cursor]
            value = blob.split("=", 1)[1]
            return re.findall(r"""["']([^"']*)["']""", value)
    return []


def _format_disabled(names: list[str]) -> str:
    return "disabled = [" + ", ".join(json.dumps(name) for name in names) + "]"


def _write_disabled(path: str, names: list[str]) -> None:
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        text = ""
    line = _format_disabled(names)
    lines = text.split("\n")
    start = next((i for i, item in enumerate(lines) if item.strip() == "[skills]"), -1)
    if start < 0:
        base = text
        if base and not base.endswith("\n"):
            base += "\n"
        if base:
            base += "\n"
        updated = base + "[skills]\n" + line + "\n"
    else:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            trim = lines[index].strip()
            if trim.startswith("[") and trim.endswith("]"):
                end = index
                break
        replaced = False
        for index in range(start + 1, end):
            if lines[index].strip().startswith("disabled") and "=" in lines[index]:
                cursor = index
                blob = lines[index]
                while blob.count("[") > blob.count("]") and cursor + 1 < end:
                    cursor += 1
                    blob += "\n" + lines[cursor]
                lines = lines[:index] + [line] + lines[cursor + 1 :]
                replaced = True
                break
        if not replaced:
            lines = lines[: start + 1] + [line] + lines[start + 1 :]
        updated = "\n".join(lines)
        if not updated.endswith("\n"):
            updated += "\n"
    temporary = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(updated)
    os.replace(temporary, path)


def list_skills() -> list[dict]:
    root = skills_dir()
    disabled = set(_read_disabled(grok_config_path()))
    found = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for name in names:
        if not SKILL_NAME.match(name):
            continue
        folder = os.path.join(root, name)
        if not os.path.isdir(folder) and not os.path.islink(folder):
            continue
        summary = _skill_summary(os.path.join(folder, "SKILL.md"))
        if summary is None:
            continue
        found.append({"name": name, "description": summary, "disabled": name in disabled})
    return found


def _known_skill(name: str) -> str | None:
    if not isinstance(name, str) or not SKILL_NAME.match(name):
        return None
    root = os.path.abspath(skills_dir())
    folder = os.path.abspath(os.path.join(root, name))
    if os.path.commonpath([root, folder]) != root:
        return None
    if not os.path.isdir(folder) and not os.path.islink(folder):
        return None
    if not os.path.isfile(os.path.join(folder, "SKILL.md")):
        return None
    return folder


def set_skill_disabled(name: str, disabled: bool) -> tuple[int, dict]:
    if _known_skill(name) is None:
        return 404, {"error": "Skill not found"}
    path = grok_config_path()
    names = [item for item in _read_disabled(path) if item != name]
    if disabled:
        names.append(name)
    try:
        _write_disabled(path, names)
    except OSError:
        return 500, {"error": "Could not update that skill."}
    return 200, {"skills": list_skills()}


def delete_skill(name: str) -> tuple[int, dict]:
    folder = _known_skill(name)
    if folder is None:
        return 404, {"error": "Skill not found"}
    try:
        if os.path.islink(folder):
            os.remove(folder)
        else:
            shutil.rmtree(folder)
    except OSError:
        return 500, {"error": "Could not delete that skill."}
    path = grok_config_path()
    if os.path.isfile(path):
        names = [item for item in _read_disabled(path) if item != name]
        try:
            _write_disabled(path, names)
        except OSError:
            pass
    return 200, {"skills": list_skills()}


MAX_BATCH = 200


def find_group_dir(cwd: str) -> str | None:
    root = sessions_root()
    if not os.path.isdir(root):
        return None
    preferred = urllib.parse.quote(cwd, safe="")
    matches: list[tuple[bool, str, str]] = []
    try:
        names = os.listdir(root)
    except OSError:
        return None
    for name in names:
        group_path = os.path.join(root, name)
        if not os.path.isdir(group_path):
            continue
        if group_cwd(group_path, name) != cwd:
            continue
        matches.append((name != preferred, name, group_path))
    if not matches:
        return None
    matches.sort()
    chosen = matches[0][2]
    real_root = os.path.realpath(root)
    real_group = os.path.realpath(chosen)
    if not real_group.startswith(real_root + os.sep):
        return None
    return chosen


def memory_workspaces_root() -> str:
    return os.environ.get("GROK_MEMORY_WORKSPACES") or os.path.expanduser(
        "~/.grok/memory-v2/workspaces"
    )


def memory_workspace_for(cwd: str) -> str | None:
    root = memory_workspaces_root()
    if not os.path.isdir(root):
        return None
    label = os.path.basename(cwd.rstrip(os.sep))
    if not label:
        return None
    prefix = label + "-"
    matches: list[str] = []
    try:
        names = os.listdir(root)
    except OSError:
        return None
    for name in names:
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix) :]
        if re.fullmatch(r"[0-9a-f]{8}", suffix) and os.path.isdir(os.path.join(root, name)):
            matches.append(name)
    if len(matches) != 1:
        return None
    return os.path.join(root, matches[0])


def _write_json(path: str, payload: dict) -> None:
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def _point_session_at(session_dir: str, cwd: str) -> None:
    summary_path = os.path.join(session_dir, "summary.json")
    if os.path.isfile(summary_path):
        with open(summary_path, encoding="utf-8") as handle:
            summary = json.load(handle)
        if not isinstance(summary, dict):
            summary = {}
        info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
        info["cwd"] = cwd
        summary["info"] = info
        _write_json(summary_path, summary)
    context_path = os.path.join(session_dir, "prompt_context.json")
    if not os.path.isfile(context_path):
        return
    with open(context_path, encoding="utf-8") as handle:
        context = json.load(handle)
    if not isinstance(context, dict):
        return
    context["working_directory"] = cwd
    workspace = memory_workspace_for(cwd)
    if workspace:
        context["memory_workspace_path"] = workspace
    _write_json(context_path, context)


def _set_index_cwd(database: str | None, table: str, session_id: str, cwd: str) -> None:
    if not database or not os.path.isfile(database):
        return
    try:
        connection = sqlite3.connect(database, timeout=5)
    except sqlite3.Error:
        return
    try:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if table not in names:
            return
        connection.execute(f"UPDATE {table} SET cwd = ? WHERE session_id = ?", (cwd, session_id))
        connection.commit()
    except sqlite3.Error:
        return
    finally:
        connection.close()


def move_session(session_id: object, target: object) -> tuple[int, dict]:
    if not isinstance(session_id, str) or not ID_RE.fullmatch(session_id):
        return 400, {"error": "Invalid session id"}
    if not isinstance(target, str) or not target.startswith("/") or len(target) > 512:
        return 400, {"error": "Unknown folder"}
    path = find_session_dir(session_id)
    if path is None:
        return 404, {"error": "Session not found"}
    source = session_cwd(path)
    if source == target:
        return 400, {"error": "Session is already in that folder"}
    if target not in {group["cwd"] for group in list_groups()}:
        return 404, {"error": "Folder not found"}
    if session_id in live_ids():
        return 409, {"error": "This session is open in Grok"}
    if session_is_locked(session_id, source):
        return 409, {"error": "This session is locked"}
    destination_parent = find_group_dir(target)
    if destination_parent is None:
        return 404, {"error": "Folder not found"}
    destination = os.path.join(destination_parent, session_id)
    if os.path.exists(destination):
        return 409, {"error": "A session with that id is already in that folder"}
    shutil.move(path, destination)
    try:
        _point_session_at(destination, target)
    except (OSError, json.JSONDecodeError):
        return 500, {"error": "Could not update the session folder"}
    _set_index_cwd(os.path.join(sessions_root(), "session_search.sqlite"), "session_docs", session_id, target)
    dashboard = os.environ.get("GROK_DASHBOARD_DB")
    if not dashboard and using_real_store():
        dashboard = os.path.expanduser("~/.grok/dashboard/workspace.db")
    _set_index_cwd(dashboard, "members", session_id, target)
    return 200, {"ok": True, "cwd": target}


def delete_sessions(ids: object) -> tuple[int, dict]:
    if not isinstance(ids, list) or not ids:
        return 400, {"error": "Choose at least one session"}
    if len(ids) > MAX_BATCH:
        return 400, {"error": "Too many sessions"}
    deleted: list[str] = []
    failed: list[dict] = []
    seen: set[str] = set()
    for raw in ids:
        if not isinstance(raw, str) or raw in seen:
            if not isinstance(raw, str):
                failed.append({"id": "", "error": "Invalid session id"})
            continue
        seen.add(raw)
        status, message = delete_session(raw)
        if status == 204:
            deleted.append(raw)
        else:
            failed.append({"id": raw, "error": message or "Could not delete session"})
    return 200, {"deleted": deleted, "failed": failed}


class Handler(BaseHTTPRequestHandler):
    server_version = "GrokManager"

    def version_string(self) -> str:
        return f"GrokManager/{app_version()}"

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/sessions":
            self._send_json(200, {"groups": list_groups(), "pinned": load_pins(), "skills": list_skills(), "machines": load_machines()})
            return
        if path in ("/", "/index.html"):
            self._send_file("index.html", "text/html; charset=utf-8")
            return
        self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > 65536:
            self._send_json(400, {"error": _post_error(path)})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._send_json(400, {"error": _post_error(path)})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "Unknown lock"})
            return
        if path == "/api/sessions/delete":
            status, body = delete_sessions(payload.get("ids"))
            self._send_json(status, body)
            return
        if path == "/api/locks":
            status, body = set_lock(payload.get("kind"), payload.get("id"), payload.get("locked"))
            self._send_json(status, body)
            return
        if path == "/api/pins":
            if "order" in payload:
                status, body = reorder_pins(payload.get("order"))
            else:
                status, body = set_pin(payload.get("id"), payload.get("pinned"))
            self._send_json(status, body)
            return
        if path == "/api/sessions/move":
            status, body = move_session(payload.get("id"), payload.get("cwd"))
            self._send_json(status, body)
            return
        if path == "/api/sessions/rename":
            status, body = rename_session(payload.get("id"), payload.get("title"))
            self._send_json(status, body)
            return
        if path == "/api/machines":
            if payload.get("remove") is True:
                status, body = remove_machine(payload.get("name"))
            else:
                status, body = add_machine(payload.get("name"), payload.get("url"))
            self._send_json(status, body)
            return
        if path == "/api/skills":
            if payload.get("remove") is True:
                status, body = delete_skill(payload.get("name"))
            elif isinstance(payload.get("disabled"), bool):
                status, body = set_skill_disabled(payload.get("name"), payload.get("disabled"))
            else:
                status, body = 400, {"error": "Unknown skill"}
            self._send_json(status, body)
            return
        self._send_json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        prefix = "/api/sessions/"
        if not path.startswith(prefix) or path.count("/") != 3:
            self._send_json(404, {"error": "Not found"})
            return
        session_id = urllib.parse.unquote(path[len(prefix) :])
        status, message = delete_session(session_id)
        if status == 204:
            self._send_empty(204)
            return
        self._send_json(status, {"error": message or "Could not delete session"})

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_file(self, name: str, content_type: str) -> None:
        path = os.path.join(STATIC_DIR, name)
        try:
            with open(path, "rb") as handle:
                body = handle.read()
        except OSError:
            self._send_json(404, {"error": "Not found"})
            return
        if name == "index.html":
            body = body.replace(b"__VERSION__", app_version().encode("utf-8"))
            body = body.replace(b"__HOSTNAME__", machine_name().encode("utf-8"))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Grok Manager listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
