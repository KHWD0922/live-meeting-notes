# -*- coding: utf-8 -*-
"""
线下 Seminar 实时转写 —— 常驻拾音脚本（默认英语）

做三件事：
  1. 持续从麦克风采集音频，同时完整存一份 wav（便于会后高质量重转）
  2. 每 N 秒切一段，用 faster-whisper(CPU/int8) 转成带时间戳的文字
  3. 把文本滚动追加到 live_transcript.txt

语言策略：
  默认 --language en（英语 Seminar）；若现场有中文发言，用 --language auto
  自动检测；纯中文场景用 --language zh。中文结果会自动转成简体。

防幻觉四重保险：
  - 静音闸门：音量过低的片段直接跳过，不送进模型
  - 有效语音时长闸门：VAD 检出的语音不足 0.8 秒，判为噪声直接丢弃
  - 回声过滤：识别结果若与提示词高度重合，判为幻觉丢弃
  - 黑名单：过滤 Whisper 在静音段常见的固定幻觉句（中英双语）

术语表与纠错表：
  默认自动读取同目录 `术语表.txt`。
  - 普通行  -> 作为 initial_prompt 灌给模型，提升人名/术语识别率
  - `fix:` 行 -> 转写后做字面替换，格式 `fix: 错误写法 = 正确写法`

用法：
  python live_capture.py                                   # 英语，small，15 秒一段
  python live_capture.py --glossary "Speakers: Jane Doe, John Smith. Terms: CMDB, ITSM"
  python live_capture.py --language auto                   # 英中混合场次
  python live_capture.py --model medium                    # 换更大模型（更准，更吃 CPU）
  python live_capture.py --chunk 10                        # 更短切片，延迟更低
  python live_capture.py --list-devices                    # 列出所有音频设备
  python live_capture.py --device 12                       # 指定输入设备编号
停止：按 Ctrl+C
"""
import argparse
import atexit
import datetime
import os
import queue
import re
import shutil
import sys
import threading
import time
import wave

# 国内直连 huggingface.co 不通，走镜像
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

try:
    from zhconv import convert as _zh_convert
except Exception:
    _zh_convert = None

BASE = (os.environ.get("SEMINAR_DIR")
        or os.path.dirname(os.path.abspath(__file__)))
LOCAL_MODELS = os.path.join(BASE, "models")
SAMPLERATE = 16000
CHANNELS = 1

# 低于此 RMS 视为静音，直接跳过（可用 --silence-rms 调整）
DEFAULT_SILENCE_RMS = 0.0018

# 一段有效语音的最短时长：低于此值判为噪声幻觉（最强的防幻觉手段）
MIN_SPEECH_SEC = 0.8

# ---- Whisper 静音幻觉黑名单 ----
# 强特征：只要出现就判为幻觉，不受长度限制（这些词几乎不会出现在真实会议里）
HALLUCINATION_STRONG = (
    # 英文 —— Whisper 在英文视频/音频静音段最常吐出的固定句
    "thanks for watching", "thank you for watching", "thank you for your watching",
    "please subscribe", "like and subscribe", "subscribe to",
    "subtitles by", "subtitle by", "subtitles:", "amara.org",
    "transcription by", "translated by", "uncaptioned", "www.", ".com",
    "ming pao", "voice over by",
    # 中文
    "请不吝点赞", "請不吝點贊", "字幕由", "字幕组", "字幕志愿者",
    "明镜与点点", "下期再见", "我们下个视频", "订阅频道", "訂閱頻道",
)

# 弱特征：这些词在会议里也可能正常出现，仅当整句很短（≤12 字）时才判为幻觉
HALLUCINATION_SHORT = (
    # 英文
    "thank you", "thanks", "bye", "you", "the end", "subtitles",
    # 中文
    "谢谢观看", "谢谢大家观看", "感谢观看", "谢谢大家", "谢谢", "再见", "订阅",
)


def ts(t=None):
    return (t or datetime.datetime.now()).strftime("%H:%M:%S")


# ---------------------------------------------------------------- 单实例锁
# 为什么要这个：误双击两次会让两个进程同时往 live_transcript.txt 追加，
# 内容交错、时间戳乱序，会后合并时极难还原。
LOCK_NAME = "_capture.lock"


def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(path):
    """拿到锁返回 True；已有活着的实例返回 False。"""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = int((f.read() or "0").strip() or 0)
        except (OSError, ValueError):
            old = 0
        if old and old != os.getpid() and pid_alive(old):
            return False, old
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    return True, 0


def release_lock(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                pid = int((f.read() or "0").strip() or 0)
            if pid in (0, os.getpid()):
                os.remove(path)
    except (OSError, ValueError):
        pass


def to_simplified(text):
    """繁体 -> 简体。不依赖提示词，避免模型把提示词念出来。"""
    if _zh_convert is None:
        return text
    try:
        return _zh_convert(text, "zh-cn")
    except Exception:
        return text


def _norm(s):
    """去掉标点与空白，用于比对。"""
    return re.sub(r"[\s，。、！？；：,.!?;:~…—\-（）()\[\]【】\"'“”‘’]", "", s or "")


def is_echo(text, prompt):
    """识别结果若整体出现在提示词里，判为提示词泄漏。

    阈值取 15：术语表里常有 Acme / MySQL 这类短产品名，
    讲者真的说出这些词时不应被误判为泄漏。
    """
    if not prompt:
        return False
    a, b = _norm(text), _norm(prompt)
    if len(a) < 15:
        return False
    return a in b


def _glossary_tokens(prompt):
    """把术语表切成去重的小词元（长词优先），用于判断整句是否只是术语表回声。"""
    toks = set()
    for t in re.split(r"[,;.、，。\s]+", prompt or ""):
        n = _norm(t).lower()
        if len(n) >= 3:
            toks.add(n)
    return sorted(toks, key=len, reverse=True)


def is_glossary_echo(text, prompt):
    """整句几乎只是术语表条目的拼凑 —— 判为提示词回声幻觉。

    为什么必须单独一条：术语表里全是人名（Jane Doe / John Smith …），
    Whisper 在静音段很容易把提示词里的人名"念"出来。
    此时句子很短，is_echo 的长度闸门拦不住，黑名单也没有这些人名。
    """
    if not prompt or not text:
        return False
    a = _norm(text).lower()
    if not a or len(a) > 60:
        return False
    rest = a
    for t in _glossary_tokens(prompt):
        rest = rest.replace(t, "")
    covered = 1.0 - len(rest) / max(1, len(a))
    return covered >= 0.9 and len(rest) <= 3


def _fix_regex(wrong):
    """把「错误写法」编成正则。

    为什么要加边界：`Jane Dow → Jane Doe` 这种规则，
    用朴素的 str.replace 会把本来正确的 "Jane Doe" 再补一个 e 变成 "Jane Doee"。
    这里用「前后不能是字母数字」的环视，避免踩这种坑。
    含中日韩字符时不加边界（中文之间没有词边界概念）。
    """
    esc = re.escape(wrong)
    if re.search(r"[\u3000-\u9fff\uff00-\uffef]", wrong):
        return esc
    return r"(?<![A-Za-z0-9])" + esc + r"(?![A-Za-z0-9])"


def parse_fix_rules(path):
    """从术语表文件里解析 fix: 纠错规则。

    返回 [(编译好的正则, 正确写法, 错误写法原文)]，长词优先。

    格式（一行一条，直接写在 `术语表.txt` 里即可）：

        fix: Cooper Netes = Kubernetes
        fix: Post Gres => PostgreSQL

    分隔符支持 `=`、`=>`、`→`。只做字面替换，不做模糊匹配，
    所以宁可少写几条、写准，也不要乱猜。
    """
    rules = []
    if not path or not os.path.exists(path):
        return rules
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                s = ln.strip()
                if s[:4].lower() != "fix:":
                    continue
                body = s[4:].strip()
                for sep in ("=>", "→", "="):
                    if sep in body:
                        wrong, right = body.split(sep, 1)
                        wrong = wrong.strip().strip("\"'").strip()
                        right = right.strip().strip("\"'").strip()
                        if wrong and right and wrong.lower() != right.lower():
                            rules.append((wrong, right))
                        break
    except Exception:
        return []
    # 长词优先：先替换 "Thunder I's" 再替换 "Thunder"
    rules.sort(key=lambda r: len(r[0]), reverse=True)
    return [
        (re.compile(_fix_regex(w), re.IGNORECASE), r, w)
        for w, r in rules
    ]


def apply_fixes(text, rules):
    """按纠错表做批量替换。"""
    if not text or not rules:
        return text
    out = text
    for pat, right, _wrong in rules:
        out = pat.sub(lambda m: right, out)
    return out


def is_hallucination(text):
    """命中静音幻觉黑名单。强特征不限长度，弱特征仅在整句很短时才生效。"""
    if not text:
        return False
    low = text.lower()
    if any(p in low for p in HALLUCINATION_STRONG):
        return True
    n = _norm(low)
    if not n:
        return False
    if len(n) <= 12 and any(_norm(p).lower() in n for p in HALLUCINATION_SHORT):
        return True
    return False


def resolve_model(model_size):
    """优先用本地实目录里的模型，避免 Windows 符号链接导致的加载失败。"""
    name = model_size
    if os.sep not in model_size and not os.path.isabs(model_size):
        candidate = os.path.join(LOCAL_MODELS, "faster-whisper-%s" % model_size)
        if os.path.isdir(candidate) and os.path.getsize(os.path.join(candidate, "model.bin")) > 1024:
            return candidate
    return name


class Transcriber:
    """后台转写线程：从队列取音频片段，转写后追加写文件。"""

    def __init__(self, model_size, language, transcript_path, glossary=None, fix_rules=None,
                 decode=None, chunk_sec=20):
        self.language = language
        self.chunk_sec = chunk_sec
        # 英语结果不需要繁简转换；中文/自动检测场景才做
        self.is_english = bool(language) and str(language).lower().startswith("en")
        self.glossary = glossary
        self.fix_rules = fix_rules or []
        # 解码参数（beam / 抑制重复）。默认值在 build_decode_opts() 里给。
        self.decode = dict(decode or {})
        self.fixed = 0
        self.transcript_path = transcript_path
        self.queue = queue.Queue()
        self.stop_flag = False
        self.dropped = 0
        self.last_line = ""
        resolved = resolve_model(model_size)
        print("[%s] 正在加载模型 %s ..." % (ts(), resolved), flush=True)
        t0 = time.time()
        self.model = WhisperModel(resolved, device="cpu", compute_type="int8")
        print("[%s] 模型就绪，耗时 %.1f 秒" % (ts(), time.time() - t0), flush=True)
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def submit(self, audio, wall_clock):
        self.queue.put((audio, wall_clock))
        # 落后告警：队列里堆的段数 × 每段秒数 ≈ 落后时长。
        # 不丢音频（丢了就永久缺内容），但必须让人当场看见，否则会一直追不上。
        backlog = self.queue.qsize()
        if backlog >= 6 and backlog % 3 == 0:
            print("[%s] ★ 转写落后约 %d 秒（积压 %d 段）——"
                  "可加大 --chunk 或降模型；先别停，会慢慢追上"
                  % (wall_clock, int(backlog * getattr(self, "chunk_sec", 20)), backlog),
                  flush=True)

    def _append(self, line):
        with open(self.transcript_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _worker(self):
        while not (self.stop_flag and self.queue.empty()):
            try:
                audio, wall_clock = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                t0 = time.time()
                # ★ 解码参数很关键（实测）：
                #   beam_size=5 在噪声段容易陷入重复幻觉（"AI, AI, AI…" 刷几十遍），
                #   解码开销是贪心解码的 2 倍以上；配合 10 秒切片会把吞吐压到 0.5x 实时。
                #   现场推荐：切片放大到 20 秒 + beam_size=1 + 抑制重复。
                segments, _info = self.model.transcribe(
                    audio,
                    language=self.language,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    initial_prompt=self.glossary,
                    **self.decode
                )
                segs = list(segments)
                raw = "".join(s.text for s in segs).strip()
                # 纠错表：在过滤/落盘之前统一替换成人名、术语的正确写法，
                # 这样后面的「术语表回声」判定也能作用在被纠正过的文本上。
                fixed_raw = apply_fixes(raw, self.fix_rules)
                if fixed_raw != raw:
                    self.fixed += 1
                raw = fixed_raw
                speech_sec = sum(max(0.0, s.end - s.start) for s in segs)
                cost = time.time() - t0

                if speech_sec < MIN_SPEECH_SEC:
                    self.dropped += 1
                    print("[%s] · 有效语音不足 %.1fs，判为噪声 (%d)" % (
                        wall_clock, MIN_SPEECH_SEC, self.dropped), flush=True)
                    continue
                if not raw or raw.isspace():
                    print("[%s] · 无有效语音 (%.1fs)" % (wall_clock, cost), flush=True)
                    continue
                if (is_echo(raw, self.glossary)
                        or is_glossary_echo(raw, self.glossary)
                        or is_hallucination(raw)):
                    self.dropped += 1
                    print("[%s] · 已过滤幻觉 (%d): %s" % (wall_clock, self.dropped, raw[:30]), flush=True)
                    continue

                text = raw.strip() if self.is_english else to_simplified(raw).strip()
                line = "[%s] %s" % (wall_clock, text)

                # 连续重复内容只保留一次
                if _norm(text) and _norm(text) == _norm(self.last_line):
                    print("[%s] · 重复内容已合并" % wall_clock, flush=True)
                    continue
                self.last_line = text

                self._append(line)
                try:
                    print(line, flush=True)
                except Exception:
                    pass
            except Exception as e:
                print("[%s] 转写出错: %s" % (ts(), e), flush=True)
            finally:
                self.queue.task_done()

    def close(self):
        self.stop_flag = True
        self.thread.join(timeout=180)


def build_decode_opts(args):
    """把命令行参数整理成 faster-whisper 的解码参数。

    ★ 为什么这几项对"实时"是决定性的（现场实测）：
      · beam_size=5 → 1   ：约快 2 倍（噪声段不再陷入重复幻觉的长解码）
      · 切片 10 秒 → 20 秒 ：约快 2.5 倍
        原因：Whisper 的编码器固定按 30 秒窗口计算，切片越短，
        这份固定开销被摊在越少的音频上，单位音频的代价就越高。
      实测：10 秒切片 = 0.51x 实时（追不上）；20 秒 = 1.29x；整段不切 = 2.40x。
    """
    opts = {"beam_size": max(1, int(getattr(args, "beam", 5)))}
    rp = float(getattr(args, "repetition_penalty", 1.1) or 0)
    if rp and rp > 1.0:
        opts["repetition_penalty"] = rp
    ng = int(getattr(args, "no_repeat_ngram", 3) or 0)
    if ng > 0:
        opts["no_repeat_ngram_size"] = ng
    return opts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small", help="tiny/base/small/medium/large-v3")
    ap.add_argument("--chunk", type=float, default=20.0,
                    help="每段音频秒数。★ 实测：10 秒会把吞吐压到 0.5x 实时（追不上），"
                         "默认 20（约 1.3x）")
    ap.add_argument("--beam", type=int, default=1,
                    help="beam search 宽度。1 = 贪心，约快 2 倍（默认）；准确率优先可设 5")
    ap.add_argument("--repetition-penalty", type=float, default=1.1,
                    help="重复惩罚，抑制噪声段的重复幻觉（1.0 = 关闭）")
    ap.add_argument("--no-repeat-ngram", type=int, default=3,
                    help="禁止重复的 n-gram 长度，抑制刷屏式幻觉（0 = 关闭）")
    ap.add_argument("--device", default=None, help="输入设备编号或名称，默认系统默认设备")
    ap.add_argument(
        "--language",
        default="en",
        help="语言：en（默认，英语 Seminar）/ zh（中文）/ auto（英中自动检测）",
    )
    ap.add_argument(
        "--glossary",
        default=None,
        help='术语表/人名，例如 "Speakers: Jane Doe, John Smith. Terms: CMDB, ITSM"',
    )
    ap.add_argument(
        "--glossary-file",
        default=None,
        help="从文本文件读取术语表（每行一条，# 开头为注释）",
    )
    ap.add_argument(
        "--silence-rms",
        type=float,
        default=DEFAULT_SILENCE_RMS,
        help="静音闸门阈值，越小越灵敏（默认 %.4f）" % DEFAULT_SILENCE_RMS,
    )
    ap.add_argument("--list-devices", action="store_true", help="列出音频设备后退出")
    ap.add_argument("--no-audio-save", action="store_true", help="不保存整场 wav")
    args = ap.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return

    # 语言归一化：auto / detect -> None（交给 faster-whisper 自动检测）
    lang = str(args.language).strip().lower()
    if lang in ("auto", "detect", "none", ""):
        lang = None
    args.language = lang
    lang_label = args.language if args.language else "auto（自动检测）"

    # 术语表：命令行 --glossary 优先；否则读 --glossary-file（默认同目录 术语表.txt）
    # 同一个文件里的 `fix:` 行是纠错规则，不参与提示词。
    gl_path = args.glossary_file or os.path.join(BASE, "术语表.txt")
    fix_rules = parse_fix_rules(gl_path)
    if not args.glossary and os.path.exists(gl_path):
        try:
            with open(gl_path, "r", encoding="utf-8") as gf:
                items = [
                    ln.strip() for ln in gf
                    if ln.strip()
                    and not ln.strip().startswith("#")
                    and ln.strip()[:4].lower() != "fix:"
                ]
            if items:
                g = ". ".join(items)
                if len(g) > 800:          # 过长的提示词会削弱效果并拖慢转写，做截断
                    g = g[:800]
                args.glossary = g
                print("[%s] 已载入术语表（%d 条 / %d 字符）: %s" % (
                    ts(), len(items), len(g), gl_path), flush=True)
        except Exception as e:
            print("[%s] 术语表读取失败（忽略）: %s" % (ts(), e), flush=True)

    if fix_rules:
        print("[%s] 已载入纠错规则 %d 条: %s" % (
            ts(), len(fix_rules),
            " / ".join("%s→%s" % (w, r) for _p, r, w in fix_rules)), flush=True)

    session = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    transcript_path = os.path.join(BASE, "live_transcript.txt")
    audio_path = os.path.join(BASE, "recording_%s.wav" % session)

    if os.path.exists(transcript_path):
        archived = os.path.join(BASE, "live_transcript_%s.txt" % session)
        try:
            os.replace(transcript_path, archived)
        except OSError:
            # 网盘 / 同步盘常拒绝 rename 语义（有读共享但无删除共享）。
            # 退化为「复制 + 清空」，保证上一场内容不丢。
            try:
                shutil.copy2(transcript_path, archived)
                with open(transcript_path, "w", encoding="utf-8"):
                    pass
            except OSError as e:
                print("[%s] 上一场转写归档失败（继续录制，内容会追加在本文件）: %s"
                      % (ts(), e), flush=True)
                archived = None
        if archived:
            print("[%s] 上一场转写已归档: %s" % (ts(), archived), flush=True)

    lock_path = os.path.join(BASE, LOCK_NAME)
    ok_lock, other = acquire_lock(lock_path)
    if not ok_lock:
        print("[%s] 已经有一个转写进程在跑（PID %d），本次不再启动。"
              % (ts(), other))
        print("         要重开请先双击「停止转写」，或结束那个进程后删除 %s" % lock_path)
        return 2
    atexit.register(release_lock, lock_path)


    with open(transcript_path, "w", encoding="utf-8") as f:
        f.write("# 实时转写  —— 开始于 %s\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        f.write("# 语言: %s ｜ 模型: %s ｜ 切片: %.0f 秒 ｜ 音频存档: %s\n" % (
            lang_label, args.model, args.chunk, os.path.basename(audio_path)))
        f.write("# 解码: beam=%d ｜ 重复惩罚=%s ｜ 禁重复 n-gram=%s\n" % (
            args.beam, getattr(args, "repetition_penalty", 1.1),
            getattr(args, "no_repeat_ngram", 3)))
        if args.glossary:
            f.write("# 术语表: %s\n" % args.glossary)
        if fix_rules:
            f.write("# 纠错规则: %s\n" % " / ".join("%s→%s" % (w, r) for _p, r, w in fix_rules))
        f.write("\n")

    # 停止标记：双击「停止转写.cmd」会创建这个文件，主循环看到后优雅收尾。
    # 用于控制台窗口不在焦点、Ctrl+C 传不进去的场景。
    # 放在加载模型之前清残留，避免"刚启动就被上一轮的残留标记停掉"。
    stop_sentinel = os.path.join(BASE, "STOP_CAPTURE")
    if os.path.exists(stop_sentinel):
        try:
            os.remove(stop_sentinel)
        except Exception:
            pass

    device = args.device
    if device is not None and str(device).isdigit():
        device = int(device)

    print("[%s] 输入设备: %s" % (ts(), device if device is not None else "系统默认"))
    print("[%s] 转写文件: %s" % (ts(), transcript_path))
    print("[%s] 音频存档: %s" % (ts(), audio_path if not args.no_audio_save else "(已关闭)"))
    print("-" * 60, flush=True)

    decode_opts = build_decode_opts(args)
    transcriber = Transcriber(args.model, args.language, transcript_path, args.glossary,
                              fix_rules, decode_opts, chunk_sec=args.chunk)

    audio_q = queue.Queue()
    writer_stop = threading.Event()

    wave_writer = None
    if not args.no_audio_save:
        wave_writer = wave.open(audio_path, "wb")
        wave_writer.setnchannels(CHANNELS)
        wave_writer.setsampwidth(2)
        wave_writer.setframerate(SAMPLERATE)

    def writer_loop():
        while not (writer_stop.is_set() and audio_q.empty()):
            try:
                block = audio_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if wave_writer is not None:
                wave_writer.writeframes((np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
            audio_q.task_done()

    writer_thread = threading.Thread(target=writer_loop, daemon=True)
    writer_thread.start()

    state = {"buf": [], "len": 0, "start": None}
    chunk_target = int(SAMPLERATE * args.chunk)

    def callback(indata, frames, time_info, status):
        if status:
            print("[%s] 音频状态: %s" % (ts(), status), flush=True)
        mono = indata[:, 0].astype(np.float32).copy()
        audio_q.put(mono)

        if state["start"] is None:
            state["start"] = datetime.datetime.now()
        state["buf"].append(mono)
        state["len"] += len(mono)
        if state["len"] >= chunk_target:
            merged = np.concatenate(state["buf"])
            start_label = state["start"].strftime("%H:%M:%S")
            state["buf"] = []
            state["len"] = 0
            state["start"] = None

            rms = float(np.sqrt(np.mean(merged ** 2)))
            if rms < args.silence_rms:
                print("[%s] · 静音段跳过 (rms=%.4f)" % (start_label, rms), flush=True)
            else:
                transcriber.submit(merged, start_label)

    try:
        with sd.InputStream(
            samplerate=SAMPLERATE,
            channels=CHANNELS,
            dtype="float32",
            device=device,
            callback=callback,
            blocksize=0,
        ):
            print("[%s] ● 录音中… 结束方式：按 Ctrl+C，或双击「停止转写.cmd」" % ts(), flush=True)
            while True:
                time.sleep(0.5)
                if os.path.exists(stop_sentinel):
                    print("\n[%s] 检测到停止标记，正在收尾…" % ts(), flush=True)
                    try:
                        os.remove(stop_sentinel)
                    except Exception:
                        pass
                    break
    except KeyboardInterrupt:
        print("\n[%s] 收到停止信号，正在处理剩余音频…" % ts(), flush=True)
    finally:
        if state["len"] > SAMPLERATE * 2 and state["start"] is not None:
            merged = np.concatenate(state["buf"])
            if float(np.sqrt(np.mean(merged ** 2))) >= args.silence_rms:
                transcriber.submit(merged, state["start"].strftime("%H:%M:%S"))
        time.sleep(0.5)
        writer_stop.set()
        writer_thread.join(timeout=30)
        transcriber.close()
        if wave_writer is not None:
            wave_writer.close()
        with open(transcript_path, "a", encoding="utf-8") as f:
            f.write("\n# —— 结束于 %s，累计过滤幻觉 %d 段，纠错替换 %d 段 ——\n" % (
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                transcriber.dropped, transcriber.fixed))
        print("[%s] 已结束。转写: %s" % (ts(), transcript_path), flush=True)
        if wave_writer is not None:
            print("[%s] 音频存档: %s" % (ts(), audio_path), flush=True)
        release_lock(lock_path)


if __name__ == "__main__":
    main()
