# -*- coding: utf-8 -*-
"""会话配置读取 —— 整条流水线唯一的「议程真相来源」。

所有脚本（new_session / merge_transcripts / split_topics / build_delivery）
都从这里取议题边界，不再各自硬编码。

配置文件：<工作目录>/议程配置.json
  - 不存在时使用 NEUTRAL_DEFAULTS（日期取当天），保证任何会议都能直接跑
  - sessions 为空时自动进入「单场模式」：整段录音视为一个议题（T1）
  - 可用环境变量 SEMINAR_CONFIG 指定其它路径
  - 可用环境变量 SEMINAR_DIR 覆盖工作目录
"""
import datetime
import io
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def base_dir():
    """工作目录：SEMINAR_DIR > 本脚本所在目录。"""
    return (os.environ.get("SEMINAR_DIR")
            or os.path.dirname(os.path.abspath(__file__)))


BASE = base_dir()


# ---------------------------------------------------------------- 场景预设
# 只影响文案（标题、看板抬头），不影响流水线逻辑。
SCENARIOS = {
    "meeting":  ("会议", "Meeting"),
    "seminar":  ("研讨会", "Seminar"),
    "panel":    ("座谈会", "Panel Discussion"),
    "training": ("培训", "Training"),
    "webinar":  ("线上会", "Webinar"),
}


DEFAULTS = {
    "code": "",
    "date": "",
    "weekday": "",
    "title_en": "",
    "scenario": "meeting",
    "agenda_note": "",
    "span": "",
    "subtitle": "音频与转写全程保存在本机",
    "summary_file": "会议摘要-双语.md",
    "board_template": "_看板模板.html",
    "board_file": "会议看板.html",
    "board_title": "",
    "board_h1": "",
    "transcript_file": "live_transcript.txt",
    "transcript_archive_glob": "live_transcript_*.txt",
    "merged_file": "全文转写-合并去重.txt",
    "final_file": "全文转写-最终.txt",
    "gap_file": "全文转写-未归入议题.txt",
    "topics_dir": "_topics",
    "summary_dir": "_summary",
    "record_dir": "_record",
    "delivery": {"md": "会后交付.md", "html": "会后交付.html"},
    "sessions": [],
}

WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def config_path():
    forced = os.environ.get("SEMINAR_CONFIG")
    if forced:
        return forced if os.path.isabs(forced) else os.path.join(BASE, forced)
    return os.path.join(BASE, "议程配置.json")


def _normalise_date(s):
    """接受 2026-01-31 / 2026/1/31 / 20260131，统一成 YYYY-MM-DD；失败返回空。"""
    s = str(s or "").strip()
    for pat, fmt in ((r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", "%Y-%m-%d"),
                     (r"^(\d{4})(\d{2})(\d{2})$", "%Y-%m-%d")):
        m = re.match(pat, s)
        if m:
            try:
                return datetime.datetime.strptime(
                    "%s-%s-%s" % (m.group(1), int(m.group(2)), int(m.group(3))),
                    fmt).strftime("%Y-%m-%d")
            except ValueError:
                return ""
    return ""


def _fill_identity(cfg):
    """补齐会议身份信息，缺省时用当天日期，绝不留空。"""
    today = datetime.date.today()
    d = _normalise_date(cfg.get("date")) or today.strftime("%Y-%m-%d")
    cfg["date"] = d
    try:
        cfg["weekday"] = cfg.get("weekday") or WEEKDAYS[
            datetime.datetime.strptime(d, "%Y-%m-%d").weekday()]
    except ValueError:
        cfg["weekday"] = cfg.get("weekday") or ""

    scen = str(cfg.get("scenario") or "meeting").strip().lower()
    if scen not in SCENARIOS:
        scen = "meeting"
    cfg["scenario"] = scen
    zh, en = SCENARIOS[scen]

    cfg["title_en"] = (cfg.get("title_en") or "").strip() or en
    cfg["code"] = (cfg.get("code") or "").strip() or d
    cfg["board_title"] = (cfg.get("board_title") or "").strip() or ("%s实时摘要" % zh)
    cfg["board_h1"] = (cfg.get("board_h1") or "").strip() or ("%s · 实时摘要" % zh)
    cfg["_scenario_zh"] = zh
    cfg["_scenario_en"] = en
    return cfg


def load(path=None):
    """读配置；缺字段用 DEFAULTS 补齐。返回 dict。"""
    p = path or config_path()
    cfg = dict(DEFAULTS)
    cfg["delivery"] = dict(DEFAULTS["delivery"])
    if os.path.exists(p):
        with io.open(p, "r", encoding="utf-8") as f:
            user = json.load(f)
        for k, v in user.items():
            if k == "delivery" and isinstance(v, dict):
                cfg["delivery"].update(v)
            else:
                cfg[k] = v
        cfg["_source"] = p
    else:
        cfg["_source"] = None

    # sessions 为空 -> 单场模式（整段录音一个议题）
    raw = cfg.get("sessions") or []
    cfg["_single_session"] = not raw
    if not raw:
        raw = [{
            "id": "T1",
            "start": "00:00",
            "end": "23:59",
            "en": cfg.get("title_en") or "Full Session",
            "zh": "全程记录",
            "speakers": "—",
            "note": "未提供议程，按整场处理",
        }]
        cfg["sessions_note"] = "single"
    cfg["sessions"] = normalise_sessions(raw)
    _fill_identity(cfg)
    # 单场模式下的标题要跟随配置，不被 DEFAULTS 覆盖
    if cfg["_single_session"]:
        cfg["sessions"][0]["en"] = cfg["title_en"]
        cfg["sessions"][0]["zh"] = "%s（全程）" % cfg["_scenario_zh"]
        cfg["sessions"][0]["span"] = "全程"
    cfg["_dir"] = BASE
    return cfg


def hhmm_to_sec(s):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(s or ""))
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60


def normalise_sessions(sessions):
    """补齐 id / start_sec / end_sec / note / 中英文标题。"""
    out = []
    for i, s in enumerate(sessions):
        s = dict(s)
        s.setdefault("id", "T%d" % (i + 1))
        s.setdefault("en", "")
        s.setdefault("zh", "")
        s.setdefault("speakers", "—")
        s.setdefault("note", "")
        s["start_sec"] = hhmm_to_sec(s.get("start"))
        s["end_sec"] = hhmm_to_sec(s.get("end"))
        if s["start_sec"] is None:
            s["start_sec"] = 0
        if s["end_sec"] is None:
            s["end_sec"] = 24 * 3600
        s["span"] = "%s-%s" % (s.get("start", ""), s.get("end", ""))
        out.append(s)
    return out


def is_break(s):
    """茶歇 / 午餐 / 空档场次（不生成要点小节的）。"""
    en = (s.get("en") or "")
    zh = (s.get("zh") or "")
    if en.startswith("☕"):
        return True
    for k in ("Break", "Lunch", "Dinner", "Tea"):
        if re.search(r"\b%s\b" % k, en):
            return True
    for k in ("茶歇", "午餐", "晚餐", "休息"):
        if k in zh:
            return True
    return False


def board_rows(cfg):
    """看板顶部进度条用的 [时间, 英文, 中文] 列表。"""
    rows = []
    for s in cfg["sessions"]:
        rows.append([s.get("start", ""), s.get("en", ""), s.get("zh", "")])
    return rows


def delivery_title(cfg):
    d = cfg.get("delivery") or {}
    t = d.get("title")
    if t:
        return t
    return "%s｜会后完整交付" % (cfg.get("title_en") or "Meeting")


def main():
    """自检：打印当前配置解析结果。"""
    cfg = load()
    print("工作目录 :", cfg["_dir"])
    print("配置来源 :", cfg["_source"] or "(无 议程配置.json，使用默认值)")
    print("会话     :", cfg["code"], cfg["date"], cfg["weekday"],
          "｜场景:", cfg["_scenario_zh"])
    print("摘要文件 :", cfg["summary_file"])
    print("模式     :", "单场（无议程）" if cfg["_single_session"] else "多议题")
    print("议题数   : %d" % len(cfg["sessions"]))
    for s in cfg["sessions"]:
        flag = " [break]" if is_break(s) else ""
        print("  %-4s %-13s %s / %s  ｜ %s%s"
              % (s["id"], s["span"], s["en"][:44], s["zh"][:16], s["speakers"], flag))


if __name__ == "__main__":
    main()
