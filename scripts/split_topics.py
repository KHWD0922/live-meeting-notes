# -*- coding: utf-8 -*-
"""split_topics.py —— 会后第 3 步：把「全文转写-最终.txt」重新按议题切片。

与 merge_transcripts.py 的区别：那一步在合并时就切了；这一步用于
**人工精修过 全文转写-最终.txt 之后**再切一遍（实战就是这么做的），
并且带一层「短噪声行过滤」，让切片更干净。

用法：
  python split_topics.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import session_config  # noqa: E402

BASE = session_config.BASE
cfg = session_config.load()

SRC = os.path.join(BASE, cfg["final_file"])
OUTDIR = os.path.join(BASE, cfg["topics_dir"])
os.makedirs(OUTDIR, exist_ok=True)

PAT = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s*(.*)$")

# 常见英文技术/商业缩写，仅用于识别「整句都是术语堆砌」的噪声行。
# 按需增补即可；不写具体的公司名或客户名。
TERMS = set("""ai llm rag mlops genai agent agents copilot api sdk gpu cpu roi tco
cto cio ciso ceo cmdb itsm devops devsecops cloud edge data model models token tokens
cybersecurity firewall owasp mitre soc zero trust platform pipeline""".split())

CJK = re.compile(r"[\u4e00-\u9fff]")


def is_junk(txt):
    """判断一行是否为静音/噪声段的废话。

    ★ 2026-10-21 修正：原实现只数 `[A-Za-z&']+` 词元，
      中文行词元数为 0 → 一律判为垃圾 → **中文会议会被整场清空**。
      现在中文行单独判定：只要含 2 个以上汉字就保留。
    """
    if CJK.search(txt):
        # 中文（含中英混排）：汉字数量是可靠判据
        han = len(CJK.findall(txt))
        if han >= 2:
            return False
        # 只剩零星汉字或纯符号，交给下面的英文规则兜底
        txt = CJK.sub(" ", txt)

    toks = re.findall(r"[A-Za-z&']+", txt)
    wc = len(toks)
    if wc < 4:
        return True
    longw = [t for t in toks if len(t) >= 5]
    if len(longw) < 2 and wc < 20:
        return True
    low = [t.lower() for t in toks]
    in_term = sum(1 for t in low if t in TERMS)
    if wc < 16 and in_term / wc >= 0.75:
        return True
    return False


def main():
    if not os.path.exists(SRC):
        print("找不到 %s —— 请先跑 merge_transcripts.py / dedup_global.py" % SRC)
        return 1

    items = []
    for line in open(SRC, encoding="utf-8", errors="replace"):
        m = PAT.match(line.strip())
        if not m:
            continue
        hh, mm, ss, txt = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        if txt:
            items.append((int(hh) * 3600 + int(mm) * 60 + int(ss),
                          "%s:%s:%s" % (hh, mm, ss), txt))
    print("输入条目: %d" % len(items))

    covered = set()
    for s in cfg["sessions"]:
        rows = [it for it in items if s["start_sec"] <= it[0] < s["end_sec"]]
        clean = [it for it in rows if not is_junk(it[2])]
        for it in rows:
            covered.add(it[1])
        with open(os.path.join(OUTDIR, "%s.txt" % s["id"]), "w", encoding="utf-8") as fh:
            fh.write("# %s | %s | %s | %s\n# %s\n\n"
                     % (s["id"], s["span"], s["en"], s["speakers"], s["zh"]))
            for sec, ts, txt in clean:
                fh.write("[%s] %s\n" % (ts, txt))
        print("TOPIC %-4s %-13s raw=%4d clean=%4d chars=%7d"
              % (s["id"], s["span"], len(rows), len(clean),
                 sum(len(x[2]) for x in clean)))

    gap = [it for it in items if it[1] not in covered]
    gp = os.path.join(BASE, cfg["gap_file"])
    with open(gp, "w", encoding="utf-8") as fh:
        fh.write("# 未归入议题的条目（茶歇 / 午餐 / 空档）\n\n")
        for sec, ts, txt in gap:
            fh.write("[%s] %s\n" % (ts, txt))
    print("GAP: %d  ->  %s" % (len(gap), gp))
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
