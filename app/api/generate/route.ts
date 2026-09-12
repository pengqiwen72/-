import { buildDemoStoryboard, matchedSampleTitle } from "@/lib/demo-cache";
import { clampSource, describeError, generateLive, hasCredentials } from "@/lib/llm";
import { MEDIA, type Medium, type StreamEvent } from "@/lib/types";

export const runtime = "nodejs";
export const maxDuration = 300;

const MIN_CHARS = 20;
const MAX_CHARS = 20000;

interface Payload {
  text: string;
  medium: Medium;
  targetDurationSec: number;
}

function validate(body: unknown): { ok: true; value: Payload } | { ok: false; message: string } {
  if (!body || typeof body !== "object") return { ok: false, message: "请求体格式不正确。" };
  const o = body as Record<string, unknown>;

  const text = typeof o.text === "string" ? o.text.trim() : "";
  if (text.length < MIN_CHARS) {
    return { ok: false, message: `原文太短，至少需要 ${MIN_CHARS} 个字才能拆出节拍。` };
  }
  const { text: clamped, truncated } = clampSource(text);
  if (truncated) {
    // 不静默截断：超长文本会走下面的 notice 事件告知用户
  }

  const medium = MEDIA.some((m) => m.id === o.medium) ? (o.medium as Medium) : "vertical";

  const raw = Number(o.targetDurationSec);
  const targetDurationSec = Number.isFinite(raw)
    ? Math.round(Math.min(600, Math.max(10, raw)))
    : 60;

  return { ok: true, value: { text: clamped, medium, targetDurationSec } };
}

function line(event: StreamEvent): string {
  return `${JSON.stringify(event)}\n`;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** 把 AsyncGenerator<StreamEvent> 变成 NDJSON 响应体。 */
function ndjsonResponse(run: (emit: (e: StreamEvent) => void) => Promise<void>): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const emit = (e: StreamEvent) => controller.enqueue(encoder.encode(line(e)));
      try {
        await run(emit);
      } catch (err) {
        emit({ t: "error", message: describeError(err) });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "请求体不是合法 JSON。" }, { status: 400 });
  }

  const parsed = validate(body);
  if (!parsed.ok) {
    return Response.json({ error: parsed.message }, { status: 400 });
  }
  const { text, medium, targetDurationSec } = parsed.value;

  // ---- 演示模式：没有凭据时用内置样例跑通同一条用户路径 --------------------
  if (!hasCredentials()) {
    return ndjsonResponse(async (emit) => {
      const title = matchedSampleTitle(text);
      if (!title) {
        emit({
          t: "error",
          message:
            "当前是演示模式（未配置 ANTHROPIC_API_KEY），只能生成内置样例。请点击「载入样例」，或配置 API Key 后重启服务。",
        });
        return;
      }

      const board = buildDemoStoryboard();
      emit({ t: "notice", message: board.meta.notice ?? "" });
      emit({ t: "meta", title: board.title, source: "demo" });

      // 用真实的节拍回放节奏，让离线体验和在线体验一致
      for (const beat of board.beats) {
        await sleep(140);
        emit({ t: "beat", beat });
      }
      for (const shot of board.shots) {
        await sleep(90);
        emit({ t: "shot", shot });
      }
      emit({ t: "done" });
    });
  }

  // ---- 实时生成 -----------------------------------------------------------
  return ndjsonResponse(async (emit) => {
    // 模型开始出字前有一段思考时间，先给个明确的状态，不要让界面干等
    emit({ t: "notice", message: "正在拆解戏剧节拍…" });
    for await (const event of generateLive({ text, medium, targetDurationSec })) {
      emit(event);
    }
  });
}
