# -*- coding: utf-8 -*-
"""诊断：实时转写的滞后量与追赶速度。

只看时间戳，不加载任何模型（避免自己抢占 CPU、影响被测对象）。
用法： python _lag.py [观测秒数，默认 90]
"""
import datetime
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TS = re.compile(r"\[(\d{1,2}):(\d{2}):(\d{2})\]")

# 工作目录：SEMINAR_DIR > 本脚本所在目录
_BASE = (os.environ.get("SEMINAR_DIR")
         or os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("SEMINAR_TRANSCRIPT") or os.path.join(_BASE, "live_transcript.txt")


def secs(h, m, s):
    return h * 3600 + m * 60 + s


def snapshot():
    body = [l.rstrip() for l in open(PATH, encoding="utf-8", errors="replace")
            if l.strip() and not l.startswith("#")]
    last_ts = None
    for l in reversed(body):
        hit = TS.search(l)
        if hit:
            last_ts = (int(hit.group(1)), int(hit.group(2)), int(hit.group(3)))
            break
    return len(body), last_ts


def hms(t):
    return "%02d:%02d:%02d" % t


def main():
    span = int(sys.argv[1]) if len(sys.argv) > 1 else 90

    n1, t1 = snapshot()
    w1 = datetime.datetime.now()
    print()
    print("  T0  %s  行数=%d  转写末尾时间戳=%s" % (w1.strftime("%H:%M:%S"), n1, hms(t1)))
    print("      此刻滞后 %.1f 分钟" % ((secs(w1.hour, w1.minute, w1.second) - secs(*t1)) / 60.0))
    print()
    print("  观测 %d 秒（期间不跑任何重活）…" % span)
    time.sleep(span)

    n2, t2 = snapshot()
    w2 = datetime.datetime.now()
    d = secs(*t2) - secs(*t1)
    lag = secs(w2.hour, w2.minute, w2.second) - secs(*t2)

    print("  T1  %s  行数=%d  转写末尾时间戳=%s" % (w2.strftime("%H:%M:%S"), n2, hms(t2)))
    print()
    print("  ── 结论 ──")
    print("  内容推进        : %d 秒 / %d 秒  →  %.2fx 实时" % (d, span, d / float(span)))
    print("  新增行数        : %d" % (n2 - n1))
    print("  当前滞后        : %.1f 分钟" % (lag / 60.0))
    if d / float(span) >= 0.98:
        verdict = "基本跟上实时（滞后会稳定在当前水平）"
    elif d / float(span) >= 0.7:
        verdict = "略微落后（滞后会缓慢变大，但不会失控）"
    else:
        verdict = "明显落后（滞后在持续变大，需减负或等它慢慢追）"
    print("  判断            : %s" % verdict)
    print()
    print("  说明：音频存档 wav 是完整实时的，转写滞后 ≠ 内容丢失。")
    print("        即使这里追不上，会后也能用 analyze_audio.py 对 wav 全量重转。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
