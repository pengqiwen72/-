# Mini Agent - 从零手写完整 Agent Runtime

> 纯原生 Python 实现 · 零 Agent 框架依赖 · 完整 ReAct 循环

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

## 📖 项目简介

这是一个从零开始手写的 AI Agent 运行时系统，**完全禁止使用任何 Agent 框架**（如 langgraph、crewai、openhands 等），所有核心逻辑（Agent 循环、工具调度、会话管理、上下文控制）全部原生 Python 实现。

### ✨ 核心特性

- 🔄 **自研 ReAct 循环**：完整的「思考-行动-观察」闭环
- 🛠️ **统一工具注册机制**：基于 JSON Schema 的自主工具调用
- 👥 **多 Session 隔离**：多窗口独立对话，互不干扰
- 📝 **上下文精细管理**：轮次限制 + 超长自动压缩
- 📊 **完整 Trace 日志**：工具调用、执行耗时、异常追踪
- 🔌 **多 LLM 支持**：兼容 OpenAI / DeepSeek / Qwen 等

---

## 🏗️ 系统架构

### 整体架构图
