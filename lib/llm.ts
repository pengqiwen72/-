import Anthropic from "@anthropic-ai/sdk";

import {
  buildAuditPrompt,
  buildBeatPrompt,
  buildRefinePrompt,
  buildSystemPrompt,
  buildUserPrompt,
} from "./prompt";
import { StreamSanitizer, parseRefineShots } from "./schema";
import type {
  AuditIssue,
  AuditResult,
  Beat,
  Medium,
  Shot,
  StreamEvent,
  Usage,
} from "./types";

/**
 * 模型调用层。
 *
 * 只做三件事：拿凭据、发起流式请求、把 SDK 的原始事件翻译成产品自己的
 * StreamEvent 协议。业务规则（怎么切镜头）在 prompt.ts，数据清洗在
 * schema.ts，这里保持薄。
 */

export const MODEL = process.env.ANTHROPIC_MODEL?.trim() || "claude-opus-5";

/** 分镜设计需要一定的权衡（景别、节奏、取舍），给 medium 而不是 low。 */
const EFFORT = (process.env.ANTHROPIC_EFFORT?.trim() || "medium") as
  | "low"
  | "medium"
  | "high"
  | "xhigh"
  | "max";

/**
 * `thinking` / `output_config` 是 Anthropic 的专有参数。
 * 通过 ANTHROPIC_BASE_URL 接入第三方兼容网关（例如 DeepSeek）时，
 * 带上它们会直接 400。所以只在确认是 Claude 模型时才发送，
 * 让同一份代码既能跑官方 API，也能跑兼容网关。
 */
function isClaudeModel(): boolean {
  return MODEL.startsWith("claude-");
}

/** 没配 Key 时静默降级到内置样例，而不是抛错——产品要能被打开展示。 */
export function hasCredentials(): boolean {
  return Boolean(
    process.env.ANTHROPIC_API_KEY?.trim() ||
      process.env.ANTHROPIC_AUTH_TOKEN?.trim(),
  );
}

function makeClient(): Anthropic {
  const baseURL = process.env.ANTHROPIC_BASE_URL?.trim();
  return new Anthropic(baseURL ? { baseURL } : {});
}

/** 把可能很长的原文截断到可控长度，并告知截断了多少（不静默丢内容）。 */
const MAX_SOURCE_CHARS = 12000;

export function clampSource(text: string): { text: string; truncated: boolean } {
  if (text.length <= MAX_SOURCE_CHARS) return { text, truncated: false };
  return { text: text.slice(0, MAX_SOURCE_CHARS), truncated: true };
}

// ---------------------------------------------------------------------------

export interface GenerateOptions {
  text: string;
  medium: Medium;
  targetDurationSec: number;
}

/**
 * 实时生成：一次调用里先出节拍、再出镜头。
 * 用流式 NDJSON 而非等一整块 JSON，是为了让节拍和镜头能立刻上屏。
 */
export async function* generateLive(
  opts: GenerateOptions,
): AsyncGenerator<StreamEvent> {
  const client = makeClient();
  const { text: source, truncated } = clampSource(opts.text);

  if (truncated) {
    yield {
      t: "notice",
      message: `原文超过 ${MAX_SOURCE_CHARS} 字，本次只分析前 ${MAX_SOURCE_CHARS} 字。`,
    };
  }

  const maxTokens = Number(process.env.ANTHROPIC_MAX_TOKENS) || 32000;

  const stream = client.messages.stream({
    model: MODEL,
    max_tokens: maxTokens,
    ...(isClaudeModel()
      ? { thinking: { type: "adaptive" as const }, output_config: { effort: EFFORT } }
      : {}),
    system: [
      {
        type: "text",
        text: buildSystemPrompt(opts.medium),
        // 系统提示词是稳定前缀，缓存它能把多轮生成的输入成本压下来
        cache_control: { type: "ephemeral" },
      },
    ],
    messages: [
      { role: "user", content: buildUserPrompt(source, opts.targetDurationSec) },
    ],
  });

  const sanitizer = new StreamSanitizer(source);
  let sawAny = false;
  // 清洗层会静默丢弃解析不了的行——静默是给用户的，不是给开发者的。
  // 这里留一份样本，出问题时写进服务端日志，方便判断是模型跑偏还是解析器有 bug。
  const skipped: string[] = [];
  let rawChars = 0;
  const debug = process.env.ANTHROPIC_DEBUG === "1";

  // 推理型模型（以及开了 adaptive thinking 的 Claude）会先花一大段预算在
  // thinking 上，这段时间界面是全黑的。把思考过程做成增量进度吐出去，
  // 用户看到的是「导演正在权衡」，而不是一个转不动的圈。
  let thinkingChars = 0;
  let thinkingSent = 0;
  const THINKING_CAP = 1600;

  // NDJSON 要攒成完整的行才能解析，所以自己维护行缓冲。
  // 思考增量不参与解析——它只是进度，直接当事件转发出去。
  let buffer = "";
  let done = false;

  /** 解析一行。坏行只留样本不抛出：单个镜头解析失败不该让整次生成白费。 */
  const takeLine = (raw: string): StreamEvent | null => {
    const line = raw.trim();
    if (!line) return null;
    rawChars += line.length;
    if (debug) console.error("[raw]", line.slice(0, 300));
    const evt = sanitizer.parseLine(line);
    if (!evt) {
      if (skipped.length < 8) skipped.push(line.slice(0, 200));
      return null;
    }
    return evt;
  };

  for await (const event of stream) {
    if (event.type !== "content_block_delta") continue;
    const delta = event.delta;

    if (delta.type === "thinking_delta") {
      thinkingChars += delta.thinking.length;
      if (thinkingSent < THINKING_CAP) {
        const slice = delta.thinking.slice(0, THINKING_CAP - thinkingSent);
        thinkingSent += slice.length;
        if (slice) yield { t: "thinking", text: slice };
      }
      continue;
    }

    if (delta.type !== "text_delta") continue;
    buffer += delta.text;

    let idx: number;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const evt = takeLine(buffer.slice(0, idx));
      buffer = buffer.slice(idx + 1);
      if (!evt) continue;
      sawAny = true;
      // 看到 done 就收工：后面的内容我们不需要，也不必再往下读
      if (evt.t === "done") {
        done = true;
        break;
      }
      yield evt;
    }
    if (done) break;
  }

  // 流结束时缓冲里可能还剩一行没换行结尾的内容
  if (!done) {
    const evt = takeLine(buffer);
    if (evt) {
      sawAny = true;
      if (evt.t !== "done") yield evt;
    }
  }

  const finalMessage = await stream.finalMessage();

  if (!sawAny) {
    // 模型可能因为 stop_reason 异常、整段被拒、或者根本没按 NDJSON 输出
    const truncated = finalMessage.stop_reason === "max_tokens";
    console.error(
      `[generate] 没有可解析的镜头。stop_reason=${finalMessage.stop_reason} ` +
        `文本字符数=${rawChars} 思考字符数=${thinkingChars} ` +
        `usage=${JSON.stringify(finalMessage.usage)}\n--- 前几行样本 ---\n${skipped.join("\n")}\n---`,
    );

    const reason = truncated
      ? "模型输出被截断（达到 max_tokens），请缩短原文或调大 ANTHROPIC_MAX_TOKENS。"
      : finalMessage.stop_reason === "refusal"
        ? "模型拒绝了这次请求，请调整原文内容后重试。"
        : "模型没有按预期的 NDJSON 格式输出，本次结果无法解析。请重试，或改用指令遵循能力更强的模型（例如 claude-opus-5）。";
    yield { t: "error", message: reason };
    return;
  }

  yield { t: "done", usage: toUsage(finalMessage.usage) };
}

function toUsage(usage: Anthropic.Usage | undefined): Usage | undefined {
  if (!usage) return undefined;
  return {
    inputTokens: usage.input_tokens,
    outputTokens: usage.output_tokens,
    cacheReadTokens: usage.cache_read_input_tokens ?? 0,
  };
}

// ---------------------------------------------------------------------------

export interface RefineRequest {
  action: "rewrite" | "regenerate" | "split" | "merge";
  medium: Medium;
  beat?: { summary: string; emotion: string; intensity: number };
  shot: Pick<
    Shot,
    "shotSize" | "camera" | "description" | "dialogue" | "sfx" | "durationSec" | "rationale"
  >;
  next?: Pick<
    Shot,
    "shotSize" | "camera" | "description" | "dialogue" | "sfx" | "durationSec" | "rationale"
  >;
  sourceText: string;
}

/** 单镜精修。输出短、要即时反馈，所以不开思考、不流式。 */
export async function refineLive(
  req: RefineRequest,
): Promise<Array<Omit<Shot, "id" | "beatId">>> {
  const client = makeClient();
  const { text: source } = clampSource(req.sourceText);

  const response = await client.messages.create({
    model: MODEL,
    max_tokens: 4000,
    ...(isClaudeModel() ? { output_config: { effort: "low" as const } } : {}),
    system:
      "你是资深分镜师，只输出一行合法 JSON，不要 markdown 代码块，不要任何解释文字。",
    messages: [
      { role: "user", content: buildRefinePrompt({ ...req, sourceText: source }) },
    ],
  });

  if (response.stop_reason === "refusal") {
    throw new Error("模型拒绝了这次改写请求。");
  }

  const text = response.content
    .filter((b): b is Anthropic.TextBlock => b.type === "text")
    .map((b) => b.text)
    .join("");

  return parseRefineShots(text, source).shots;
}

// ---------------------------------------------------------------------------
// 按节拍重跑镜头
// ---------------------------------------------------------------------------

export interface BeatRegenRequest {
  medium: Medium;
  beat: { summary: string; emotion: string; intensity: number };
  shots: Array<
    Pick<
      Shot,
      "shotSize" | "camera" | "description" | "dialogue" | "sfx" | "durationSec" | "rationale"
    >
  >;
  targetDurationSec: number;
  sourceText: string;
}

/**
 * 重做一个节拍下的全部镜头。
 *
 * 输出协议刻意与单镜精修保持一致（一行 JSON，`{"t":"result","shots":[...]}`），
 * 这样清洗层可以整套复用：台词回原文逐字核验、景别归一化、时长钳制、
 * 以及所有 flags 标记，都不必重写一遍。
 */
export async function regenerateBeatLive(
  req: BeatRegenRequest,
): Promise<Array<Omit<Shot, "id" | "beatId">>> {
  const client = makeClient();
  const { text: source } = clampSource(req.sourceText);

  const response = await client.messages.create({
    model: MODEL,
    max_tokens: 8000,
    ...(isClaudeModel() ? { output_config: { effort: "medium" as const } } : {}),
    system:
      "你是资深分镜师，只输出一行合法 JSON，不要 markdown 代码块，不要任何解释文字。",
    messages: [
      { role: "user", content: buildBeatPrompt({ ...req, sourceText: source }) },
    ],
  });

  if (response.stop_reason === "refusal") {
    throw new Error("模型拒绝了这次重跑请求。");
  }

  const text = response.content
    .filter((b): b is Anthropic.TextBlock => b.type === "text")
    .map((b) => b.text)
    .join("");

  return parseRefineShots(text, source).shots;
}

// ---------------------------------------------------------------------------
// 忠实度自查
// ---------------------------------------------------------------------------

const SEVERITIES = new Set(["high", "medium", "low"]);

/**
 * 拿生成结果回原文对质。
 * 刻意用一次**独立调用**：让同一个上下文给自己打分，几乎必然打高分。
 */
export async function auditLive(
  sourceText: string,
  shots: Array<Pick<Shot, "id" | "beatId" | "shotSize" | "description" | "dialogue" | "rationale">>,
  beats: Array<Pick<Beat, "id" | "summary" | "emotion" | "intensity">>,
): Promise<AuditResult> {
  const client = makeClient();
  const { text: source } = clampSource(sourceText);

  const response = await client.messages.create({
    model: MODEL,
    max_tokens: 8000,
    ...(isClaudeModel() ? { output_config: { effort: "medium" as const } } : {}),
    system:
      "你是分镜指导，只核对分镜与原文的一致性。严格输出 NDJSON，每行一个 JSON 对象，不要代码块，不要解释。",
    messages: [
      { role: "user", content: buildAuditPrompt(source, shots, beats) },
    ],
  });

  if (response.stop_reason === "refusal") {
    throw new Error("模型拒绝了这次自查请求。");
  }

  const text = response.content
    .filter((b): b is Anthropic.TextBlock => b.type === "text")
    .map((b) => b.text)
    .join("");

  const issues: AuditIssue[] = [];
  let reviewed = shots.length;
  let verdict = "";

  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line.startsWith("{")) continue;
    let obj: Record<string, unknown>;
    try {
      obj = JSON.parse(line) as Record<string, unknown>;
    } catch {
      continue;
    }
    if (obj.t === "issue" && obj.issue && typeof obj.issue === "object") {
      const o = obj.issue as Record<string, unknown>;
      const severity = String(o.severity ?? "medium").toLowerCase();
      issues.push({
        shotId: String(o.shotId ?? "").trim(),
        severity: (SEVERITIES.has(severity) ? severity : "medium") as AuditIssue["severity"],
        kind: String(o.kind ?? "其他").trim().slice(0, 12),
        quote: String(o.quote ?? "").trim().slice(0, 200),
        detail: String(o.detail ?? "").trim().slice(0, 300),
        suggestion: String(o.suggestion ?? "").trim().slice(0, 300),
      });
    } else if (obj.t === "summary" && obj.summary && typeof obj.summary === "object") {
      const s = obj.summary as Record<string, unknown>;
      reviewed = Number(s.reviewed) || reviewed;
      verdict = String(s.verdict ?? "").trim().slice(0, 60);
    }
  }

  // 只保留指向真实镜头的结论，避免模型编出不存在的镜号
  const ids = new Set(shots.map((s) => s.id));
  const kept = issues.filter((i) => ids.has(i.shotId));
  if (kept.length !== issues.length) {
    const dropped = issues.length - kept.length;
    verdict = `${verdict}（另有 ${dropped} 条结论指向了不存在的镜号，已丢弃）`.trim();
  }

  const order: Record<AuditIssue["severity"], number> = { high: 0, medium: 1, low: 2 };
  kept.sort((a, b) => order[a.severity] - order[b.severity]);

  return { issues: kept, reviewed, verdict };
}

/**
 * 把 SDK 的错误翻译成用户能看懂的话。
 * 分镜师不关心 429 和 529 的区别，但关心「是等一下还是去改配置」。
 */
export function describeError(err: unknown): string {
  if (err instanceof Anthropic.AuthenticationError) {
    return "API Key 无效或已过期，请检查 ANTHROPIC_API_KEY。";
  }
  if (err instanceof Anthropic.RateLimitError) {
    return "触发了限流，请等几十秒后重试。";
  }
  if (err instanceof Anthropic.BadRequestError) {
    const detail = err.message?.slice(0, 200) ?? "";
    return `请求被拒绝（可能是原文过长或模型名不可用）：${detail}`;
  }
  if (err instanceof Anthropic.APIConnectionError) {
    return "连不上模型服务，请检查网络或 ANTHROPIC_BASE_URL。";
  }
  if (err instanceof Anthropic.APIError) {
    return `模型服务返回错误 ${err.status ?? ""}：${err.message?.slice(0, 200) ?? ""}`;
  }
  if (err instanceof Error) return err.message;
  return "未知错误。";
}
