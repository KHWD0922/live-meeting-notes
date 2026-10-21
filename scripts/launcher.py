# -*- coding: utf-8 -*-
"""
统一中文菜单入口。

为什么需要它：Windows 的 cmd.exe 按 GBK 解析 .cmd 文件内容，
若 .cmd 里写中文会出现「行被拆断、命令解析失败」的问题（实测确认）。
因此所有 .cmd 只保留纯 ASCII 的调用逻辑，中文交互全部放在这里。

用法（由 .cmd 调用，一般不手动执行）：
  python launcher.py check      # 麦克风实测菜单
  python launcher.py capture    # 实时转写启动菜单
  python launcher.py stop       # 优雅停止正在运行的转写（等价于 Ctrl+C）
  python launcher.py board      # 启动看板实时刷新守护（每 15 秒重建 HTML）
"""
import os
import subprocess
import sys

BASE = (os.environ.get("SEMINAR_DIR")
        or os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def run(script, args):
    """以 UTF-8 环境调用同目录下的子脚本，输出直接继承控制台。

    ★ 为什么用 Popen 而不是 subprocess.call：
      Ctrl+C 会同时发给父进程（本脚本）和子进程（live_capture.py）。
      若父进程收到信号就立刻退出，.cmd 结束、控制台窗口关闭，
      子进程会被连带杀掉 —— 音频 wav 收不了尾，最后一段会丢。
      因此这里必须留下来等子进程自己收尾完成。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    cmd = [sys.executable, os.path.join(BASE, script)] + list(args)

    proc = subprocess.Popen(cmd, env=env)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        print()
        print("  收到停止信号：正在等转写收尾（保存音频 + 写完最后一段），请稍候…")
        while True:
            try:
                rc = proc.wait()
                break
            except KeyboardInterrupt:
                # 用户又按了一次 Ctrl+C，继续等，不要半路退出
                continue
        print("  已安全结束。")
        return rc


STOP_SENTINEL = "STOP_CAPTURE"


def stop_capture():
    """通过「停止标记文件」优雅停止正在运行的转写。

    为什么要这个东西：Ctrl+C 需要控制台窗口处于焦点状态；
    如果窗口被最小化、或被别的程序挡住，窗口不在焦点上按键就传不进去。
    双击 `停止转写.cmd` 则不受焦点影响，任何时候都能停。
    """
    import time

    sentinel = os.path.join(BASE, STOP_SENTINEL)
    transcript = os.path.join(BASE, "live_transcript.txt")

    # 先判断"看起来有没有在跑"：转写文件是否在最近 90 秒内被写过
    running = False
    if os.path.exists(transcript):
        if time.time() - os.path.getmtime(transcript) < 90:
            running = True

    if not running:
        print()
        print("  看起来当前没有正在进行的转写（live_transcript.txt 最近没有更新）。")
        print("  为避免误伤下一次，本次不发送停止信号。")
        print("  如果你确信正在录，请回到转写窗口按 Ctrl+C。")
        return 1

    with open(sentinel, "w", encoding="utf-8") as f:
        f.write("stop\n")

    print()
    print("  已发送停止信号，正在等转写收尾…")
    for _ in range(60):          # 最多等 30 秒
        time.sleep(0.5)
        if not os.path.exists(sentinel):
            print("  转写已正常结束：音频、转写文本都已保存。")
            print("  可以关闭这个窗口了。")
            return 0

    try:
        os.remove(sentinel)
    except Exception:
        pass
    print("  30 秒内没有等到收尾完成，标记已撤销（不会影响下一次）。")
    print("  请回到转写窗口查看情况，必要时按 Ctrl+C。")
    return 1


def menu(title, options):
    print()
    print("  " + title)
    for key, label in options:
        print("    [%s] %s" % (key, label))
    print()
    try:
        choice = input("  请选择（直接回车 = 1）: ").strip()
    except (EOFError, KeyboardInterrupt):
        choice = ""
    return choice or "1"


def main():
    argv = sys.argv[1:]
    mode = argv[0] if argv else ""
    extra = argv[1:]

    if mode == "check":
        choice = menu("麦克风实测", [
            ("1", "模拟现场测试（录 30 秒）：按下后走到 3-5 米外，用大会音量说话"),
            ("2", "扫描全部麦克风，比较拾音电平（各录 4 秒，请持续说话）"),
        ])
        if choice == "2":
            return run("mic_check.py", ["--scan"] + extra)
        return run("mic_check.py", ["--seconds", "30"] + extra)

    if mode == "capture":
        choice = menu("本场 Seminar 的语言", [
            ("1", "英语为主（推荐｜切片 20 秒 + 贪心解码，实测约 2.5x 实时）"),
            ("2", "英中混合（自动检测语言）"),
            ("3", "中文为主"),
        ])
        lang = {"2": "auto", "3": "zh"}.get(choice, "en")
        # ★ 现场实测修正：切片 10 秒会把吞吐压到 0.51x 实时（越录越落后）。
        #   原因：Whisper 编码器固定按 30 秒窗口算，切片越短，固定开销摊得越重。
        #   20 秒 = 1.29x，整段不切 = 2.40x。beam 1 再快约 2 倍。
        chunk = "20"
        # 英文用贪心解码（最快）；中文/自动检测保留 2 档 beam，
        # 在准确率与实时性之间取平衡（beam=5 实测只有约 0.5x 实时，会持续落后）。
        beam = "1" if lang == "en" else "2"
        return run("live_capture.py",
                   ["--model", "small", "--chunk", chunk, "--beam", beam,
                    "--language", lang] + extra)

    if mode == "stop":
        return stop_capture()

    if mode == "board":
        # 看板实时刷新守护：纯本地循环重建 HTML，不依赖定时任务
        return run("live_watch.py", extra)

    print("用法: launcher.py check | capture | stop | board [额外参数]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
