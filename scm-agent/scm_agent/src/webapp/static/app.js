/* 坐席工作台前端。
 *
 * 接口地址一律用**绝对路径** `/scm/...`：页面本身挂在 `/scm/workbench` 下，
 * 用相对路径会拼成 `/scm/workbench/scm/chat/stream`。静态资源则相反，必须相对。
 *
 * 为什么不用 EventSource
 * ---------------------
 * EventSource 不能自定义请求头，而这里的每个接口都要 `Authorization: Bearer`。
 * 所以用 `fetch` + `response.body.getReader()` 手动切 SSE 帧 —— 多写二十行，
 * 换来的是令牌不必塞进 URL 查询串（那会进浏览器历史、代理日志和 Referer）。
 *
 * 为什么前端不做轮询
 * ----------------
 * 中断本身就是事件流里的一个终态事件（`interrupt`），流结束即代表「停下来了」。
 * 旧版每 3 秒问一次 `/api/get_interrupt`，是因为图当时跑在平台的另一个进程里，
 * 页面无从感知它停没停。
 */

'use strict';

const API = {
  health: '/scm/health',
  samples: '/scm/samples',
  chat: '/scm/chat/stream',
  resume: '/scm/chat/resume',
  state: (id) => '/scm/state/' + encodeURIComponent(id),
};

const KEY = { token: 'scm_token', user: 'scm_user', thread: 'scm_last_thread' };

const $ = (id) => document.getElementById(id);

// ==================== 小工具 ====================

/** 建 DOM。数据来自图（客户原话、模型输出），一律走 textContent，不拼 innerHTML。 */
function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

const now = () => new Date().toLocaleTimeString('zh-CN', { hour12: false });

function authHeaders(extra) {
  const token = localStorage.getItem(KEY.token);
  return Object.assign({ Authorization: 'Bearer ' + token }, extra || {});
}

/** 401 直接回登录页：令牌过期后继续待在页面上，每个操作都只会得到一串红字。 */
function guard(res) {
  if (res.status !== 401) return;
  localStorage.removeItem(KEY.token);
  location.replace('login.html');
  throw new Error('登录已过期');
}

// ==================== SSE ====================

/** 把一个 SSE 帧（`event:` / `data:` 两行）解析成 {name, data}。 */
function parseFrame(raw) {
  let name = 'message';
  let data = '';
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) name = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (!data) return null;
  try {
    return { name, data: JSON.parse(data) };
  } catch {
    return null; // 半截帧或非 JSON，丢掉即可 —— 下一帧还是完整的
  }
}

/** 逐帧消费 SSE，交给 onEvent 处理。 */
async function consumeSSE(res, onEvent) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // 帧之间用空行分隔；chunk 边界可能正好落在帧中间，所以必须缓冲
    let idx;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const frame = parseFrame(buffer.slice(0, idx));
      buffer = buffer.slice(idx + 2);
      if (frame) onEvent(frame);
    }
  }
}

// ==================== 页面状态 ====================

const state = {
  threadId: null,
  running: false,
  steps: 0,        // 收到的进度事件总数
  rows: 0,         // 时间线上实际占的行数（折叠后会少于步数）
  group: null,     // 当前展开中的折叠行，见 addStep
  messages: 0,
};

function setRunning(on) {
  state.running = on;
  $('sendBtn').disabled = on;
  $('sendBtn').textContent = on ? '执行中…' : '发送';
}

function setThread(id) {
  state.threadId = id;
  if (id) localStorage.setItem(KEY.thread, id);
  const badge = $('threadBadge');
  badge.textContent = id ? '会话 ' + id.replace(/^scm-/, '').slice(0, 12) : '新会话';
}

function resetPanels() {
  state.steps = 0;
  state.rows = 0;
  state.group = null;
  $('timeline').replaceChildren(el('div', { class: 'empty', text: '等待发起。' }));
  $('stepCount').textContent = '';
  $('approvalCard').hidden = true;
  $('approvalBody').replaceChildren();
  $('resultCard').hidden = true;
  $('resultBody').replaceChildren();
}

// ==================== 渲染 ====================

function addMessage(role, text) {
  const thread = $('thread');
  if (state.messages === 0) thread.replaceChildren();     // 去掉「还没有会话」的占位
  state.messages += 1;
  $('threadCount').textContent = '（' + state.messages + ' 条）';

  thread.append(el('div', { class: 'msg ' + role },
    el('div', { class: 'bubble' },
      el('div', { text }),
      el('div', { class: 'meta', text: now() }))));
  thread.lastElementChild.scrollIntoView({ block: 'nearest' });
}

// 少数几步不是「又走了一步」，而是链路里的关键判断，用颜色把它们从流水账里挑出来
const TONE = {
  handoff: 'warn',
  idempotent_replay: 'warn',
  report_verify_failed: 'warn',
  refund_submitted: 'ok',
  policy_match: 'ok',
  report_built: 'ok',
};

// 少数阶段天生啰嗦：`normalize`（字段归一）是把整张字段映射表逐条上报的，
// 订单链路一次能刷 24 行，把整条链路淹掉。这类阶段折成一行「字段归一 ×24」，
// 点开看逐条。
//
// 为什么用白名单而不是「连续 ≥N 条同名就折」：数据装载那 7 行也同名，但每行说的是
// **不同的数据源**（欢迎语 / 坐席 / 标签体系 / 客户 / 会话 / 客服会话 / 订单），
// 折起来正好丢掉「统一状态模型汇多源数据」这件事 —— 而那是要展示的重点。
const COLLAPSIBLE = new Set(['normalize']);

/** 普通的一行。 */
function stepRow(ev) {
  const row = el('div', { class: 'step tone-' + (TONE[ev.stage] || 'info') },
    el('div', { class: 'dot' }),
    el('div', {},
      el('span', { class: 'label', text: ev.label || ev.stage || '进度' }),
      ev.detail ? el('span', { class: 'detail', text: '：' + ev.detail }) : null));

  // 事件可以带 depth 表示「这是子图里的第几层」。目前 emit() 都发 0，
  // 留着是因为终端那边（quickstart 的 _print_events）用的是同一套字段 ——
  // 两边一起缩进，才不会一个有层次一个没层次。
  const depth = Number(ev.depth || 0);
  if (depth) row.style.paddingLeft = depth * 16 + 'px';
  return row;
}

/** 折叠行：一行表头 +（默认藏起来的）明细。明细边来边塞，点表头展开。 */
function groupRow(first) {
  const count = el('b', { class: 'count' });
  const caret = el('span', { class: 'caret', text: '▸' });
  const details = el('div', { class: 'group-details' });
  details.hidden = true;

  const toggle = () => {
    details.hidden = !details.hidden;
    caret.textContent = details.hidden ? '▸' : '▾';
  };
  const head = el('div', { class: 'group-head', onclick: toggle },
    caret, el('span', { class: 'label', text: first.label || first.stage }), count);

  const root = el('div', { class: 'step tone-info group' },
    el('div', { class: 'dot' }), el('div', {}, head, details));

  const group = {
    stage: first.stage,
    root,
    add(ev) {
      group.n += 1;
      count.textContent = '×' + group.n;
      details.append(el('div', { class: 'group-item', text: ev.detail || '' }));
    },
    n: 0,
  };
  group.add(first);
  return group;
}

function addStep(ev) {
  const timeline = $('timeline');
  if (state.steps === 0) timeline.replaceChildren();   // 去掉「等待发起」的占位
  state.steps += 1;

  // 同一段连续的可折叠阶段并进上一行；换个阶段就开新的一行
  if (COLLAPSIBLE.has(ev.stage) && state.group && state.group.stage === ev.stage) {
    state.group.add(ev);
  } else {
    state.group = COLLAPSIBLE.has(ev.stage) ? groupRow(ev) : null;
    timeline.append(state.group ? state.group.root : stepRow(ev));
    state.rows += 1;
  }

  $('stepCount').textContent = '（' + state.rows + ' 行 / ' + state.steps + ' 步）';
  timeline.scrollTop = timeline.scrollHeight;
}

/** 判断审批卡片的形态 —— 三种中断的载荷字段不同。 */
function approvalKind(v) {
  if (!v || typeof v !== 'object') return 'generic';
  if (v.plan_id || v.type === 'after_sale_approval') return 'aftersale';
  if (v.old_tags || v.new_tags) return 'tag';
  if (v.old_profile || v.new_profile) return 'profile';
  return 'generic';
}

function kv(pairs) {
  const list = el('dl', { class: 'kv' });
  for (const [k, v] of pairs) {
    if (v === null || v === undefined || v === '') continue;
    list.append(el('dt', { text: k }), el('dd', { text: String(v) }));
  }
  return list;
}

function tagChips(items, cls) {
  return (items || []).map((t) => el('span', { class: 'tag ' + (cls || '') },
    t.tag_name || t.tag_id || '?',
    t.tag_reason ? el('span', { class: 'why', text: t.tag_reason }) : null));
}

/** 画像是新旧两份清单，按 item_name 对齐成「改动表」，只把变化的行标出来。 */
function profileTable(oldItems, newItems) {
  const oldMap = new Map((oldItems || []).map((i) => [i.item_name, i.item_value]));
  const table = el('table', { class: 'mini' },
    el('thead', {}, el('tr', {},
      el('th', { text: '画像项' }), el('th', { text: '原值' }), el('th', { text: '新值' }))));

  const body = el('tbody');
  let changed = 0;
  for (const item of newItems || []) {
    const before = oldMap.get(item.item_name);
    const isNew = !oldMap.has(item.item_name);
    const diff = isNew || String(before) !== String(item.item_value);
    if (diff) changed += 1;
    body.append(el('tr', { style: diff ? 'background:#f3fbf5' : '' },
      el('td', { class: 'k', text: item.item_name }),
      el('td', { text: isNew ? '（新增）' : (before || '—') }),
      el('td', { text: item.item_value || '—' })));
  }
  table.append(body);
  return el('div', {}, table,
    el('p', { class: 'explain', text: '共 ' + (newItems || []).length + ' 项，其中 ' + changed + ' 项有变化。' }));
}

function renderApproval(payload) {
  const body = $('approvalBody');
  const kind = approvalKind(payload);
  const nodes = [el('p', { class: 'desc', text: payload.description || '需要你的确认' })];

  if (kind === 'aftersale') {
    nodes.push(kv([
      ['方案', payload.plan_id],
      ['订单', payload.order_id],
      ['处理方式', payload.refund_type],
      ['退款金额', payload.amount != null ? payload.amount + ' 元' : ''],
    ]));
    const trace = el('ul', { class: 'trace' });
    for (const line of payload.reason_trace || []) trace.append(el('li', { text: line }));
    if (payload.reason_trace && payload.reason_trace.length) {
      nodes.push(el('div', { class: 'explain', text: '判定依据（政策引擎逐条比对，✓ 命中 / ✗ 未命中）：' }));
      nodes.push(trace);
    }
    nodes.push(el('p', { class: 'explain', text:
      '⚠ 金额超过自动处理额度。批准后由独立的执行节点发起退款；' +
      '中断节点本身没有副作用，所以重复提交不会重复退款。' }));
  } else if (kind === 'tag') {
    const oldTags = (payload.old_tags || {}).customer_tags || [];
    const add = (payload.new_tags || {}).tag_ids_add || [];
    const remove = (payload.new_tags || {}).tag_ids_remove || [];
    nodes.push(el('div', { class: 'explain', text: '当前标签' }));
    nodes.push(el('div', {}, tagChips(oldTags)));
    nodes.push(el('div', { class: 'explain', text: '本次新增' }));
    nodes.push(el('div', {}, add.length ? tagChips(add, 'add') : el('span', { class: 'explain', text: '（无）' })));
    if (remove.length) {
      nodes.push(el('div', { class: 'explain', text: '本次移除' }));
      nodes.push(el('div', {}, tagChips(remove, 'remove')));
    }
  } else if (kind === 'profile') {
    nodes.push(profileTable(
      (payload.old_profile || {}).profile_items,
      (payload.new_profile || {}).profile_items));
  } else {
    nodes.push(el('pre', { class: 'trace', text: JSON.stringify(payload, null, 2) }));
  }

  const buttons = el('div', { class: 'actions' },
    el('button', { class: 'primary', text: '确认', onclick: () => decide('ok') }),
    el('button', { text: '放弃', onclick: () => decide('discard') }),
    el('button', { text: '重新生成', onclick: () => decide('recreate') }));

  nodes.push(buttons);
  body.replaceChildren(...nodes);
  $('approvalCard').hidden = false;
  $('approvalCard').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function renderResult(result, threadId) {
  if (!result) return;
  const body = $('resultBody');
  const ok = Number(result.task_result_code) === 0;
  body.replaceChildren(
    el('div', { class: 'answer', text: result.task_result || '（无输出）' }),
    result.task_result_explain ? el('div', { class: 'explain', text: result.task_result_explain }) : null,
    el('div', { class: 'explain', text: (ok ? '✓ 结果有效' : '✗ 结果无效') + '　会话 ' + (threadId || '') }),
  );
  $('resultCard').hidden = false;
}

function showError(message) {
  addStep({ stage: 'error', label: '执行出错', detail: message, depth: 0 });
  addMessage('bot', '⚠ 执行出错：' + message);
}

// ==================== 动作 ====================

async function readError(res) {
  try {
    const body = await res.json();
    return body.detail || JSON.stringify(body);
  } catch {
    return res.status + ' ' + res.statusText;
  }
}

function handleEvent(frame) {
  const { name, data } = frame;
  if (name === 'open') {
    setThread(data.thread_id);
  } else if (name === 'progress') {
    addStep(data);
  } else if (name === 'interrupt') {
    setThread(data.thread_id);
    const first = (data.interrupts || [])[0];
    if (first) renderApproval(first.value || {});
  } else if (name === 'done') {
    setThread(data.thread_id);
    $('approvalCard').hidden = true;
    const answer = (data.task_result || {}).task_result;
    renderResult(data.task_result, data.thread_id);
    addMessage('bot', answer || '（本轮没有产生最终答复）');
  } else if (name === 'error') {
    showError(data.message || '未知错误');
  }
}

/** 一套「发一次请求 + 消费流 + 收尾」的公共流程。 */
async function runStream(url, body) {
  if (state.running) return;
  setRunning(true);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    if (!res.ok) { guard(res); throw new Error(await readError(res)); }
    await consumeSSE(res, handleEvent);
  } catch (e) {
    showError(e.message || String(e));
  } finally {
    setRunning(false);
  }
}

function send() {
  const input = $('input');
  const text = input.value.trim();
  if (!text || state.running) return;

  resetPanels();
  addMessage('user', text);
  input.value = '';
  setThread(null);                       // 新一轮 = 新线程，避免上一轮的状态混进来

  runStream(API.chat, { task_input: text });
}

function decide(decision) {
  if (!state.threadId || state.running) return;
  addMessage('user', { ok: '确认', discard: '放弃', recreate: '重新生成' }[decision] + '（人工审批）');
  $('approvalCard').hidden = true;

  runStream(API.resume, { thread_id: state.threadId, decision });
}

/** 刷新后把界面恢复成中断前的样子 —— 否则审批卡片一刷新就没了。 */
async function restore() {
  const threadId = localStorage.getItem(KEY.thread);
  if (!threadId) return;
  try {
    const res = await fetch(API.state(threadId), { headers: authHeaders() });
    if (!res.ok) return;
    const data = await res.json();
    setThread(threadId);

    if (data.interrupted) {
      const first = (data.interrupts || [])[0];
      addMessage('bot', '上一轮停在人工审批，已为你恢复审批卡片。');
      if (first) renderApproval(first.value || {});
    } else if (data.task_result) {
      renderResult(data.task_result, threadId);
    }
  } catch {
    /* 恢复失败不算错误：就当一个干净的新页面 */
  }
}

async function loadHealth() {
  try {
    const res = await fetch(API.health);
    const data = await res.json();
    // 三个开关直接摆在顶栏：截图时不用解释「这份跑的是离线还是真实模型」
    $('modeBadge').replaceChildren(
      document.createTextNode('LLM '), el('b', { text: data.llm_mode }),
      document.createTextNode(' · 业务接口 '), el('b', { text: data.integration_mode }),
      document.createTextNode(' · 工具 '), el('b', { text: data.tool_gateway }));
  } catch {
    $('modeBadge').textContent = '模式未知';
  }
}

async function loadSamples() {
  try {
    const res = await fetch(API.samples, { headers: authHeaders() });
    if (!res.ok) return;
    const { samples } = await res.json();
    const box = $('samples');
    box.replaceChildren(...samples.map((s) => el('button', {
      class: 'chip',
      text: s.title,
      title: s.desc + '\n\n' + s.input,
      onclick: () => { $('input').value = s.input; send(); },
    })));
  } catch {
    /* 示例清单加载失败不影响手输 */
  }
}

// ==================== 启动 ====================

(function boot() {
  if (!localStorage.getItem(KEY.token)) {
    location.replace('login.html');
    return;
  }
  $('agentName').textContent = localStorage.getItem(KEY.user) || '坐席';

  $('sendBtn').addEventListener('click', send);
  $('logoutBtn').addEventListener('click', () => {
    localStorage.removeItem(KEY.token);
    localStorage.removeItem(KEY.thread);
    location.replace('login.html');
  });
  $('input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send();
  });

  loadHealth();
  loadSamples();
  restore();
})();
