# -*- coding: utf-8 -*-
"""用 _看板模板.html + 会议摘要-Day4.md + live_transcript.txt 生成 会议看板.html。

用法：
    python build_board.py              # 生成一次（打印结果）
    python build_board.py --quiet      # 供 live_watch.py 循环调用，静默模式

设计说明：
  看板的 HTML/CSS/JS 全部固定在模板里，本脚本只做两件事——
    ① 把 markdown 摘要塞进 @@CONTENT@@（由 LLM 整理的高质量双语摘要）
    ② 把实时转写尾部若干行塞进 @@LIVEFEED@@（JSON，由本地循环进程刷新）
  两层解耦：摘要层需要 LLM，最快按小时更新；实时层不需要任何模型，
  可以每 15 秒重建一次 —— 这样"实时"就不依赖定时任务了。

★ 三个必须守住的不变量（踩过坑，页面全白）：
  1) 模板里每个占位符 **只能出现一次**。
     原先 JS 里也写了 @@CONTENT@@ 的字面量做哨兵判断，str.replace 把整篇 markdown
     也塞进了 JS，字符串字面量被换行截断 → 整个 <script> 语法错误
     → 内容区永远空白，而且"占位符已替换"这类检查全都通过，看不出问题。
     模板里的哨兵因此改成拼接写法。
     ★ 注释里也不许写出那个字面量（我第一版注释就是这样被自检拦下的）。
  2) 生成后必须**真做一次 JS 语法校验**，不能只检查占位符是否被替换。
  3) 注入 JSON 前必须把 "</" 转义成 "<\\/"，否则 </script> 会提前截断脚本块。
"""
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = (os.environ.get("SEMINAR_DIR")
        or os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(BASE, "_看板模板.html")
def _pick_summary():
    """摘要文件自动探测：环境变量 SEMINAR_SUMMARY > 会议摘要-双语.md > 最新的 会议摘要*.md。"""
    forced = os.environ.get("SEMINAR_SUMMARY")
    if forced:
        return forced if os.path.isabs(forced) else os.path.join(BASE, forced)
    p = os.path.join(BASE, "会议摘要-双语.md")
    if os.path.exists(p):
        return p
    hits = sorted(glob.glob(os.path.join(BASE, "会议摘要*.md")),
                  key=os.path.getmtime, reverse=True)
    return hits[0] if hits else p


MD = _pick_summary()
TRANSCRIPT = os.path.join(BASE, "live_transcript.txt")
OUT = os.path.join(BASE, "会议看板.html")

PLACEHOLDER = "@@CONTENT@@"
LIVE_PLACEHOLDER = "@@LIVEFEED@@"

# 模板里必须原样保留的 JS 哨兵行
SENTINEL = 'if(raw.indexOf(PH)>=0||!/\\S/.test(raw)){'

# 实时原声面板最多展示多少行
LIVE_MAX_LINES = 120

TS_RE = re.compile(r"^\s*\[(\d{1,2}:\d{2}:\d{2})\]\s*")

# 逐字重复型幻觉（静音段 Whisper 会把同一 token 刷几十遍）
NOISE_SUBSTRINGS = (
    "字幕", "subtitle", "amara.org", "subscribe", "www.",
    "谢谢观看", "感谢观看", "下期再见",
)


def is_noise(text):
    """判断一行转写是否为静音/噪声段的废话，用于实时面板过滤。

    只做保守判断：宁可留下可疑行，也不要误杀真实发言。
    """
    core = TS_RE.sub("", text).strip()
    if not core:
        return True
    low = core.lower()
    for bad in NOISE_SUBSTRINGS:
        if bad in low:
            return True
    # 纯符号/纯空白
    plain = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", " ", core).strip()
    if not plain:
        return True
    toks = plain.split()
    if len(toks) >= 3:
        from collections import Counter
        c = Counter(t.lower() for t in toks)
        top_n = c.most_common(1)[0][1]
        # 例："AI, AI, AI, AI, AI" / "R&D, R&D, R&D" / "Models, Models"
        if top_n / len(toks) >= 0.6 and len(c) <= 2:
            return True
    return False


def read_live(limit=LIVE_MAX_LINES):
    """读 live_transcript.txt，返回 (payload_dict, ok)。

    payload 形如：
      {"generated":"10:05:12","total":312,"kept":286,"lastTs":"10:04:58",
       "lines":[{"t":"10:04:58","x":"..."}, ...]}   # 最新的在最前
    """
    payload = {"generated": datetime.datetime.now().strftime("%H:%M:%S"),
               "total": 0, "kept": 0, "lastTs": "—", "lines": []}
    if not os.path.exists(TRANSCRIPT):
        return payload, False

    raw_lines = []
    with open(TRANSCRIPT, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            s = ln.rstrip("\n").rstrip("\r")
            if not s.strip() or s.strip().startswith("#"):
                continue
            raw_lines.append(s)

    payload["total"] = len(raw_lines)

    kept, prev = [], None
    for s in raw_lines:
        if is_noise(s):
            continue
        if s == prev:                 # 连续重复行去重
            continue
        prev = s
        m = TS_RE.match(s)
        ts = m.group(1) if m else "—"
        txt = TS_RE.sub("", s).strip()
        kept.append({"t": ts, "x": txt})

    payload["kept"] = len(kept)
    if kept:
        payload["lastTs"] = kept[-1]["t"]
    # 最新的在最前面，用户一抬眼就看到当下
    payload["lines"] = list(reversed(kept[-limit:]))
    return payload, True


def find_node():
    """找一个可用的 node，用于 JS 语法校验。找不到就返回 None。"""
    p = shutil.which("node")
    if p:
        return p
    patterns = [
        os.path.expanduser("~/.workbuddy/binaries/node/versions/*/node.exe"),
        os.path.expanduser("~/.workbuddy/binaries/node/versions/*/bin/node"),
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        "/usr/local/bin/node",
        "/opt/homebrew/bin/node",
        "/usr/bin/node",
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat), reverse=True)
        if hits:
            return hits[0]
    return None


def check_js_syntax(html):
    """抽出主 <script> 块做语法校验。返回 (ok, 说明)；ok 为 None 表示跳过。"""
    blocks = re.findall(r"<script>([\s\S]*?)</script>", html)
    if not blocks:
        return False, "找不到主 <script> 块"
    node = find_node()
    if not node:
        return None, "未找到 node.exe，跳过 JS 语法校验"

    fd, tmp = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(blocks[-1])
        r = subprocess.run([node, "--check", tmp],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return True, "JS 语法 OK"
        msg = (r.stderr or r.stdout or "").strip().splitlines()
        return False, (msg[2] if len(msg) > 2 else (msg[0] if msg else "未知错误"))
    except Exception as e:
        return None, "JS 校验未执行：%s" % e
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    quiet = "--quiet" in argv

    def say(*a):
        if not quiet:
            print(*a)

    for p in (TPL, MD):
        if not os.path.exists(p):
            print("[构建失败] 找不到文件: %s" % p)
            return 1

    with open(TPL, "r", encoding="utf-8") as f:
        tpl = f.read()
    with open(MD, "r", encoding="utf-8") as f:
        md = f.read()
    live, transcript_ok = read_live()

    # ---- 不变量 1：两个占位符都必须恰好出现一次 ----
    problems = []
    for name, ph in (("摘要", PLACEHOLDER), ("实时原声", LIVE_PLACEHOLDER)):
        n = tpl.count(ph)
        if n == 0:
            problems.append("模板里找不到%s占位符" % name)
        elif n > 1:
            problems.append("模板里%s占位符出现 %d 次（必须恰好 1 次）——"
                            "多出来的那处会被替换成整块内容，撑坏 <script> 块" % (name, n))
    if problems:
        print("[构建失败] 模板占位符异常：")
        for p in problems:
            print("  - %s" % p)
        return 1

    # 兜底：内容里若出现 </script> 会提前截断脚本块
    md = md.replace("</script>", "<\\/script>")
    live_json = json.dumps(live, ensure_ascii=False).replace("</", "<\\/")

    html = tpl.replace(PLACEHOLDER, md, 1).replace(LIVE_PLACEHOLDER, live_json, 1)

    # ---- 不变量 2：结构 + 语法自检 ----
    problems = []
    if SENTINEL not in html:
        problems.append("JS 哨兵行丢失或已被改写")
    # 模板里有 3 个 script 块：src(摘要) / live(实时 JSON) / 主脚本
    if html.count("<script") != 3 or html.count("</script>") != 3:
        problems.append("script 标签数量异常（<script>=%d, </script>=%d）"
                        % (html.count("<script"), html.count("</script>")))
    if problems:
        print("[构建失败] 生成结果自检不通过：")
        for p in problems:
            print("  - %s" % p)
        return 1

    ok, note = check_js_syntax(html)
    if ok is False:
        print("[构建失败] JS 语法校验不通过：%s" % note)
        print("           已放弃写入，原 %s 保持不动。" % os.path.basename(OUT))
        return 1

    # 先写 .tmp 再覆盖：避开网盘/同步盘对 rename 语义的拦截
    # ★ tmp 名带上 PID：万一同时跑了两个进程，不会互相踩坏临时文件
    tmp = "%s.%d.tmp" % (OUT, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        os.replace(tmp, OUT)
    except OSError:
        import shutil
        shutil.copyfile(tmp, OUT)
        os.remove(tmp)

    say("[看板已更新] %s" % OUT)
    say("  摘要正文: %d 字符 ｜ 实时原声: %d 行（原始 %d 行，最后一条 %s）"
        % (len(md), len(live["lines"]), live["total"], live["lastTs"]))
    say("  生成时间: %s ｜ JS 校验: %s"
        % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note))
    if not transcript_ok:
        say("  提示：live_transcript.txt 不存在（转写尚未启动），实时面板为空。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
