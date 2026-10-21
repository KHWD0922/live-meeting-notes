/* 用 Node 模拟浏览器，执行 会议看板.html 的渲染脚本，确认页面能真正渲染出内容。
   注意：eval 包在函数里执行，避免被测脚本的 var 与外层变量重名。 */
const fs = require("fs");

const html = fs.readFileSync("会议看板.html", "utf8");

const srcMatch = html.match(/<script id="src" type="text\/markdown">([\s\S]*?)<\/script>/);
if (!srcMatch) { console.log("!! 找不到 src 块"); process.exit(1); }
const srcText = srcMatch[1];

const liveMatch = html.match(/<script id="live" type="application\/json">([\s\S]*?)<\/script>/);
if (!liveMatch) { console.log("!! 找不到 live 块"); process.exit(1); }
const liveText = liveMatch[1];

const blocks = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
if (!blocks.length) { console.log("!! 找不到主脚本"); process.exit(1); }
const mainJs = blocks[blocks.length - 1][1];

const els = {};
function mk(id) {
  return { id, innerHTML: "", textContent: "", checked: true };
}
global.document = {
  getElementById(id) {
    if (id === "src") return { id: "src", textContent: srcText };
    if (id === "live") return { id: "live", textContent: liveText };
    if (!els[id]) els[id] = mk(id);
    return els[id];
  },
};
global.window = { scrollTo() {}, scrollY: 0 };
global.sessionStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
global.location = { reload() {} };
global.setInterval = () => 0;

const captured = {};
function run() {
  eval(mainJs);
  captured.agendaLen = typeof AGENDA !== "undefined" ? AGENDA.length : "未定义";
}
let err = null;
try { run(); } catch (e) { err = e; }

console.log("=== 执行结果 ===");
console.log("JS 异常       :", err ? err.name + " -> " + err.message : "无");
console.log("AGENDA 条数   :", captured.agendaLen);
console.log("src 字符数    :", srcText.length);
console.log("src 前 50     :", JSON.stringify(srcText.slice(0, 50)));
console.log();
console.log("doc 内容区    :", els.doc ? els.doc.innerHTML.length + " 字符" : "未赋值");
console.log("agenda 导航   :", els.agenda ? els.agenda.innerHTML.length + " 字符" : "未赋值");
console.log("当前议题文本  :", els.now ? JSON.stringify(els.now.textContent) : "未赋值");
console.log("时钟文本      :", els.clock ? JSON.stringify(els.clock.textContent) : "未赋值");
console.log();
const body = els.doc ? els.doc.innerHTML : "";
console.log("落入空内容分支:", body.indexOf("尚无内容") >= 0 ? "是（页面会显示'尚无内容'）" : "否");
console.log("渲染出 h2 标题:", (body.match(/<h2>/g) || []).length, "个");
console.log("渲染出 h3 标题:", (body.match(/<h3>/g) || []).length, "个");
console.log("渲染出 ol 列表:", (body.match(/<ol>/g) || []).length, "个");
console.log("渲染出表格    :", (body.match(/<table>/g) || []).length, "个");
console.log();
console.log("live JSON 长度:", liveText.length);
console.log("liveBody      :", els.liveBody ? els.liveBody.innerHTML.length + " 字符" : "未赋值");
console.log("liveMeta      :", els.liveMeta ? JSON.stringify(els.liveMeta.innerHTML).slice(0, 160) : "未赋值");
if (els.liveBody) {
  const n = (els.liveBody.innerHTML.match(/class="ln /g) || []).length +
            (els.liveBody.innerHTML.match(/class="ln"/g) || []).length;
  console.log("实时条目数    :", n, "条");
}
console.log();
console.log("=== 渲染结果前 500 字符 ===");
console.log(body.slice(0, 500));
