/* 时间线折叠逻辑的离线检查（临时文件，验证完删掉）。
 *
 * 造一个最小 DOM 桩把 app.js 跑起来，复刻一次订单链路的 36 条进度事件，
 * 断言折叠后的行数 / 计数 / 明细 / 展开收起 / 「换个阶段就开新行」。
 *   node tests/frontend/timeline_check.mjs
 */
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function Node() {}
function makeEl(tag) {
  return Object.assign(new Node(), {
    tagName: tag, className: '', textContent: '', hidden: false, style: {}, kids: [],
    setAttribute(k, v) { this[k] = v; },
    addEventListener(name, fn) { (this.handlers ||= {})[name] = fn; },
    append(...xs) { for (const x of xs) this.kids.push(x); },
    appendChild(x) { this.kids.push(x); },
    replaceChildren(...xs) { this.kids = xs; },
    scrollIntoView() {},
    scrollTop: 0, scrollHeight: 0,
    get lastElementChild() { return this.kids[this.kids.length - 1]; },
  });
}

const byId = {};
for (const id of ['timeline', 'stepCount', 'sendBtn', 'input', 'thread', 'threadCount',
                  'threadBadge', 'approvalCard', 'approvalBody', 'resultCard', 'resultBody',
                  'samples', 'modeBadge', 'agentName', 'logoutBtn']) byId[id] = makeEl('div');

const ctx = {
  Node,
  document: { createElement: makeEl, createTextNode: (t) => ({ text: String(t) }),
              getElementById: (id) => byId[id] || makeEl('div') },
  // 令牌取不到 → boot() 直接跳登录页返回，不碰 network
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  location: { replace() {} },
  fetch: () => { throw new Error('测试里不发请求'); },
  console, TextDecoder, JSON, Number, String, Boolean, Object, Array, Date, Promise,
};
ctx.globalThis = ctx;
vm.createContext(ctx);

vm.runInContext(
  readFileSync(new URL('../../src/webapp/static/app.js', import.meta.url), 'utf8') +
  '\n;globalThis.__t = { addStep, resetPanels, state, $ };', ctx);
const { addStep, resetPanels, state } = ctx.__t;

// 与后端 progress.STAGE_LABELS 一致：真实事件里 label 是中文，stage 是英文标识
const LABEL = { load_data: '加载业务数据', intent: '意图识别', fanout: '并发调用业务接口',
                normalize: '字段归一', node_done: '完成', result_confirm: '结果确认' };
const P = (stage, detail = '') =>
  ({ stage, label: LABEL[stage] || stage, detail, ts: '00:00:00' });
let fail = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fail += 1;
  console.log(`  ${ok ? '✓' : '✗'} ${name}` +
              (ok ? '' : `  期望 ${JSON.stringify(want)}，实际 ${JSON.stringify(got)}`));
};

const timeline = () => byId.timeline;
const groups = () => timeline().kids.filter((n) => String(n.className).includes('group'));
const rowText = (n) => n.kids[1].kids.map((k) => k.textContent).join('');

// ---------- 复刻一次订单链路：7 装载 + 1 意图 + 2 并发 + 24 归一 + 1 完成 + 1 确认 ----------
resetPanels();
['欢迎语', '坐席信息', '标签体系', '客户信息与画像', '会话记录', '客服会话', '订单记录']
  .forEach((d) => addStep(P('load_data', d)));
addStep(P('intent', '枚举精确匹配 → 语义识别 → 兜底'));
addStep(P('fanout', '准备并行调用订单主数据 / 物流 / 库存接口'));
addStep(P('fanout', '物流已取到（顺丰速运 已签收），库存 1 条'));
for (let i = 1; i <= 24; i++) addStep(P('normalize', `[订单中台] f${i} → F${i}`));
addStep(P('node_done', '订单 SO20260320008 已签收'));
addStep(P('result_confirm'));

const group = groups()[0];
const detailBox = group.kids[1].kids[1];
const head = group.kids[1].kids[0];

console.log('【折叠行为】');
check('步数 36', state.steps, 36);
check('行数 13（36 步折成 13 行）', state.rows, 13);
check('时间线占 13 行', timeline().kids.length, 13);
check('计数文案', byId.stepCount.textContent, '（13 行 / 36 步）');
check('load_data 7 行仍是 7 行（未被折）', timeline().kids.slice(0, 7).length, 7);
check('其中每行都带自己的 detail',
      timeline().kids.slice(0, 7).map(rowText),
      ['加载业务数据：欢迎语', '加载业务数据：坐席信息', '加载业务数据：标签体系',
       '加载业务数据：客户信息与画像', '加载业务数据：会话记录', '加载业务数据：客服会话',
       '加载业务数据：订单记录']);
check('折叠行只有 1 行', groups().length, 1);
check('折叠行表头', head.kids[1].textContent, '字段归一');
check('折叠行计数 ×24', head.kids[2].textContent, '×24');
check('明细攒了 24 条', detailBox.kids.length, 24);
check('明细内容完整', detailBox.kids[0].textContent, '[订单中台] f1 → F1');
check('明细默认藏起来', detailBox.hidden, true);

console.log('\n【点开 / 收起】');
head.handlers.click();
check('点一下展开', detailBox.hidden, false);
check('游标变向下', head.kids[0].textContent, '▾');
head.handlers.click();
check('再点收起', detailBox.hidden, true);

console.log('\n【中间夹了别的阶段 → 各占一行】');
addStep(P('normalize', '第二段 a → A'));
addStep(P('normalize', '第二段 b → B'));
check('第一行没被继续加进去', head.kids[2].textContent, '×24');
check('第二段自开一行', state.rows, 14);
check('两个折叠行', groups().length, 2);
check('第二个计数 ×2', groups()[1].kids[1].kids[0].kids[2].textContent, '×2');

console.log('\n【连续同名但不在白名单 → 不折】');
resetPanels();
for (let i = 0; i < 5; i++) addStep(P('node_done', `第 ${i} 步`));
check('连续 5 条仍是 5 行', state.rows, 5);
check('没有折叠行', groups().length, 0);

console.log('\n【出错也走同一套计数】');
addStep({ stage: 'error', label: '执行出错', detail: '演示' });
check('错误占一行', state.rows, 6);
check('计数文案', byId.stepCount.textContent, '（6 行 / 6 步）');

console.log(fail ? `\n${fail} 项未通过` : '\n全部通过');
process.exit(fail ? 1 : 0);
