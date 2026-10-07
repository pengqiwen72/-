/* 登录页：账号口令换 JWT，存 localStorage 后进工作台。
 *
 * 令牌只存在浏览器本地（localStorage），服务端不维护会话表 ——
 * 校验靠签名和过期时间，服务重启不需要「重新登录所有人」。
 * 代价是登出只能由前端清掉令牌；要做服务端吊销，得再引一层黑名单，
 * 对 demo 来说不值当。
 */

'use strict';

const KEY = { token: 'scm_token', user: 'scm_user', thread: 'scm_last_thread' };

const $ = (id) => document.getElementById(id);

function showError(message) {
  const box = $('error');
  box.textContent = message;
  box.hidden = false;
}

async function login(event) {
  event.preventDefault();
  const btn = $('submitBtn');
  const userId = $('userId').value.trim();
  const password = $('password').value;

  btn.disabled = true;
  btn.textContent = '登录中…';
  $('error').hidden = true;

  try {
    const res = await fetch('/scm/get_token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: userId, password }),
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      // 403 是账号口令不对，401 是别的鉴权问题；对使用者都是「登不进去」
      showError(body.detail || ('登录失败（' + res.status + '）'));
      return;
    }

    const data = await res.json();
    localStorage.setItem(KEY.token, data.token);
    localStorage.setItem(KEY.user, data.user_id || userId);
    // 上一轮的会话不能跨人恢复，换人登录就清掉
    localStorage.removeItem(KEY.thread);

    location.replace('index.html');
  } catch (e) {
    showError('无法连接后端服务：' + (e.message || e));
  } finally {
    btn.disabled = false;
    btn.textContent = '登录';
  }
}

$('loginForm').addEventListener('submit', login);
$('userId').focus();
