# -*- coding: utf-8 -*-
"""dedup_global.py —— 会后第 2 步：全局近似去重。

不同轮次的采集可能把同一段音频重复转写，且时间戳相差数分钟到数十分钟，
第 1 步的「时间窗去重」抓不到。这一步做全量、无序的近似去重（shingle + ratio）。

用法：
  python dedup_global.py
"""
import os
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import session_config  # noqa: E402

BASE = session_config.BASE
cfg = session_config.load()

SRC = os.path.join(BASE, cfg["merged_file"])
DST = os.path.join(BASE, cfg["final_file"])

PAT = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s*(.*)$")


def norm(s):
    """归一化：保留汉字。原实现只留 [a-z0-9]，中文行会变成空串
    → 长度 0 < 40，永远跳过比较，等于中文会议完全不去重。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", s.lower()).strip()


def shingles(s, k=5):
    """英文按词做 5-gram；中文没有空格，退化到按字符做 5-gram。"""
    t = s.split()
    if len(t) >= k:
        return {tuple(t[i:i + k]) for i in range(len(t) - k + 1)}
    core = s.replace(" ", "")
    if len(core) < k:
        return {tuple(core)} if core else set()
    return {tuple(core[i:i + k]) for i in range(len(core) - k + 1)}


def min_len_for(s):
    """中文 1 字≈英文 2~3 字符，长度门槛相应下调，否则短句永远不参与去重。"""
    return 22 if re.search(r"[\u4e00-\u9fff]", s) else 40


def main():
    if not os.path.exists(SRC):
        print("找不到 %s —— 请先跑 merge_transcripts.py" % SRC)
        return 1

    items = []
    for line in open(SRC, encoding="utf-8", errors="replace"):
        m = PAT.match(line.strip())
        if not m:
            continue
        hh, mm, ss, txt = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        if txt:
            items.append([int(hh) * 3600 + int(mm) * 60 + int(ss),
                          "%s:%s:%s" % (hh, mm, ss), txt])
    print("input entries: %d" % len(items))

    data = []
    for it in items:
        nt = norm(it[2])
        data.append((nt, shingles(nt), len(nt)))

    ORDER = sorted(range(len(items)), key=lambda i: -data[i][2])   # 长句优先保留
    removed = [False] * len(items)

    for ai in range(len(ORDER)):
        i = ORDER[ai]
        if removed[i]:
            continue
        ni, si, li = data[i]
        if li < min_len_for(ni):
            continue
        for aj in range(ai + 1, len(ORDER)):
            j = ORDER[aj]
            if removed[j]:
                continue
            nj, sj, lj = data[j]
            if lj < min_len_for(nj):
                break
            if abs(li - lj) > 0.5 * max(li, lj):
                continue
            if not (si & sj):
                continue
            if SequenceMatcher(None, ni, nj).ratio() >= 0.62:
                removed[j] = True

    kept = [it for k, it in enumerate(items) if not removed[k]]
    print("kept: %d   removed(global-dup): %d" % (len(kept), sum(removed)))

    with open(DST, "w", encoding="utf-8") as fh:
        fh.write("# 全文转写（最终版 · 已合并去重、时间有序） —— %s · %s\n"
                 % (cfg["code"], cfg["date"]))
        fh.write("# 条目数：%d\n\n" % len(kept))
        for sec, ts, txt in kept:
            fh.write("[%s] %s\n" % (ts, txt))
    print("WROTE %s" % DST)

    # 密度报告：找出「几乎没有内容」的时段（可能是漏录或静音幻觉段）
    buckets = {}
    for sec, ts, txt in kept:
        buckets[sec // 60] = buckets.get(sec // 60, 0) + 1
    if buckets:
        print("--- 密度（条/分钟） ---")
        for k in sorted(buckets):
            print("  %02d:%02d = %d" % (k // 60, k % 60, buckets[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
