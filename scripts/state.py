"""更新 _note_state.json 的 covered_until。

用法：
  python state.py                # 取转写文件最后一条有效行
  python state.py 09:24:26       # 只取时间戳 <= 09:24:26 的最后一条有效行
                                 # （用于「已整理到某时刻」的精确标记，避免跳过未整理内容）
"""
import datetime
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = (os.environ.get("SEMINAR_DIR")
        or os.path.dirname(os.path.abspath(__file__)))
TP = os.path.join(BASE, "live_transcript.txt")
SP = os.path.join(BASE, "_note_state.json")

cutoff = sys.argv[1].strip() if len(sys.argv) > 1 else None
TS = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]")

lines = []
with open(TP, "r", encoding="utf-8", errors="replace") as f:
    for ln in f:
        s = ln.rstrip("\n").rstrip("\r")
        if not s.strip() or s.strip().startswith("#"):
            continue
        if cutoff:
            m = TS.match(s.strip())
            if not m or m.group(1) > cutoff:
                continue
        lines.append(s)

last = lines[-1] if lines else ""

state = {
    "covered_until": last,
    "covered_lines": len(lines),
    "cutoff": cutoff,
    "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
}
with open(SP, "w", encoding="utf-8") as f:
    json.dump(state, f, ensure_ascii=False, indent=2)

print("cutoff        :", cutoff)
print("covered_lines :", len(lines))
print("covered_until :", last[:150])
print("updated_at    :", state["updated_at"])
