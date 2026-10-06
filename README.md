# 项目集 · Projects

本仓库收录两个可直接运行的项目。各自独立成目录、各有完整的 README：

| 目录 | 项目 | 一句话 | 技术栈 |
|---|---|---|---|
| [`ecom-cs-agent/`](ecom-cs-agent/) | **电商智能客服与运营助手** | LangGraph 多链路客服智能体：RAG 商品咨询、订单多系统编排、售后政策引擎 + 人工审批 + 幂等、运营周报；clone 后零配置离线跑通全部链路 | Python · LangGraph · FastAPI · Pydantic |
| [`storyboard-studio/`](storyboard-studio/) | **分镜工坊** | 把小说文本翻译成**可执行的镜头语言**：先做叙事层的节拍分解，再做执行层的镜头设计，分镜师可在界面上改到能用为止 | Next.js · TypeScript |

## 快速开始

### 电商智能客服与运营助手

```bash
cd ecom-cs-agent
uv sync --project cs_agent                         # 装依赖（需要 uv，这一步要联网）
uv run --project cs_agent python quickstart.py     # 13 个场景一次跑完（离线、零配置、不联网）
```

### 分镜工坊

```bash
cd storyboard-studio
npm install && npm run dev
```

线上演示：<https://story-pi-weld.vercel.app> —— Vercel 默认域名，境内访问需开代理。

## 说明

* 两个项目各自独立开发，提交历史都完整保留在本仓库（按各自目录前缀可追溯）。
* `ecom-cs-agent/` 的定位是「基于工作中同类系统的架构，抽取出的个人可运行 demo」，
  不是生产代码；演示数据全部虚构。详细说明见其目录下的 README。
