# -*- coding: utf-8 -*-
"""preflight.py —— 一条命令回答「这台机器现在能不能跑」。

检查项：Python 版本 / 依赖包 / Whisper 模型 / node / ffmpeg / 麦克风 / 磁盘 / 工作目录文件。
每一项都给出「缺了会怎样」和「怎么修」，不让用户对着一堆红色的 FAIL 发呆。

用法：
  python preflight.py            # 只体检
  python preflight.py --fix      # 体检 + 自动安装缺失的 Python 依赖（不装系统软件）
  python preflight.py --quiet    # 只打印问题
"""
import glob
import importlib.util
import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = (os.environ.get("SEMINAR_DIR")
        or os.path.dirname(os.path.abspath(__file__)))

PKG = [
    ("numpy", "numpy", "音频数组运算（必需）"),
    ("sounddevice", "sounddevice", "麦克风采集（必需）"),
    ("faster_whisper", "faster-whisper", "本地语音识别引擎（必需）"),
    ("zhconv", "zhconv", "繁体转简体（可选，缺失时中文不做繁简归一）"),
]

OK, WARN, FAIL = "OK", "WARN", "FAIL"
rows = []


def add(level, item, detail, fix=""):
    rows.append((level, item, detail, fix))


def has_mod(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def check_python():
    v = sys.version_info
    if v >= (3, 9):
        add(OK, "Python", "%d.%d.%d  %s" % (v[0], v[1], v[2], sys.executable))
    else:
        add(FAIL, "Python", "版本过低 %d.%d（需要 >= 3.9）" % (v[0], v[1]),
            "装 Python 3.10+ 后重装依赖")


def check_packages(fix=False):
    missing = []
    for mod, pipname, why in PKG:
        if has_mod(mod):
            add(OK, "依赖 " + pipname, "已安装")
        else:
            optional = mod == "zhconv"
            add(WARN if optional else FAIL, "依赖 " + pipname, "缺失 —— " + why,
                "%s -m pip install %s" % (sys.executable, pipname))
            if not optional:
                missing.append(pipname)
    if fix and missing:
        print("\n正在安装缺失依赖：%s ...\n" % ", ".join(missing))
        cmd = [sys.executable, "-m", "pip", "install"] + missing
        r = subprocess.run(cmd)
        print("\n安装退出码 = %d，请重新跑一次 preflight.py 复检。\n" % r.returncode)


def check_model():
    hits = []
    for d in (os.path.join(BASE, "models"),
              os.environ.get("WHISPER_MODELS") or "",
              os.path.expanduser("~/.cache/whisper-models")):
        if d and os.path.isdir(d):
            for p in glob.glob(os.path.join(d, "faster-whisper-*")):
                if os.path.exists(os.path.join(p, "model.bin")):
                    hits.append(p)
    hf = os.path.expanduser("~/.cache/huggingface/hub")
    if os.path.isdir(hf):
        for p in glob.glob(os.path.join(hf, "models--Systran--faster-whisper-*")):
            hits.append(p)
    if hits:
        add(OK, "Whisper 模型", hits[0])
    else:
        add(WARN, "Whisper 模型", "本机未找到已下载的模型",
            "首次运行 live_capture 会自动下载约 460MB（国内走 hf-mirror 镜像）；"
            "也可先跑 init_project.py --models <已有模型目录>")


def check_node():
    p = shutil.which("node")
    if p:
        add(OK, "node", p + "（用于看板 JS 真实执行校验）")
        return
    for pat in (os.path.expanduser("~/.workbuddy/binaries/node/versions/*/node.exe"),
                os.path.expanduser("~/.workbuddy/binaries/node/versions/*/bin/node"),
                r"C:\Program Files\nodejs\node.exe",
                "/usr/local/bin/node", "/opt/homebrew/bin/node", "/usr/bin/node"):
        hits = sorted(glob.glob(pat), reverse=True)
        if hits:
            add(OK, "node", hits[0])
            return
    add(WARN, "node", "未找到 —— 看板的 JS 语法校验会被跳过（页面仍可生成）",
        "装 Node.js 18+；不装也能用，只是少一道保险")


def check_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        add(OK, "ffmpeg", p)
    else:
        add(WARN, "ffmpeg", "未找到 —— 仅影响音频格式转换/旧录音分析，实时转写不需要",
            "可选：winget install ffmpeg / brew install ffmpeg")


def check_mic():
    try:
        import sounddevice as sd
    except Exception as e:
        add(FAIL, "麦克风", "sounddevice 不可用：%s" % e,
            "先装 sounddevice；Windows 一般自带 PortAudio，Linux 需 apt install libportaudio2")
        return
    try:
        devs = sd.query_devices()
    except Exception as e:
        add(FAIL, "麦克风", "无法枚举音频设备：%s" % e, "检查系统录音权限")
        return
    ins = []
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = -1
    for i, d in enumerate(devs):
        if int(d.get("max_input_channels", 0)) > 0:
            ins.append(i)
    if ins:
        add(OK, "麦克风", "可用输入设备 %d 个，默认 = %s" % (len(ins), default_in))
    else:
        add(FAIL, "麦克风", "没有检测到任何输入设备",
            "插好麦克风/会议全向麦，再到系统设置里允许应用访问麦克风")


def check_disk():
    try:
        u = shutil.disk_usage(BASE)
    except Exception as e:
        add(WARN, "磁盘", "无法读取：%s" % e)
        return
    free_gb = u.free / (1024 ** 3)
    need = "整场录音约 115MB/小时（16k 单声道 wav）"
    if free_gb >= 5:
        add(OK, "磁盘", "剩余 %.1f GB（%s）" % (free_gb, need))
    elif free_gb >= 2:
        add(WARN, "磁盘", "剩余仅 %.1f GB，长会议可能不够（%s）" % (free_gb, need))
    else:
        add(FAIL, "磁盘", "剩余 %.1f GB，不足以录一场会" % free_gb, "清理磁盘或换目录")


def check_files():
    need = ["live_capture.py", "build_board.py", "launcher.py", "state.py",
            "_看板模板.html"]
    miss = [n for n in need if not os.path.exists(os.path.join(BASE, n))]
    if miss:
        add(FAIL, "工作目录", "缺少 %d 个文件：%s" % (len(miss), ", ".join(miss)),
            "先跑 init_project.py 铺设工作目录")
    else:
        add(OK, "工作目录", BASE)
    if not os.path.exists(os.path.join(BASE, "议程配置.json")):
        add(WARN, "议程配置", "没有 议程配置.json —— 将按「单场模式」整场记录",
            "需要按议题分段时，填一份 议程配置.json")
    else:
        add(OK, "议程配置", "已存在")


def main():
    argv = sys.argv[1:]
    fix = "--fix" in argv
    quiet = "--quiet" in argv

    check_python()
    check_packages(fix=fix)
    check_model()
    check_node()
    check_ffmpeg()
    check_mic()
    check_disk()
    check_files()

    if not quiet:
        print("=" * 72)
        print("环境体检 · %s" % BASE)
        print("=" * 72)
    bad = 0
    for level, item, detail, fixcmd in rows:
        if quiet and level == OK:
            continue
        print("[%s] %-14s %s" % (level, item, detail))
        if fixcmd and level != OK:
            print("        → %s" % fixcmd)
        if level == FAIL:
            bad += 1

    warn = sum(1 for r in rows if r[0] == WARN)
    if not quiet:
        print("-" * 72)
        if bad:
            print("结论：有 %d 项必须先修，修完再开始录音。" % bad)
        else:
            print("结论：可以开录（%d 项提醒不影响录音）。" % warn)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
