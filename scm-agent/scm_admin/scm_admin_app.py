#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审批看板（Flask，默认 :5000）。

**单页只读**：一页审批明细 + 顶部的一次通过率。没有登录、没有写接口 ——
审批动作发生在演示工作台（interrupt → 人工决策那一步），这里只是把
「谁批的、第几轮批的、有没有返工」摊开看。

数据不在本地库里，全部来自平台服务 Store 的 `("approval",)` 命名空间
（由 `integrations/idempotency.py::record_approval` 写入）。因此它需要
平台服务在跑：

    scm_agent/run_scm_agent        # langgraph dev，:2024
    scm_admin/run_scm_admin        # 本看板，:5000

只想看智能体本身的话不用起这个：根目录 `quickstart.py` 离线就能跑通全部链路。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template

from scm_store_api.scm_store_api import ScmStoreAPI, create_scm_store_api

load_dotenv()

app = Flask(__name__)

#: 结论的显示文案（与 `sub_purchase_request_node.py::_ACTION_TEXT` 同一套叫法）
DECISION_TEXT = {
    "approved": "通过",
    "rejected": "驳回",
    "recreated": "改判重报",
}

#: 链路名 → 显示文案（`record_approval(kind=...)` 写进来的那个值）
KIND_TEXT = {
    "purchase": "采购申请",
    "receipt": "收货复核",
}


class _LazyStoreClient:
    """首次用到时才去连平台的 Store。

    ⚠️ 不能在模块导入时就连：`create_scm_store_api` 一上来就要
    SCM_SERVER_URL / SCM_USER_ID / SCM_PASSWORD，缺一个就抛 ValueError ——
    那样连「打开页面看一眼」都做不到，而且报错完全看不出是配置问题。
    现在连接推迟到第一个请求，缺哪些变量直接写进错误信息里。
    """

    def __init__(self):
        self._client: Optional[ScmStoreAPI] = None

    def _connect(self) -> ScmStoreAPI:
        if self._client is None:
            missing = [n for n in ("SCM_SERVER_URL", "SCM_USER_ID", "SCM_PASSWORD") if not os.getenv(n)]
            if missing:
                raise RuntimeError(
                    f'缺少配置 {", ".join(missing)}：请复制 .env.example 为 .env 并填写，'
                    "同时确认平台服务（run_scm_agent）已启动"
                )
            self._client = create_scm_store_api(
                url=os.getenv("SCM_SERVER_URL"),
                user_id=os.getenv("SCM_USER_ID"),
                password=os.getenv("SCM_PASSWORD"),
            )
        return self._client

    def __getattr__(self, name):
        return getattr(self._connect(), name)


client = _LazyStoreClient()


# ==================== 聚合口径 ====================

def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把审批记录聚合成看板要的两块：明细行 + 顶部统计。

    口径（页脚会把这几行原样写给看的人，否则「一次通过率」很容易被误读）：

    * **一次通过** = 第 0 轮（`revision == 0`）的结论就是 approved。
      改判重报（recreated）不算一次通过 —— 那一单是被退回来改了才继续走的。
    * **一次通过率** = 一次通过数 / 申请总数，两边都只数第 0 轮
      （一轮申请算一次，第二轮不重复计数）。
    * **返工次数** = 同一申请在 r1 及以后出现的审批记录条数。

    明细按时间倒序（最近的在最上面），每行补上显示用的文案与两个派生列。
    """
    rework: Dict[str, int] = {}
    for item in records:
        if int(item.get("revision") or 0) > 0:
            plan_key = f"{item.get('thread_id')}:{item.get('plan_id')}"
            rework[plan_key] = rework.get(plan_key, 0) + 1

    rows: List[Dict[str, Any]] = []
    first_round = 0
    first_pass = 0
    for item in records:
        revision = int(item.get("revision") or 0)
        decision = str(item.get("decision") or "")
        plan_key = f"{item.get('thread_id')}:{item.get('plan_id')}"
        is_first_pass = revision == 0 and decision == "approved"
        if revision == 0:
            first_round += 1
            first_pass += 1 if is_first_pass else 0
        rows.append({
            **item,
            "revision": revision,
            "round_text": "第 %d 轮" % (revision + 1),
            "decision_text": DECISION_TEXT.get(decision, decision or "—"),
            "kind_text": KIND_TEXT.get(str(item.get("kind") or ""), str(item.get("kind") or "—")),
            "is_first_pass": is_first_pass,
            "rework": rework.get(plan_key, 0),
        })

    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    stats = {
        "plans": first_round,
        "first_pass": first_pass,
        "first_pass_rate": round(first_pass / first_round, 4) if first_round else None,
        "reworked_plans": len(rework),
    }
    return {"records": rows, "stats": stats}


# ==================== 页面与接口 ====================

@app.route("/")
def index():
    """看板页（单页）。数据由页面里的 fetch 拉 `/api/approvals`。"""
    return render_template("approvals.html")


@app.route("/api/approvals", methods=["GET"])
def api_approvals():
    """全部审批记录 + 顶部统计。

    读失败（平台没起、配置缺失、令牌不对）一律走同一条错误路径：把原因
    原样回给页面 —— 这个看板最常见的故障就是「平台服务没起」，错误信息里
    直接写出来，比让人去看控制台日志快得多。
    """
    try:
        records = client.list_approvals()
    except Exception as e:
        app.logger.warning(f"读取审批记录失败：{e}")
        return jsonify({"success": False, "error": str(e)}), 200

    return jsonify({"success": True, "data": summarize(records)})


if __name__ == "__main__":
    port = int(os.getenv("SCM_ADMIN_PORT", "5000") or 5000)
    app.logger.info(f"审批看板启动：http://127.0.0.1:{port}（单页只读）")
    app.run(host="0.0.0.0", port=port, debug=False)
