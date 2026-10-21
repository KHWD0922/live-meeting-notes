# -*- coding: utf-8 -*-
"""分析实测录音：分段电平 + 离线重转（英语）。结果同时写 UTF-8 报告文件。"""
import sys, os, wave, time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np

BASE = (os.environ.get("SEMINAR_DIR")
        or os.path.dirname(os.path.abspath(__file__)))
WAV = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "_mic_check.wav")
MODEL_DIR = os.path.join(BASE, "models", "faster-whisper-small")
REPORT = os.path.join(BASE, "_mic_report.txt")

out = []
def log(s=""):
    out.append(str(s))
    print(s, flush=True)


# ---------- 1. 读音频 ----------
with wave.open(WAV, "rb") as w:
    sr = w.getframerate()
    nch = w.getnchannels()
    nframes = w.getnframes()
    raw = w.readframes(nframes)

audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
if nch > 1:
    audio = audio.reshape(-1, nch).mean(axis=1)

dur = len(audio) / sr
log("=" * 52)
log("录音文件: %s" % os.path.basename(WAV))
log("采样率: %d Hz ｜ 声道: %d ｜ 时长: %.1f 秒" % (sr, nch, dur))
log("=" * 52)

# ---------- 2. 分段电平 ----------
win = int(sr * 0.5)
rms_list = []
for i in range(0, len(audio) - win + 1, win):
    seg = audio[i:i + win]
    rms_list.append(float(np.sqrt(np.mean(seg ** 2)) + 1e-12))

peak = float(np.abs(audio).max())
overall = float(np.sqrt(np.mean(audio ** 2)) + 1e-12)
log("整体 RMS: %.5f ｜ 峰值: %.5f" % (overall, peak))
log("")
log("分段电平（每 0.5 秒一格，共 %d 格）:" % len(rms_list))

# 每 2 秒合成一行并画条形图
bar_max = 40
for i in range(0, len(rms_list), 4):
    chunk = rms_list[i:i + 4]
    m = max(chunk)
    t0 = i * 0.5
    n = int(min(bar_max, m / 0.05 * bar_max))
    log("  %5.1fs |%-40s| %.5f" % (t0, "#" * n, m))

silent = sum(1 for r in rms_list if r < 0.01)
log("")
log("电平 > 0.01 的格子: %d / %d （%.0f%%）" % (len(rms_list) - silent, len(rms_list),
      100.0 * (len(rms_list) - silent) / max(1, len(rms_list))))

# ---------- 3. 离线重转 ----------
log("")
log("=" * 52)
log("离线重转（faster-whisper small / CPU int8 / 英语）")
log("=" * 52)

from faster_whisper import WhisperModel
t0 = time.time()
m = WhisperModel(MODEL_DIR, device="cpu", compute_type="int8")
segs, info = m.transcribe(
    audio, language="en", beam_size=5,
    vad_filter=True, condition_on_previous_text=False,
    no_speech_threshold=0.6, log_prob_threshold=-1.0,
)
total_speech = 0.0
lines = []
for s in segs:
    txt = s.text.strip()
    if not txt:
        continue
    total_speech += (s.end - s.start)
    lines.append("[%6.2f-%6.2f] %s" % (s.start, s.end, txt))
cost = time.time() - t0

log("检测语言: %s (prob=%.2f) ｜ 转写耗时 %.1fs（%.1fx 实时）" % (
    info.language, info.language_probability, cost, dur / max(0.01, cost)))
log("VAD 判定有效语音总长: %.1f 秒 / %.1f 秒（%.0f%%）" % (
    total_speech, dur, 100.0 * total_speech / max(0.01, dur)))
log("")
if lines:
    log("--- 转写内容 ---")
    for l in lines:
        log(l)
else:
    log("--- 转写内容为空 ---")

log("")
log("=" * 52)
log("结论")
log("=" * 52)

if not lines:
    verdict = "❌ 没拾到可识别语音"
elif total_speech >= dur * 0.3 and len(" ".join(lines)) > 60:
    verdict = "✅ 拾音正常，可直接用"
elif len(" ".join(lines)) > 15:
    verdict = "⚠️ 只拾到片段，建议换设备或外接麦"
else:
    verdict = "⚠️ 几乎没拾到语音"
log(verdict)

with open(REPORT, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
print("\n[报告已写入] %s" % REPORT)
