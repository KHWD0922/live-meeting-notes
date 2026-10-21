# -*- coding: utf-8 -*-
"""new_session.py —— 新一场会议的「开工准备」，一条命令把工作目录切换到位。

做四件事（与实战版 _day_switch.py 同源，但议程来自 议程配置.json，不再硬编码）：

  1) 按配置重写 _看板模板.html 的 标题 / 主标题 / 副标题 / AGENDA 议程数组
  2) 重建 会议摘要-双语.md 骨架（按议程预置每个场次小节，会中直接往里填）
  3) 重置 _note_state.json（否则会带着上一场的整理进度，导致「更新摘要」算错新增行）
  4) 跑 build_board.py 生成一次 会议看板.html，并完成 JS 语法校验

用法：
  python new_session.py                 # 按 议程配置.json 执行
  python new_session.py --check         # 只做环境自检，不改任何文件
  python new_session.py --dry-run       # 只打印将要做的改动
  python new_session.py --keep-summary  # 不重建摘要（仅换模板 + 重置进度 + 重建看板）

★ 不变量（踩过坑，破坏了页面会整片空白）：
  - 模板里 @@CONTENT@@ 与 @@LIVEFEED@@ 各只能出现 1 次 —— 写完重新计数校验；
  - AGENDA 数组必须能被正则整体替换，替换后 var AGENDA= 只能剩 1 处；
  - JS 哨兵行必须原样保留。
"""
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import session_config  # noqa: E402

BASE = session_config.BASE
PY = sys.executable

SENTINEL = 'if(raw.indexOf(PH)>=0||!/\\S/.test(raw)){'


def paths(cfg):
    return {
        "tpl": os.path.join(BASE, cfg["board_template"]),
        "md": os.path.join(BASE, cfg["summary_file"]),
        "state": os.path.join(BASE, "_note_state.json"),
        "bak": os.path.join(BASE, "_摘要上一份-备份.md"),
        "board": os.path.join(BASE, "build_board.py"),
    }


# ---------------------------------------------------------------- 骨架
def md_skeleton(cfg):
    L = []
    L.append("# %s · 实时摘要（中英对照）" % cfg["code"])
    L.append("")
    if cfg.get("agenda_note"):
        L.append("> **会议**：%s ｜ %s" % (cfg.get("title_en", ""), cfg["agenda_note"]))
    else:
        L.append("> **会议**：%s" % cfg.get("title_en", ""))
    if cfg.get("span"):
        L.append("> **议程区间**：%s" % cfg["span"])
    L.append("")
    L.append("> **最后更新：** — ｜ **已整理至：** — ｜ **当前议题：** 尚未开始")
    L.append("> 本文件由「实时会议笔记整理」流程维护；会中随时说「更新摘要」即可立即刷新。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 一页摘要 · Executive Summary")
    L.append("")
    L.append("（会议开始后填写：全场主线 3–5 条，中英对照）")
    L.append("")
    L.append("---")
    L.append("")
    for s in cfg["sessions"]:
        L.append("## %s · %s" % (s["span"], s["en"]))
        if s.get("zh"):
            L.append("**中文标题：** %s" % s["zh"])
        L.append("**讲者 / Speakers:** %s" % s["speakers"])
        L.append("")
        L.append("*待整理 · Pending*")
        L.append("")
        L.append("---")
        L.append("")
    L.append("## 行动项 · Action Items")
    L.append("")
    L.append("| 编号 | 事项 | 负责 | 时间点 | 来源 |")
    L.append("|---|---|---|---|---|")
    L.append("| — | （待整理） | — | — | — |")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 存疑与待确认 · Open Questions")
    L.append("")
    L.append("- （待整理）")
    L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- 自检
def find_node():
    """跨平台找 node：先 which，再常见安装位置。"""
    p = shutil.which("node")
    if p:
        return p
    for pat in (
        os.path.expanduser("~/.workbuddy/binaries/node/versions/*/node.exe"),
        os.path.expanduser("~/.workbuddy/binaries/node/versions/*/bin/node"),
        r"C:\Program Files\nodejs\node.exe",
        r"C:\Program Files (x86)\nodejs\node.exe",
        "/usr/local/bin/node",
        "/opt/homebrew/bin/node",
        "/usr/bin/node",
    ):
        hits = sorted(glob.glob(pat), reverse=True)
        if hits:
            return hits[0]
    return None


def check_env(cfg):
    """返回 (ok, warn, bad)。bad 才会中止；warn 只提醒（例如模型还没下载）。"""
    ok, warn, bad = [], [], []

    def probe(label, path, kind="file"):
        exists = os.path.isdir(path) if kind == "dir" else os.path.exists(path)
        (ok if exists else bad).append("%s: %s" % (label, path))

    probe("看板模板", os.path.join(BASE, cfg["board_template"]))
    for label, names in (("转写引擎", ("live_capture.py",)),
                         ("看板守护", ("live_watch.py",)),
                         ("看板构建", ("build_board.py",)),
                         ("整理进度工具", ("state.py",)),
                         ("启动器", ("启动实时转写.cmd", "启动实时转写.sh")),
                         ("停止器", ("停止转写.cmd", "停止转写.sh")),
                         ("看板刷新启动器", ("启动看板刷新.cmd", "启动看板刷新.sh"))):
        if not any(os.path.exists(os.path.join(BASE, n)) for n in names):
            bad.append("%s 缺失: %s（先跑 init_project.py 铺设工作目录）"
                       % (label, " / ".join(names)))

    gl = os.path.join(BASE, "术语表.txt")
    (ok if os.path.exists(gl) else warn).append("术语表: %s" % gl)

    models = os.path.join(BASE, "models")
    if os.path.isdir(models) and any(os.scandir(models)):
        n = sum(len(f) for _, _, f in os.walk(models))
        ok.append("Whisper 模型: %s（%d 个文件）" % (models, n))
    else:
        warn.append("Whisper 模型未就绪: %s —— 不影响切换/看板，"
                    "首次录音会自动下载约 460MB" % models)

    node = find_node()
    if node:
        ok.append("node（看板 JS 校验用）: %s" % node)
    else:
        warn.append("node 未找到 —— 看板仍会生成，只是跳过 JS 语法校验")

    for junk in ("STOP_CAPTURE", "_watch.lock"):
        p = os.path.join(BASE, junk)
        (bad if os.path.exists(p) else ok).append(
            ("残留标记需清理: %s" % p) if os.path.exists(p) else ("无残留: %s" % junk))
    return ok, warn, bad


# ---------------------------------------------------------------- 主流程
def patch_template(cfg, tpl_path, dry=False):
    if not os.path.exists(tpl_path):
        return ["找不到模板: %s" % tpl_path]
    with io.open(tpl_path, "r", encoding="utf-8") as f:
        tpl = f.read()

    rows = session_config.board_rows(cfg)
    new_agenda = "var AGENDA=[\n" + ",\n".join(
        ' ["%s","%s","%s"]' % (t, e.replace('"', '\\"'), c) for t, e, c in rows) + "\n];"

    tpl, n_ag = re.subn(r"var AGENDA=\[[\s\S]*?\];", new_agenda, tpl, count=1)
    tpl, n_ti = re.subn(r"<title>[^<]*</title>", "<title>%s</title>" % cfg["board_title"], tpl, count=1)
    tpl, n_h1 = re.subn(r"<h1>[^<]*</h1>", "<h1>%s</h1>" % cfg["board_h1"], tpl, count=1)
    tpl, n_sub = re.subn(r'<div class="sub">[^<]*</div>',
                         '<div class="sub">%s</div>' % cfg["subtitle"], tpl, count=1)

    problems = []
    for label, n in (("AGENDA 数组", n_ag), ("<title>", n_ti), ("<h1>", n_h1), ('<div class="sub">', n_sub)):
        if n != 1:
            problems.append("%s 替换次数 = %d（应为 1）" % (label, n))
    if tpl.count("@@CONTENT@@") != 1 or tpl.count("@@LIVEFEED@@") != 1:
        problems.append("占位符计数异常 CONTENT=%d LIVEFEED=%d"
                        % (tpl.count("@@CONTENT@@"), tpl.count("@@LIVEFEED@@")))
    if tpl.count("var AGENDA=") != 1:
        problems.append("var AGENDA 出现 %d 次（应为 1）" % tpl.count("var AGENDA="))
    if SENTINEL not in tpl:
        problems.append("JS 哨兵行丢失或被改写")
    if problems:
        return problems

    if not dry:
        tmp = tpl_path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(tpl)
        os.replace(tmp, tpl_path)
    return []


def main():
    argv = sys.argv[1:]
    cfg = session_config.load()
    P = paths(cfg)

    print("工作目录 : %s" % BASE)
    print("议程配置 : %s" % (cfg["_source"] or "(未找到 议程配置.json，使用默认值)"))
    print("会话     : %s · %s（%s）" % (cfg["code"], cfg["date"], cfg["weekday"]))
    print("议题数   : %d" % len(cfg["sessions"]))
    print()

    if "--check" in argv:
        ok, warn, bad = check_env(cfg)
        print("=== 环境自检 ===")
        for s in ok:
            print("  [OK]   " + s)
        for s in warn:
            print("  [--]   " + s)
        for s in bad:
            print("  [!!]   " + s)
        return 0 if not bad else 1

    dry = "--dry-run" in argv
    keep_summary = "--keep-summary" in argv

    ok, warn, bad = check_env(cfg)
    for s in warn:
        print("  [提醒] " + s)
    if bad:
        print("环境自检有问题，先处理：")
        for s in bad:
            print("  [!!] " + s)
        return 1

    if not cfg["sessions"]:
        print("[中止] 议程配置里没有任何议题（sessions 为空）。")
        return 1

    # 1) 模板
    probs = patch_template(cfg, P["tpl"], dry=dry)
    if probs:
        print("[切换失败] 模板自检不通过，未写入：")
        for p in probs:
            print("  - " + p)
        return 1
    print("[1/4] 看板模板已切换到 %s（%s），AGENDA %d 项%s"
          % (cfg["code"], cfg["date"], len(cfg["sessions"]), "（dry-run）" if dry else ""))

    # 2) 摘要骨架
    if keep_summary:
        print("[2/4] 跳过摘要重建（--keep-summary）")
    else:
        if os.path.exists(P["md"]) and os.path.getsize(P["md"]) > 0:
            with io.open(P["md"], "r", encoding="utf-8") as f:
                old = f.read()
            if not dry:
                with io.open(P["bak"], "w", encoding="utf-8", newline="\n") as f:
                    f.write(old)
            print("       原有摘要已备份为 _摘要上一份-备份.md（%d 字符）" % len(old))
        sk = md_skeleton(cfg)
        if not dry:
            with io.open(P["md"], "w", encoding="utf-8", newline="\n") as f:
                f.write(sk)
        print("[2/4] 摘要骨架已生成（%d 字符，%d 个场次小节）%s"
              % (len(sk), len(cfg["sessions"]), "（dry-run）" if dry else ""))

    # 3) 进度重置
    st = {"covered_until": "", "covered_lines": 0, "cutoff": None,
          "updated_at": "reset by new_session.py -> %s" % cfg["date"]}
    if not dry:
        with io.open(P["state"], "w", encoding="utf-8", newline="\n") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    print("[3/4] 整理进度已重置（covered_lines=0，避免带入上一场进度）%s"
          % ("（dry-run）" if dry else ""))

    # 4) 重建看板
    if dry:
        print("[4/4] dry-run：跳过看板构建")
        return 0
    r = subprocess.run([PY, P["board"]], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip() or (r.stderr or "").strip()
    for ln in out.splitlines():
        print("       " + ln)
    print("[4/4] 看板构建 退出码 = %d" % r.returncode)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
