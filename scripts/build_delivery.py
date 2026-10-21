# -*- coding: utf-8 -*-
"""build_delivery.py —— 会后第 4 步：把逐议题材料装配成一份可交付文档。

输入（都在工作目录下）：
  _summary\\S1.md …  每个议题的 Meeting Summary（中英对照）
  _record\\T1.md …   每个议题的实时文字记录（带时间戳、中英对照）
输出：
  会后交付-*.md / 会后交付-*.html（独立、可离线、可打印）

议题标题与时间来自 议程配置.json。
用法：
  python build_delivery.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import session_config  # noqa: E402

BASE = session_config.BASE
cfg = session_config.load()

SUM = os.path.join(BASE, cfg["summary_dir"])
REC = os.path.join(BASE, cfg["record_dir"])
D = cfg.get("delivery") or {}
OUT_MD = os.path.join(BASE, D.get("md", "会后交付.md"))
OUT_HTML = os.path.join(BASE, D.get("html", "会后交付.html"))


def side_files(s):
    num = re.sub(r"^[A-Za-z]+", "", s["id"])
    return "S%s.md" % num, "T%s.md" % num


def read(p):
    with io.open(p, "r", encoding="utf-8") as f:
        return f.read().replace("\r\n", "\n").replace("\r", "\n")


def drop_leading_h1(text):
    lines = text.split("\n")
    i = 0
    while i < len(lines) and (lines[i].strip() == "" or lines[i].startswith("# ")):
        i += 1
    return "\n".join(lines[i:])


def demote_summary(text):
    """summary: ## -> ###, ### -> ####（H1 已去掉）"""
    out = []
    for l in text.split("\n"):
        if l.startswith("#### "):
            out.append("##### " + l[5:])
        elif l.startswith("### "):
            out.append("#### " + l[4:])
        elif l.startswith("## "):
            out.append("### " + l[3:])
        else:
            out.append(l)
    return "\n".join(out)


def normalise_transcript(text):
    out = []
    for l in text.split("\n"):
        if re.match(r"^##\s*一、实时文字记录", l):
            out.append("## 实时文字记录 · Bilingual Live Transcript")
        elif l.startswith("## "):
            out.append("### " + l[3:])
        else:
            out.append(l)
    return "\n".join(out)


def build_master():
    P = []
    P.append("# %s" % session_config.delivery_title(cfg))
    P.append("")
    P.append("**%s ｜ 实时文字记录（中英对照）＋ Meeting Summary（中英对照）**"
             % cfg.get("title_en", ""))
    P.append("")
    P.append("> **日期**：%s（%s）｜ **议程区间**：%s" % (cfg["date"], cfg["weekday"], cfg.get("span", "—")))
    if cfg.get("agenda_note"):
        P.append("> **议程说明**：%s" % cfg["agenda_note"])
    P.append("> **记录方式**：现场本机实时转写（faster-whisper small / CPU），**未上传云端**；"
             "会后统一去重整理后按议题切分。")
    P.append("> **信源分级**：`EN` 为现场转写原文（已清理噪声与幻觉）；`中` 为译文。"
             "存疑处标 `[转写模糊]` / `[待确认]` / `[数字存疑]`，**不猜**。")
    P.append("> **合规**：涉台表述统一为「中国台湾」，涉港统一为「中国香港」。")
    P.append("")
    P.append("---")
    P.append("")
    P.append("## 目录 · Contents")
    P.append("")
    P.append("| # | 议题 | 时间 | 讲者 | 定位 |")
    P.append("|---|---|---|---|---|")
    for i, s in enumerate(cfg["sessions"], 1):
        P.append("| %d | %s · %s | %s | %s | %s |"
                 % (i, s["en"], s["zh"], s["span"], s["speakers"], s.get("note", "")))
    P.append("")
    P.append("**阅读方式**：每个议题分两部分——先看 **Meeting Summary**（1 页速读），"
             "再看 **实时文字记录**（逐段中英对照、带时间戳）。")
    P.append("")
    P.append("---")
    P.append("")

    missing = []
    for i, s in enumerate(cfg["sessions"], 1):
        sf, tf = side_files(s)
        sp, tp = os.path.join(SUM, sf), os.path.join(REC, tf)
        if not os.path.exists(sp):
            missing.append("%s（议题 %d 摘要）" % (sf, i))
        if not os.path.exists(tp):
            missing.append("%s（议题 %d 记录）" % (tf, i))
        P.append("# 议题 %d｜%s" % (i, s["en"]))
        P.append("")
        P.append("## %s" % s["zh"])
        P.append("")
        P.append("**时间**：%s ｜ **讲者**：%s ｜ **定位**：%s"
                 % (s["span"], s["speakers"], s.get("note", "")))
        P.append("")
        P.append("---")
        P.append("")
        if os.path.exists(sp):
            P.append(demote_summary(drop_leading_h1(read(sp))).strip())
        else:
            P.append("> ⚠️ 缺少 `_summary\\%s`，议题 %d 的摘要未生成。" % (sf, i))
        P.append("")
        P.append("---")
        P.append("")
        if os.path.exists(tp):
            P.append(normalise_transcript(drop_leading_h1(read(tp))).strip())
        else:
            P.append("> ⚠️ 缺少 `_record\\%s`，议题 %d 的实时文字记录未生成。" % (tf, i))
        P.append("")
        P.append("")
        P.append("---")
        P.append("")
    return "\n".join(P), missing


HTML_HEAD = u"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#f5f6f8; --panel:#ffffff; --ink:#1b1f24; --ink2:#4a5560; --ink3:#7b8794;
  --line:#e3e7ec; --brand:#1f4e79; --brand2:#0f7b6c; --accent:#b8442a;
  --en:#20465f; --cn:#1b1f24; --quote:#f3f6f9;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"Microsoft YaHei","PingFang SC","Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif;
  font-size:15.5px; line-height:1.85;
}
header.top{
  background:linear-gradient(135deg,#1f4e79 0%,#0f7b6c 100%);
  color:#fff; padding:26px 30px 22px;
}
header.top h1{margin:0 0 6px; font-size:22px; letter-spacing:.3px}
header.top .sub{font-size:13.5px; opacity:.92; line-height:1.7}
main{max-width:980px; margin:0 auto; padding:26px 30px 90px; background:var(--panel);
     min-height:100vh; box-shadow:0 0 0 1px var(--line)}
h1,h2,h3,h4,h5,h6{line-height:1.45; color:var(--ink); scroll-margin-top:70px}
h1{font-size:24px; margin:34px 0 8px; padding:14px 0 10px; border-top:4px solid var(--brand); color:var(--brand)}
h2{font-size:19px; margin:30px 0 10px; padding-left:11px; border-left:5px solid var(--brand2)}
h3{font-size:16.5px; margin:24px 0 8px; color:var(--ink)}
h4{font-size:15.5px; margin:20px 0 6px; color:var(--en)}
h5,h6{font-size:15px; margin:16px 0 6px; color:var(--ink2)}
p{margin:9px 0}
strong{color:#0d2b45}
ul,ol{margin:8px 0 8px 4px; padding-left:22px}
li{margin:5px 0}
hr{border:0; border-top:1px dashed var(--line); margin:26px 0}
code{background:#eef2f6; border:1px solid #e0e6ec; border-radius:4px;
     padding:1px 5px; font-family:Consolas,"Courier New",monospace; font-size:13.5px; color:#b8442a}
blockquote{
  margin:12px 0; padding:11px 16px; background:var(--quote);
  border-left:4px solid #9db6cc; border-radius:0 6px 6px 0; color:var(--ink2);
}
blockquote strong{color:var(--brand)}
table{border-collapse:collapse; width:100%; margin:14px 0; font-size:14px}
th,td{border:1px solid var(--line); padding:7px 10px; text-align:left; vertical-align:top}
th{background:#f0f4f8; color:var(--brand); font-weight:600; white-space:nowrap}
tbody tr:nth-child(even){background:#fafbfc}
a{color:var(--brand2)}
#toTop{position:fixed; right:20px; bottom:20px; z-index:9; border:0; cursor:pointer;
  background:var(--brand); color:#fff; border-radius:50%; width:44px; height:44px;
  font-size:18px; box-shadow:0 4px 14px rgba(0,0,0,.22); display:none}
@media print{
  body{background:#fff; font-size:11.5pt}
  header.top{background:#1f4e79 !important; -webkit-print-color-adjust:exact; print-color-adjust:exact}
  main{box-shadow:none; max-width:none; padding:0}
  #toTop{display:none !important}
  h1{page-break-before:always}
  h1:first-of-type{page-break-before:avoid}
  table,blockquote{page-break-inside:avoid}
}
@media (max-width:620px){
  header.top{padding:20px 16px}
  main{padding:18px 16px 70px}
  table{font-size:13px}
  th,td{padding:6px 7px}
}
</style>
</head>
<body>
<header class="top">
  <h1>__TITLE__</h1>
  <div class="sub">
    __SUBTITLE__<br>
    __DATE__ ｜本机实时转写，未上传云端｜涉台表述统一「中国台湾」、涉港统一「中国香港」
  </div>
</header>
<main id="doc"><p style="color:#7b8794">正在渲染…</p></main>
<button id="toTop" title="回到顶部">↑</button>

<script id="src" type="text/markdown">
__CONTENT__
</script>

<script>
function esc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function inline(s){
  return esc(s)
    .replace(/`([^`]+)`/g,"<code>$1</code>")
    .replace(/\\*\\*([^*]+)\\*\\*/g,"<strong>$1</strong>")
    .replace(/(^|[\\s(])\\*([^*\\n]+)\\*/g,"$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g,"<del>$1</del>");
}
function md(src){
  var L=src.split(/\\r?\\n/),out=[],i=0;
  while(i<L.length){
    var l=L[i];
    if(/^\\s*$/.test(l)){i++;continue;}
    var h=l.match(/^(#{1,6})\\s+(.*)$/);
    if(h){var lv=h[1].length;out.push("<h"+lv+">"+inline(h[2])+"</h"+lv+">");i++;continue;}
    if(/^\\s*(---|\\*\\*\\*)\\s*$/.test(l)){out.push("<hr>");i++;continue;}
    if(/^\\s*\\|/.test(l)&&/^\\s*\\|?[\\s:|-]+\\|?\\s*$/.test(L[i+1]||"")){
      var head=l.split("|").slice(1,-1).map(function(c){return c.trim();});
      i+=2;var rows=[];
      while(i<L.length&&/^\\s*\\|/.test(L[i])){
        rows.push(L[i].split("|").slice(1,-1).map(function(c){return c.trim();}));i++;
      }
      var t="<table><thead><tr>"+head.map(function(c){return "<th>"+inline(c)+"</th>";}).join("")+
            "</tr></thead><tbody>"+rows.map(function(r){
              return "<tr>"+r.map(function(c){return "<td>"+inline(c)+"</td>";}).join("")+"</tr>";
            }).join("")+"</tbody></table>";
      out.push(t);continue;
    }
    if(/^\\s*>\\s?/.test(l)){
      var b=[];
      while(i<L.length&&/^\\s*>\\s?/.test(L[i])){b.push(L[i].replace(/^\\s*>\\s?/,""));i++;}
      out.push("<blockquote>"+b.map(inline).join("<br>")+"</blockquote>");continue;
    }
    if(/^\\s*[-*+]\\s+/.test(l)){
      var u=[];
      while(i<L.length&&/^\\s*[-*+]\\s+/.test(L[i])){u.push(L[i].replace(/^\\s*[-*+]\\s+/,""));i++;}
      out.push("<ul>"+u.map(function(x){return "<li>"+inline(x)+"</li>";}).join("")+"</ul>");continue;
    }
    if(/^\\s*\\d+[.)]\\s+/.test(l)){
      var o=[];
      while(i<L.length&&/^\\s*\\d+[.)]\\s+/.test(L[i])){o.push(L[i].replace(/^\\s*\\d+[.)]\\s+/,""));i++;}
      out.push("<ol>"+o.map(function(x){return "<li>"+inline(x)+"</li>";}).join("")+"</ol>");continue;
    }
    var p=[l];i++;
    while(i<L.length&&!/^\\s*$/.test(L[i])&&!/^(#{1,6}\\s|>|\\s*[-*+]\\s|\\s*\\d+[.)]\\s|```)/.test(L[i])
          &&!/^\\s*(---|\\*\\*\\*)\\s*$/.test(L[i])){p.push(L[i]);i++;}
    out.push("<p>"+p.map(inline).join(" ")+"</p>");
  }
  return out.join("\\n");
}
var raw=document.getElementById("src").textContent;
document.getElementById("doc").innerHTML=md(raw);
var btn=document.getElementById("toTop");
window.addEventListener("scroll",function(){btn.style.display=window.scrollY>420?"block":"none";});
btn.addEventListener("click",function(){window.scrollTo({top:0,behavior:"smooth"});});
</script>
</body>
</html>
"""


def build_html(md_text):
    safe = md_text.replace("</script", "<\\/script")
    title = session_config.delivery_title(cfg)
    return (HTML_HEAD
            .replace("__TITLE__", title)
            .replace("__SUBTITLE__", "实时文字记录（中英对照）＋ Meeting Summary（中英对照）")
            .replace("__DATE__", "%s（%s）" % (cfg["date"], cfg["weekday"]))
            .replace("__CONTENT__", safe))


def check_tables(md_text):
    """校验所有表格的行列数一致（防止单元格内出现裸 | 导致串列）"""
    lines = md_text.split("\n")
    problems = []
    i = 0
    while i < len(lines):
        if re.match(r"^\s*\|", lines[i]) and i + 1 < len(lines) \
                and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            head = len(lines[i].split("|")) - 2
            j = i + 2
            while j < len(lines) and re.match(r"^\s*\|", lines[j]):
                c = len(lines[j].split("|")) - 2
                if c != head:
                    problems.append("line %d: 列数 %d != 表头 %d -> %s"
                                    % (j + 1, c, head, lines[j][:90]))
                j += 1
            i = j
        else:
            i += 1
    return problems


def main():
    md_text, missing = build_master()
    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write(md_text)
    html = build_html(md_text)
    with io.open(OUT_HTML, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)

    print("OK  主交付 Markdown: %s  (%d 字符 / %d 行)"
          % (os.path.basename(OUT_MD), len(md_text), md_text.count("\n") + 1))
    print("OK  主交付 HTML    : %s  (%d 字符)" % (os.path.basename(OUT_HTML), len(html)))
    if missing:
        print("!! 缺失材料 %d 项（文档里已标 ⚠️）：" % len(missing))
        for m in missing:
            print("     - %s" % m)
    probs = check_tables(md_text)
    print("表格校验: %s" % ("全部通过" if not probs else "发现 %d 处串列" % len(probs)))
    for p in probs[:12]:
        print("    ! " + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
