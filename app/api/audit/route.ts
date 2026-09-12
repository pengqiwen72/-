import { auditLive, describeError, hasCredentials } from "@/lib/llm";

export const runtime = "nodejs";
export const maxDuration = 120;

/**
 * 忠实度自查。刻意做成用户手动触发的一次独立调用：
 * 一是它要多花一次请求的钱和十几秒，不该拖慢主路径；
 * 二是让同一段上下文给自己打分，几乎必然得到「没问题」。
 */
export async function POST(request: Request) {
  if (!hasCredentials()) {
    return Response.json(
      { error: "演示模式下不支持自查，请配置 ANTHROPIC_API_KEY 后重启服务。" },
      { status: 503 },
    );
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "请求体不是合法 JSON。" }, { status: 400 });
  }

  const o = (body ?? {}) as Record<string, unknown>;
  const shots = Array.isArray(o.shots) ? o.shots : [];
  const beats = Array.isArray(o.beats) ? o.beats : [];
  const sourceText = typeof o.sourceText === "string" ? o.sourceText : "";

  if (shots.length === 0) {
    return Response.json({ error: "没有可自查的镜头。" }, { status: 400 });
  }
  if (sourceText.trim().length === 0) {
    return Response.json({ error: "缺少原文，无法核对。" }, { status: 400 });
  }

  try {
    const result = await auditLive(sourceText, shots as never, beats as never);
    return Response.json(result);
  } catch (err) {
    return Response.json({ error: describeError(err) }, { status: 502 });
  }
}
