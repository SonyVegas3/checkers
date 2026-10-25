"""Scratch: where the coin-offer dialog is opened and what the amount means."""

import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

path = pathlib.Path(".browser-profile/Default/Cache/Cache_Data/f_00000d")
data = path.read_bytes().decode("utf-8", "ignore")

for match in list(re.finditer("coin_offer", data))[:12]:
    start = max(0, match.start() - 320)
    print(f"\n--- @{match.start()}")
    print(" ".join(data[start:match.start() + 420].split()))
