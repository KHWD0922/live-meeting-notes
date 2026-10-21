# -*- coding: utf-8 -*-
"""merge_transcripts.py —— 会后第 1 步：把多轮转写合并成一份时间有序、去重的全文，
并按议程边界切成逐议题切片。

实战来源：某场会议用 4 轮转写合并（原始 1600+ 条）。算法未改，只把
「文件清单」与「议题边界」改成读 议程配置.json。

用法：
  python merge_transcripts.py
  python merge_transcripts.py --window 10 --lcs 40 --ratio 0.62
"""
import glob
import os
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import session_config  # noqa: E402

BASE = session_config.BASE
cfg = session_config.load()

PAT = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s*(.*)$")


def arg_num(flag, default):
    if flag in sys.argv:
        try:
            return float(sys.argv[sys.argv.index(flag) + 1])
        except (IndexError, ValueError):
            pass
    return default


WINDOW = arg_num("--window", 10)      # 秒：只跟前后这么多秒内的条目比
LCS_MIN = arg_num("--lcs", 40)        # 公共子串阈值（字符）
RATIO_MIN = arg_num("--ratio", 0.62)  # 或整体相似度阈值


def collect_files():
    if cfg.get("transcript_files"):
        return list(cfg["transcript_files"])
    files = sorted(glob.glob(os.path.join(BASE, cfg["transcript_archive_glob"])))
    files = [os.path.basename(p) for p in files]
    cur = cfg["transcript_file"]
    if cur not in files:
        files.append(cur)
    return files


def read_entries(files):
    entries, report = [], []
    for f in files:
        p = os.path.join(BASE, f)
        if not os.path.exists(p):
            report.append("MISSING %s" % f)
            continue
        n = 0
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = PAT.match(line.strip())
                if not m:
                    continue
                hh, mm, ss, txt = m.group(1), m.group(2), m.group(3), m.group(4).strip()
                if not txt:
                    continue
                entries.append((int(hh) * 3600 + int(mm) * 60 + int(ss),
                                "%s:%s:%s" % (hh, mm, ss), txt))
                n += 1
        report.append("READ %-46s %5d lines" % (f, n))
    return entries, report


CJK = re.compile(r"[\u4e00-\u9fff]")


def norm(s):
    """归一化：保留汉字。★ 2026-10-21 修正：原正则 `[^a-z0-9]` 会把中文全部抹成空串，
    导致中文条目永远进不了去重比较（既不误删，也永远去不掉重复）。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s.lower()).strip()


def lcs_min_for(s):
    """中文信息密度高，40 字的公共子串在中文里几乎等于整段相同，阈值要下调。"""
    return 24 if CJK.search(s) else LCS_MIN


def lcs_len(a, b):
    return max((bl.size for bl in SequenceMatcher(None, a, b).get_matching_blocks()), default=0)


def dedup(entries):
    kept, dropped = [], 0
    for sec, ts, txt in entries:
        nt = norm(txt)
        dup = False
        for k in range(len(kept) - 1, -1, -1):
            ksec, kts, ktxt, knt = kept[k]
            if sec - ksec > WINDOW:
                break
            if not knt or not nt:
                continue
            if (lcs_len(nt, knt) >= lcs_min_for(nt)
                    or SequenceMatcher(None, nt, knt).ratio() >= RATIO_MIN):
                dup = True
                if len(nt) > len(knt):        # 保留信息量更大的那条
                    kept[k] = (sec, ts, txt, nt)
                dropped += 1
                break
        if not dup:
            kept.append((sec, ts, txt, nt))
    kept.sort(key=lambda x: (x[0], x[1]))
    return kept, dropped


def main():
    files = collect_files()
    print("会话：%s · %s ｜ 议题 %d 个" % (cfg["code"], cfg["date"], len(cfg["sessions"])))
    print("--- 输入文件 ---")
    entries, report = read_entries(files)
    for r in report:
        print("  " + r)
    entries.sort(key=lambda x: (x[0], x[1]))
    print("TOTAL raw entries: %d" % len(entries))
    if not entries:
        print("没有可用条目，退出。")
        return 1

    kept, dropped = dedup(entries)
    print("KEPT: %d   DROPPED(dup): %d" % (len(kept), dropped))

    merged = os.path.join(BASE, cfg["merged_file"])
    with open(merged, "w", encoding="utf-8") as fh:
        fh.write("# 全文转写（合并去重） —— %s · %s\n" % (cfg["code"], cfg["date"]))
        fh.write("# 来源：%d 个转写文件合并，按时间戳排序，剔除重复段\n" % len(files))
        fh.write("# 条目数：%d（原始 %d，去重丢弃 %d）\n\n" % (len(kept), len(entries), dropped))
        for sec, ts, txt, nt in kept:
            fh.write("[%s] %s\n" % (ts, txt))
    print("WROTE %s" % merged)

    # ---- 按议题切片 ----
    tdir = os.path.join(BASE, cfg["topics_dir"])
    os.makedirs(tdir, exist_ok=True)
    covered = [False] * len(kept)
    for s in cfg["sessions"]:
        rows = []
        for i, e in enumerate(kept):
            if s["start_sec"] <= e[0] < s["end_sec"]:
                rows.append(e)
                covered[i] = True
        fp = os.path.join(tdir, "%s.txt" % s["id"])
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write("# %s | %s | %s | %s\n" % (s["id"], s["span"], s["en"], s["speakers"]))
            fh.write("# %s\n\n" % s["zh"])
            for sec, ts, txt, nt in rows:
                fh.write("[%s] %s\n" % (ts, txt))
        print("TOPIC %-4s %-13s %5d entries %8d chars"
              % (s["id"], s["span"], len(rows), sum(len(r[2]) for r in rows)))

    gap = [e for i, e in enumerate(kept) if not covered[i]]
    gp = os.path.join(BASE, cfg["gap_file"])
    with open(gp, "w", encoding="utf-8") as fh:
        fh.write("# 未归入任何议题的条目（茶歇 / 午餐 / 空档 / 溢出）\n\n")
        for sec, ts, txt, nt in gap:
            fh.write("[%s] %s\n" % (ts, txt))
    print("GAP entries: %d  ->  %s" % (len(gap), gp))
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
