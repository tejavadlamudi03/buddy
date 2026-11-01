"""Talks to an app's accessibility tree. Runs under the system Python, which has the bindings.

Usage: a11y_helper.py PID list
       a11y_helper.py PID press NAME [NAME...]   the names in order of preference
"""
import json
import sys

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

MAX_NODES = 4000
MAX_DEPTH = 40


def app_for(pid: int):
    desktop = Atspi.get_desktop(0)
    for index in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(index)
        if app and app.get_process_id() == pid:
            return app
    return None


def pressable(node) -> list:
    """Everything visible in the tree that can be activated, as (name, role, node)."""
    found, stack, seen = [], [(node, 0)], 0
    while stack and seen < MAX_NODES:
        current, depth = stack.pop()
        seen += 1
        try:
            action = current.get_action_iface()
            name = (current.get_name() or "").strip()
            if action and action.get_n_actions() > 0 and name and current.get_state_set().contains(Atspi.StateType.SHOWING):
                found.append((name, current.get_role_name(), current))
            if depth < MAX_DEPTH:
                count = current.get_child_count()
                stack.extend((current.get_child_at_index(i), depth + 1) for i in range(count - 1, -1, -1))
        except Exception:  # nodes vanish while the tree is being walked
            continue
    return found


def main() -> None:
    pid, mode, names = int(sys.argv[1]), sys.argv[2], [n.lower() for n in sys.argv[3:]]
    app = app_for(pid)
    if app is None:
        print(json.dumps({"error": "not on the accessibility bus"}))
        return
    items = pressable(app)
    if mode == "list":
        print(json.dumps({"items": [{"name": name, "role": role} for name, role, _ in items]}))
        return
    for wanted in names:
        for name, _, node in items:
            if name.lower() == wanted:
                print(json.dumps({"pressed": name if node.get_action_iface().do_action(0) else None}))
                return
    print(json.dumps({"pressed": None}))


if __name__ == "__main__":
    main()
