# -*- coding: utf-8 -*-
"""init_project.py —— 为一整场会议铺设一个全新工作目录（复用入口）。

把技能里的引擎、模板、启动器一次性铺到目标目录，之后那个目录就是
一个自给自足的「会场录音工作台」：双击 .cmd 即可录音、看板、停止。

用法：
  python init_project.py "D:\\Meetings\\2026-11-05 客户大会"
  python init_project.py <目录> --refresh        # 只重铺脚本（引擎升级后同步）
  python init_project.py <目录> --models "D:\\...\\models"   # 复用已有 Whisper 模型（建 junction，不复制）
  python init_project.py <目录> --no-models      # 不建模型目录，首次录音自动下载

铺完后的目录：
  <目标>\\
    live_capture.py / launcher.py / live_watch.py / build_board.py / state.py ...
    启动实时转写.cmd  启动看板刷新.cmd  停止转写.cmd  麦克风实测.cmd
    _看板模板.html    术语表.txt        议程配置.json
    会议摘要-双语.md（骨架，由 new_session.py 生成）
    models\\（junction 到共享模型目录）
"""
import io
import os
import shutil
import subprocess
import sys

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SC = os.path.join(SKILL, "scripts")
AS = os.path.join(SKILL, "assets")

SCRIPT_FILES = [
    "live_capture.py", "launcher.py", "live_watch.py", "build_board.py",
    "state.py", "session_config.py", "new_session.py", "init_project.py",
    "mic_check.py", "analyze_audio.py", "lag_check.py", "preflight.py",
    "merge_transcripts.py", "dedup_global.py", "split_topics.py",
    "build_delivery.py", "check_board.js",
]

# 资产 -> 目标文件名
ASSET_FILES = [
    ("看板模板.html", "_看板模板.html"),
    ("术语表-模板.txt", "术语表.txt"),
    ("议程配置-单场.json", "议程配置.json"),      # 默认单场模式，开箱即用
    ("议程配置-示例.json", "议程配置-示例.json"),   # 有议程时照着改
    ("议程-模板.md", "议程.md"),
]

LAUNCHERS = [
    ("启动实时转写", "capture", "实时转写"),
    ("启动看板刷新", "board", "看板刷新"),
    ("停止转写", "stop", "停止转写"),
    ("麦克风实测", "check", "麦克风实测"),
    ("环境体检", "preflight", "环境体检"),
]

# 可复用的 Whisper 模型位置（按优先级）；都没有就让首次运行自动下载
def model_candidates():
    out = []
    env = os.environ.get("WHISPER_MODELS")
    if env:
        out.append(os.path.expanduser(env))
    out.append(os.path.join(SKILL, "models"))
    out.append(os.path.expanduser("~/.cache/whisper-models"))
    return out


CMD_TEMPLATE = u"""@echo off
chcp 65001 >nul
title {title}
set PY={py}
set SEMINAR_DIR=%~dp0

if not exist "%PY%" (
  echo [ERROR] Python not found:
  echo   %PY%
  echo Please fix the PY path in this .cmd file.
  pause
  exit /b 1
)

"%PY%" "%~dp0{script}

echo.
echo ---- finished ----
pause
"""

SH_TEMPLATE = u"""#!/usr/bin/env bash
# 由 init_project.py 生成；macOS / Linux 用
set -e
cd "$(dirname "$0")"
export SEMINAR_DIR="$PWD"
PY="${{PY:-python3}}"
exec "$PY" {script}
"""


def find_python():
    """一律用当前解释器：这是唯一在别人机器上也可信的答案。"""
    return sys.executable


def find_models():
    for c in model_candidates():
        if os.path.isdir(c) and os.listdir(c):
            return c
    return None


def _decode(b):
    """cmd.exe 的输出是本地代码页（中文 Windows 为 GBK），不能按 UTF-8 硬解。"""
    if not b:
        return ""
    for enc in ("mbcs", "utf-8", "gbk"):
        try:
            return b.decode(enc, "replace")
        except (LookupError, AttributeError):
            continue
    return repr(b)


def link_models(link, target):
    """把共享模型接进来：Windows 用 junction，类 Unix 用 symlink，都不行就复制。"""
    target = os.path.normpath(target)
    link = os.path.normpath(link)
    if os.path.exists(link):
        if os.path.isdir(link) and not os.path.islink(link) and os.listdir(link):
            return "已存在，跳过"
        try:
            os.rmdir(link) if os.path.isdir(link) else os.remove(link)
        except OSError:
            return "已存在且无法替换，跳过"

    if os.name == "nt":
        # 必须用反斜杠：mklink 不接受正斜杠路径（会报「无效语法」）
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                           capture_output=True)
        if r.returncode == 0:
            return "OK (junction)"
        err = _decode(r.stderr or r.stdout).strip()[:120]
    else:
        try:
            os.symlink(target, link, target_is_directory=True)
            return "OK (symlink)"
        except OSError as e:
            err = str(e)[:120]

    try:
        shutil.copytree(target, link)
        return "OK (已复制 460MB；未能建链接: %s)" % err
    except Exception as e:
        return "失败: %s / 复制也失败: %s" % (err, str(e)[:80])


def copy_file(src, dst, force=True):
    if os.path.exists(dst) and not force:
        return "跳过"
    shutil.copy2(src, dst)
    return "OK"


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if not argv:
        print(__doc__)
        return 2

    dest = os.path.abspath(argv[0])
    refresh = "--refresh" in flags
    no_models = "--no-models" in flags
    model_from = None
    if "--models" in flags:
        i = sys.argv.index("--models")
        if i + 1 < len(sys.argv):
            model_from = sys.argv[i + 1]

    if not os.path.isdir(dest):
        os.makedirs(dest)
        print("[新建] 工作目录 %s" % dest)
    else:
        print("[复用] 工作目录已存在 %s" % dest)

    py = find_python()
    rep = []

    # 1) 引擎脚本
    for name in SCRIPT_FILES:
        src = os.path.join(SC, name)
        if not os.path.exists(src):
            rep.append("  [缺失] %s（技能不完整）" % name)
            continue
        st = copy_file(src, os.path.join(dest, name))
        if not refresh or st == "OK":
            rep.append("  [%s] %s" % (st, name))
    print("[1/4] 引擎脚本已铺设（%d 个）" % len(SCRIPT_FILES))

    # 2) 资产与启动器
    for src_name, dst_name in ASSET_FILES:
        src = os.path.join(AS, src_name)
        if not os.path.exists(src):
            rep.append("  [缺失] 资产 %s" % src_name)
            continue
        dst = os.path.join(dest, dst_name)
        if os.path.exists(dst) and refresh:
            rep.append("  [保留] %s（已存在，未覆盖）" % dst_name)
            continue
        copy_file(src, dst)
        rep.append("  [OK] %s" % dst_name)

    for name, mode, title in LAUNCHERS:
        if mode == "preflight":
            cmd_target, sh_target = 'preflight.py"', "preflight.py"
        else:
            cmd_target, sh_target = 'launcher.py" %s' % mode, "launcher.py %s" % mode

        cmd_path = os.path.join(dest, name + ".cmd")
        sh_path = os.path.join(dest, name + ".sh")
        for path, is_cmd in ((cmd_path, True), (sh_path, False)):
            if os.path.exists(path) and refresh:
                rep.append("  [保留] %s（已存在，未覆盖）" % os.path.basename(path))
                continue
            if is_cmd:
                body = CMD_TEMPLATE.format(title="%s - %s" % (title, "Live Notes"), py=py,
                                           script=cmd_target)
                with io.open(path, "w", encoding="utf-8", newline="\r\n") as f:
                    f.write(body)
            else:
                body = SH_TEMPLATE.format(script=sh_target)
                with io.open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(body)
                try:
                    os.chmod(path, 0o755)
                except OSError:
                    pass
            rep.append("  [OK] %s" % os.path.basename(path))
    print("[2/4] 模板、术语表、配置、启动器已就位")

    # 3) 模型
    mdir = os.path.join(dest, "models")
    if no_models:
        print("[3/4] 跳过模型目录（首次录音会自动下载到 %s）" % mdir)
    else:
        src_models = model_from or find_models()
        if src_models:
            print("[3/4] 模型接入: %s -> %s  (%s)"
                  % (mdir, src_models, link_models(mdir, src_models)))
        else:
            os.makedirs(mdir, exist_ok=True)
            print("[3/4] 未找到可复用的模型，已建空目录 %s（首次录音自动下载约 460MB）" % mdir)

    # 4) 生成摘要骨架 + 看板
    print("[4/4] 生成摘要骨架与看板…")
    r = subprocess.run([py, os.path.join(dest, "new_session.py"), "--check"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=dest,
                       env=dict(os.environ, SEMINAR_DIR=dest, PYTHONIOENCODING="utf-8"))
    print("      new_session --check 退出码 = %d" % r.returncode)
    for ln in ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[:20]:
        print("      " + ln)

    print()
    print("=== 明细 ===")
    print("\n".join(rep))
    print()
    print("下一步：")
    print("  1) 编辑 %s\\议程配置.json —— 填会议名/日期/各场次时间与讲者" % dest)
    print("     （没有议程也留空：脚本会自动按「单场模式」处理整段录音）")
    print("  2) 编辑 %s\\术语表.txt  —— 填人名、公司名、专有名词（必做，收益最大）" % dest)
    print("  3) 双击「环境体检」（或 .sh）—— 缺什么按提示补")
    print("  4) 双击「麦克风实测」选 [2] 扫一遍麦克风")
    print("  5) 双击「启动实时转写」，再双击「启动看板刷新」")
    print("  6) 开局跑一次： %s new_session.py   （把看板与摘要切到本场）" % py)
    return 0


if __name__ == "__main__":
    sys.exit(main())
