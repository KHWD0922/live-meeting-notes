# -*- coding: utf-8 -*-
"""
麦克风实测工具（默认按英语转写）

用法：
  python mic_check.py                 # 用默认麦克风录 20 秒并立即转写
  python mic_check.py --seconds 15    # 自定义时长
  python mic_check.py --device 12     # 指定设备
  python mic_check.py --scan          # 扫描所有输入设备，各录 4 秒，比较拾音电平
  python mic_check.py --file x.wav    # 直接转写一个已有音频文件
  python mic_check.py --language auto # 转写语言改自动检测

判断标准：
  电平 < 0.002  -> 基本没拾到声音
  电平 0.002~0.01 -> 偏小，远场可能识别不准
  电平 > 0.01   -> 正常
"""
import argparse
import os
import sys
import time
import wave

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import sounddevice as sd

BASE = (os.environ.get("SEMINAR_DIR")
        or os.path.dirname(os.path.abspath(__file__)))
SR = 16000
MODEL_DIR = os.path.join(BASE, "models", "faster-whisper-small")


def record(seconds, device=None, meter=True):
    """录音。meter=True 时实时打印拾音电平条，让你当场看出麦克风是否在'听'。"""
    if not meter:
        rec = sd.rec(int(SR * seconds), samplerate=SR, channels=1,
                     dtype="float32", device=device)
        sd.wait()
        return rec[:, 0]

    chunks = []
    block_frames = int(SR * 0.2)          # 每 0.2 秒刷一次表
    bar_len = 36
    stop = time.time() + seconds
    with sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                        device=device, blocksize=block_frames) as stream:
        while time.time() < stop:
            block, _overflow = stream.read(block_frames)
            mono = block[:, 0].copy()
            chunks.append(mono)
            r = float(np.sqrt(np.mean(mono ** 2)))
            filled = int(min(1.0, r / 0.05) * bar_len)
            bar = "#" * filled + "." * (bar_len - filled)
            sys.stdout.write("\r  [%s] 剩 %.1fs  RMS=%.4f " % (bar, max(0.0, stop - time.time()), r))
            sys.stdout.flush()
    sys.stdout.write("\n")
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def rms_of(x):
    return float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0


def describe(rms):
    if rms < 0.002:
        return "几乎没拾到声音"
    if rms < 0.01:
        return "偏小，远场可能不理想"
    return "正常"


def save_wav(path, data):
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.clip(data, -1, 1) * 32767).astype(np.int16).tobytes())
    w.close()


def transcribe(path, language="en"):
    from faster_whisper import WhisperModel
    m = WhisperModel(MODEL_DIR, device="cpu", compute_type="int8")
    segs, info = m.transcribe(path, language=language, beam_size=5,
                              vad_filter=True, condition_on_previous_text=False)
    segs = list(segs)
    speech = sum(max(0.0, s.end - s.start) for s in segs)
    text = "".join(s.text for s in segs).strip()
    return text, speech, info.duration


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--device", default=None)
    ap.add_argument("--scan", action="store_true", help="扫描所有输入设备比较电平")
    ap.add_argument("--file", default=None, help="直接转写已有音频文件")
    ap.add_argument("--language", default="en",
                    help="转写语言：en（默认）/ zh / auto")
    args = ap.parse_args()

    lang = str(args.language).strip().lower()
    if lang in ("auto", "detect", "none", ""):
        lang = None

    if args.file:
        text, speech, dur = transcribe(args.file, lang)
        print("音频时长 %.1fs ｜ 检出语音 %.1fs" % (dur, speech))
        print("转写结果: %s" % (text if text else "(空)"))
        return

    if args.scan:
        devices = sd.query_devices()
        inputs = []
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0:
                inputs.append((i, d["name"], d["hostapi"]))
        print("发现 %d 个输入设备，各录 4 秒（请持续说话）\n" % len(inputs))
        results = []
        for i, name, _api in inputs:
            try:
                data = record(4, device=i, meter=False)
                r = rms_of(data)
                results.append((r, i, name))
                print("  #%-3d %-52s RMS=%.5f  %s" % (i, name[:52], r, describe(r)))
            except Exception as e:
                print("  #%-3d %-52s 不可用 (%s)" % (i, name[:52], str(e)[:40]))
        if results:
            results.sort(reverse=True)
            print("\n>> 拾音最好的设备: #%d %s (RMS=%.5f)" % (
                results[0][1], results[0][2], results[0][0]))
            print("   启动转写时加参数: --device %d" % results[0][1])
        return

    device = args.device
    if device is not None and str(device).isdigit():
        device = int(device)

    print("录制 %.0f 秒 —— 请用平时开会的音量，对着笔记本正常讲话" % args.seconds, flush=True)
    print("（下方实时显示拾音电平，条形越长说明拾得越清楚）\n", flush=True)

    data = record(args.seconds, device=device)
    r = rms_of(data)
    peak = float(np.max(np.abs(data)))
    print("\nRMS=%.5f  PEAK=%.5f  -> %s" % (r, peak, describe(r)))

    p = os.path.join(BASE, "_mic_check.wav")
    save_wav(p, data)

    t0 = time.time()
    text, speech, dur = transcribe(p, lang)
    print("转写耗时 %.1fs" % (time.time() - t0))
    print("检出语音 %.1fs / 总时长 %.1fs" % (speech, dur))
    print("转写结果: %s" % (text if text else "(空 —— 说明没拾到有效人声)"))


if __name__ == "__main__":
    main()
