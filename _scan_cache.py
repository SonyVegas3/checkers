"""Scratch: is init_play global, and how is an incoming rematch accepted?"""

import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

root = pathlib.Path(".browser-profile/Default/Cache/Cache_Data")
patterns = ("init_play =", "init_play=", "rm-offer", "rematch_accept", "rematch_watch")
for path in sorted(root.glob("*")):
    if not path.is_file():
        continue
    data = path.read_bytes().decode("utf-8", "ignore")
    for pattern in patterns:
        hits = list(re.finditer(re.escape(pattern), data))
        if not hits:
            continue
        print(f"\n===== {path.name} :: {pattern} ({len(hits)}) =====")
        for match in hits[:2]:
            print(" ".join(data[max(0, match.start() - 500):match.start() + 700].split()))
