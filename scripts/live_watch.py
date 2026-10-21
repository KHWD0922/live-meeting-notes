# -*- coding: utf-8 -*-
"""看板实时刷新守护：每隔 N 秒重建一次 会议看板.html。

为什么需要它：
  定时整理任务（automation）在本环境**最小粒度是 1 小时**，而且实测带有
  5–10 分钟的随机抖动（jitter），永远做不到"实时"。
  但"实时"这件事其实不需要模型 —— 转写文本本来就是实时落到
  live_transcript.txt 的，只要有人把它渲染进看板就行。
  这个守护进程就是干这个的：纯本地、无模型、每 15 秒一次。

用法：
  python live_watch.py              # 15 秒一轮
  python live_watch.py --interval 10
  python live_watch.py --once       # 只跑一次（排查用）

停止：Ctrl+C（或直接关窗口，它不持有任何资源）
"""
import datetime
import os
import sys
import time

BASE = (os.environ.get("SEMINAR_DIR")
        or os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import build_board  # noqa: E402

TRANSCRIPT = os.path.join(BASE, "live_transcript.txt")
MD = build_board.MD      # 跟随 build_board 的摘要探测结果
LOCK = os.path.join(BASE, "_watch.lock")


def ts():
    return datetime.datetime.now().strftime("%H:%M:%S")


def pid_alive(pid):
    """Windows 下判断进程是否真的还活着（比只看锁文件新旧可靠）。"""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k = ctypes.windll.kernel32
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:
        return False


def take_lock(interval):
    """单实例保护：避免重复双击起两个守护进程同时写同一个 HTML。

    判断顺序：① 锁里的 PID 是否还活着 → 活则拒绝；② 否则接管。
    这样"上一次被强杀、锁没清掉"的情况不会挡路。
    """
    if os.path.exists(LOCK):
        try:
            old = int(open(LOCK, encoding="utf-8").read().strip() or 0)
        except Exception:
            old = 0
        if old and old != os.getpid() and pid_alive(old):
            age = time.time() - os.path.getmtime(LOCK)
            print("  已经有一个看板刷新进程在运行了（PID %d，心跳 %.0f 秒前）。" % (old, age))
            print("  本窗口不做重复启动，直接关掉即可。")
            return False
    with open(LOCK, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True


def heartbeat():
    try:
        os.utime(LOCK, None)
    except OSError:
        pass


def release_lock():
    try:
        os.remove(LOCK)
    except OSError:
        pass


def fingerprint():
    """给两个数据源做轻量指纹，用来判断"这轮内容有没有变"。"""
    fp = []
    for p in (TRANSCRIPT, MD):
        try:
            st = os.stat(p)
            fp.append((st.st_size, int(st.st_mtime)))
        except OSError:
            fp.append(None)
    return tuple(fp)


def main():
    argv = sys.argv[1:]
    interval = 15
    once = "--once" in argv
    if "--interval" in argv:
        try:
            interval = max(3, int(argv[argv.index("--interval") + 1]))
        except (IndexError, ValueError):
            pass

    print()
    print("  看板实时刷新已启动 —— 每 %d 秒重建一次 会议看板.html" % interval)
    print("  浏览器保持打开即可；页面自身每 30 秒自动刷新。")
    print("  停止：按 Ctrl+C（或直接关闭本窗口）。")
    print()

    if not take_lock(interval):
        return 0

    last = None
    rounds = 0
    failures = 0

    try:
        while True:
            rounds += 1
            heartbeat()
            try:
                rc = build_board.main(["--quiet"])
                if rc != 0:
                    failures += 1
                    if failures <= 3 or failures % 20 == 0:
                        print("[%s] 本轮构建返回 %d（看板保持上一版，未破坏）"
                              % (ts(), rc), flush=True)
                else:
                    failures = 0
                    fp = fingerprint()
                    if fp != last:
                        last = fp
                        live, _ok = build_board.read_live()
                        print("[%s] 已刷新 ｜ 实时原声 %d 行（原始 %d，最后 %s）｜ 摘要 %d 字符"
                              % (ts(), len(live["lines"]), live["total"],
                                 live["lastTs"],
                                 os.path.getsize(MD) if os.path.exists(MD) else 0),
                              flush=True)
            except Exception as e:                  # 守护进程绝不能自己死掉
                failures += 1
                print("[%s] 异常已忽略：%r" % (ts(), e), flush=True)

            if once:
                return 0
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                raise
    finally:
        release_lock()
        print()
        print("  已停止（共 %d 轮）。看板保留最后一次生成的结果。" % rounds)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
