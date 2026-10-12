/* 演示工作台前端。
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

const KEY = { token: 'scm_token', user: 'scm_user', thread: 'scm_last_thread', todos: 'scm_todos' };

const $ = (id) => document.getElementById(id);

// ==================== 小工具 ====================

/** 建 DOM。数据来自图（用户原话、模型输出），一律走 textContent，不拼 innerHTML。 */
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
// （阶段名必须与 observability/progress.py 的 STAGE_LABELS 对齐：
//  写错的阶段名不会报错，只是这一行的圆点永远是灰的）
const TONE = {
  handoff: 'warn',
  degrade: 'warn',
  idempotent_replay: 'warn',
  idempotent_conflict: 'warn',
  hitl_rejected: 'warn',
  policy_match: 'ok',
  quality_check: 'ok',
  hitl_recreate: 'ok',
  purchase_submitted: 'ok',
  receipt_submitted: 'ok',
};

// 少数阶段天生啰嗦：同一个阶段名一次能刷几十行（字段归一那类「逐条上报映射表」
// 的阶段），把整条链路淹掉。这类阶段折成一行「×× ×24」，点开看逐条。
//
// 为什么用白名单而不是「连续 ≥N 条同名就折」：数据装载那 7 行也同名，但每行说的是
// **不同的数据源**（欢迎语 / 操作员 / 物料 / 供应商 / 仓库 / 库存 / 采购订单），
// 折起来正好丢掉「统一状态模型汇多源数据」这件事 —— 而那是要展示的重点。
//
// ⚠️ 当前是**空表**：现在的链路没有逐条刷屏的阶段（字段归一轨迹走了日志，
// 不再逐条上报）。机制留在 `addStep` 里也留了用例（`tests/frontend/timeline_check.mjs`
// 自己往表里放一个阶段来验折叠），将来哪条链路开始逐条上报，在这里登记一行即可。
const COLLAPSIBLE = new Set();

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

/** 判断审批卡片的形态。
 *
 * ⚠️ 先看 `type`，**不能**靠 `plan_id` 之类的字段碰运气：采购与质检两种中断
 * 都带 `plan_id`，拿它判别形态会把复核卡按采购卡的字段去渲染 —— 页面不报错，
 * 只是每个字段都错位（只有肉眼看才发现）。这张表只认 `type`，认不出来就落
 * 兜底（原样展示 JSON），至少不会把人引到错误的结论上。
 */
function approvalKind(v) {
  if (!v || typeof v !== 'object') return 'generic';
  if (v.type === 'purchase_approval') return 'purchase';
  if (v.type === 'quality_review') return 'quality';
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

const label = (name, code) => (name && code ? name + '（' + code + '）' : (name || code || ''));
const num = (v) => (typeof v === 'number' ? String(+v.toFixed(4)) : String(v == null ? '' : v));
const money = (v) => (typeof v === 'number' ? v.toFixed(2) + ' 元' : String(v == null ? '' : v));

/** 策略引擎的判定依据（✓ 命中 / ✗ 未命中）—— 两种卡片共用。 */
function reasonTrace(payload) {
  const lines = payload.reason_trace || [];
  if (!lines.length) return null;
  const trace = el('ul', { class: 'trace' });
  for (const line of lines) trace.append(el('li', { text: line }));
  return el('div', {},
    el('div', { class: 'explain', text: '判定依据（策略引擎逐条比对，✓ 命中 / ✗ 未命中）：' }),
    trace);
}

function purchaseCard(payload) {
  return [
    kv([
      ['方案', payload.plan_id],
      ['申请单', payload.biz_id],
      ['物料', label(payload.material_name, payload.material_code)],
      ['数量', payload.qty != null ? num(payload.qty) + (payload.unit || '') : ''],
      ['单价', money(payload.unit_price)],
      ['金额', money(payload.amount)],
      ['到货仓库', payload.target_warehouse],
      ['供应商', label(payload.supplier_name, payload.supplier_code)],
      ['预计到货', payload.expect_arrival],
      ['命中条款', payload.policy_id],
      ['轮次', payload.revision ? '第 ' + (payload.revision + 1) + ' 轮' : ''],
    ]),
    reasonTrace(payload),
    el('p', { class: 'explain', text:
      '⚠ 批准后由独立的执行节点下达采购订单；中断节点本身没有副作用，' +
      '所以重复提交不会重复下单。价格或供应商不合适时，用「改判重报」带上修改重走策略引擎。' }),
  ];
}

function qualityCard(payload) {
  return [
    kv([
      ['方案', payload.plan_id],
      ['收货单', payload.biz_id],
      ['采购订单', payload.po_no],
      ['物料', label(payload.material_name, payload.material_code)],
      ['到货仓库', payload.warehouse_code],
      ['供应商', payload.supplier_code],
      ['预计数量', num(payload.qty_expected)],
      ['实收数量', num(payload.qty_received)],
      ['不良数量', num(payload.qty_defect)],
      ['不良率', payload.defect_rate != null ? (payload.defect_rate * 100).toFixed(2) + '%' : ''],
      ['不良类型', payload.defect_type],
      ['当前处置', payload.disposition],
      ['命中条款', payload.policy_id],
      ['轮次', payload.revision ? '第 ' + (payload.revision + 1) + ' 轮' : ''],
    ]),
    reasonTrace(payload),
    el('p', { class: 'explain', text:
      '⚠ 不良率超阈值，入库前需要人工复核。批准按当前处置执行；驳回按整批退货处理；' +
      '也可以用「改判重报」换一种处置方式 —— 不良率是既有事实，复判仍会命中同一条款，' +
      '所以改判后会再确认一轮，绝不静默放行。' }),
  ];
}

/** 「改判重报」的小表单：字段与节点的 patch 白名单一致，没改动的字段不提交。 */
function patchForm(payload) {
  const kind = approvalKind(payload);
  const specs = kind === 'purchase' ? [
    { key: 'supplier_code', label: '供应商编码（如 S001）', value: payload.supplier_code || '' },
    { key: 'qty', label: '数量（同申请单位）', value: payload.qty != null ? num(payload.qty) : '', numeric: true },
    { key: 'target_warehouse', label: '到货仓库（如 WH01）', value: payload.target_warehouse || '' },
  ] : kind === 'quality' ? [
    { key: 'disposition', label: '处置方式', value: payload.disposition || '', options: ['入库', '让步接收', '退货', '换货'] },
  ] : [];
  if (!specs.length) return null;

  const inputs = new Map();
  const box = el('div', { class: 'patch' },
    el('div', { class: 'explain', text:
      '改判重报：只提交改动过的字段。白名单之外的字段节点会忽略并把原因写进轨迹，不会静默生效。' }));

  for (const spec of specs) {
    let field;
    if (spec.options) {
      field = el('select');
      for (const opt of spec.options) field.append(el('option', { value: opt, text: opt }));
      if (spec.options.includes(spec.value)) field.value = spec.value;
    } else {
      field = el('input', { type: 'text', value: spec.value });
    }
    inputs.set(spec.key, { spec, field });
    box.append(el('label', { class: 'patch-field' }, el('span', { text: spec.label }), field));
  }

  const submit = el('button', { class: 'primary', text: '提交改判' });
  submit.addEventListener('click', () => {
    const patch = {};
    for (const [key, { spec, field }] of inputs) {
      const value = String(field.value || '').trim();
      if (!value) continue;
      if (!spec.options && value === String(spec.value)) continue;      // 没改的字段不提交
      patch[key] = spec.numeric && !Number.isNaN(Number(value)) ? Number(value) : value;
    }
    if (!Object.keys(patch).length) {
      addStep({ stage: 'error', label: '改判重报', detail: '没有任何字段被改动，已取消提交' });
      return;
    }
    decide('recreate', patch);
  });

  box.append(el('div', { class: 'actions' }, submit));
  return box;
}

function renderApproval(payload) {
  const body = $('approvalBody');
  const kind = approvalKind(payload);
  const nodes = [el('p', { class: 'desc', text: payload.description || '需要你的确认' })];

  if (kind === 'purchase') nodes.push(...purchaseCard(payload));
  else if (kind === 'quality') nodes.push(...qualityCard(payload));
  else nodes.push(el('pre', { class: 'trace', text: JSON.stringify(payload, null, 2) }));

  let form = null;                       // 改判表单：点「改判重报」时才挂出来
  const toggle = () => {
    if (form) { form.remove(); form = null; return; }
    form = patchForm(payload);
    if (!form) { decide('recreate'); return; }   // 兜底卡片没有可改字段，直接重报
    body.insertBefore(form, buttons);
    form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  };

  const buttons = el('div', { class: 'actions' },
    el('button', { class: 'primary', text: '批准', onclick: () => decide('ok') }),
    el('button', { text: '驳回', onclick: () => decide('discard') }),
    el('button', { text: '改判重报', onclick: toggle }));

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
  } else if (name === 'todo') {
    saveTodo(data.item || {});
  } else if (name === 'done') {
    setThread(data.thread_id);
    dropTodo(data.thread_id);            // 这一单批完了，待办里不再挂着
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

function decide(decision, patch) {
  if (!state.threadId || state.running) return;
  const extra = patch && Object.keys(patch).length ? '　' + JSON.stringify(patch) : '';
  addMessage('user', { ok: '批准', discard: '驳回', recreate: '改判重报' }[decision] + '（人工审批）' + extra);
  $('approvalCard').hidden = true;

  const body = { thread_id: state.threadId, decision };
  if (patch && Object.keys(patch).length) body.patch = patch;
  runStream(API.resume, body);
}

// ==================== 待办（本地的） ====================

/* 待办清单只存在浏览器里（localStorage）。后端在 interrupt 之前不写任何状态
 * —— HITL 的第一条不变量就是「中断节点重放时无副作用」，为了记住待办去落一次
 * store 会让同一笔审批恢复两次就记两条。所以后端只推一条 `todo` 帧（纯渲染，
 * 见 chat_routes.todo_item），清单在此维护：收到帧写入、done / error 移除。
 */

function readTodos() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY.todos) || '[]');
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

function writeTodos(list) {
  localStorage.setItem(KEY.todos, JSON.stringify(list.slice(0, 20)));
  renderTodos();
}

function saveTodo(item) {
  const list = readTodos().filter((t) => t.thread_id !== item.thread_id);
  list.unshift(item);                        // 最新的一笔排最前（同一会话只留一条）
  writeTodos(list);
}

function dropTodo(threadId) {
  if (!threadId) return;
  const list = readTodos();
  const rest = list.filter((t) => t.thread_id !== threadId);
  if (rest.length !== list.length) writeTodos(rest);
}

function renderTodos() {
  const list = readTodos();
  const btn = $('todoBtn');
  const panel = $('todoPanel');
  btn.hidden = list.length === 0;
  $('todoCount').textContent = String(list.length);
  if (!list.length) {
    panel.hidden = true;
    panel.replaceChildren();
    return;
  }
  panel.replaceChildren(
    el('div', { class: 'todo-head', text: '待人工确认（' + list.length + '）' }),
    ...list.map((t) => el('div', { class: 'todo-item', onclick: () => openTodo(t.thread_id) },
      el('div', { class: 'todo-title', text: t.title || '待人工确认' }),
      el('div', { class: 'todo-detail', text: t.detail || '' }),
      el('div', { class: 'todo-meta', text: (t.ts || '') + '　会话 ' + String(t.thread_id || '').replace(/^scm-/, '').slice(0, 12) }))));
}

/** 点待办 = 把那个会话调回来（审批卡片重新渲染，接着批）。 */
async function openTodo(threadId) {
  if (state.running || !threadId) return;
  $('todoPanel').hidden = true;
  resetPanels();
  localStorage.setItem(KEY.thread, threadId);
  await restore();
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
    } else {
      // 这个会话已经不在等人的队伍里了（别处批过 / 已跑完）：待办里的那条是旧的
      dropTodo(threadId);
      if (data.task_result) renderResult(data.task_result, threadId);
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
  $('agentName').textContent = localStorage.getItem(KEY.user) || '操作员';

  $('sendBtn').addEventListener('click', send);
  $('logoutBtn').addEventListener('click', () => {
    localStorage.removeItem(KEY.token);
    localStorage.removeItem(KEY.thread);
    location.replace('login.html');
  });
  $('input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) send();
  });

  $('todoBtn').addEventListener('click', () => {
    const panel = $('todoPanel');
    if (panel.hidden) { renderTodos(); panel.hidden = false; } else { panel.hidden = true; }
  });
  // 点面板和按钮以外的地方收起待办
  document.addEventListener('click', (e) => {
    const panel = $('todoPanel');
    if (panel.hidden) return;
    if (e.target.closest('#todoPanel') || e.target.closest('#todoBtn')) return;
    panel.hidden = true;
  });

  renderTodos();
  loadHealth();
  loadSamples();
  restore();
})();
