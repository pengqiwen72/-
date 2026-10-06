import { describeError, hasCredentials, refineLive } from "@/lib/llm";
import { MEDIA, type Medium, type RefineAction } from "@/lib/types";

export const runtime = "nodejs";
export const maxDuration = 60;

const ACTIONS: RefineAction[] = ["rewrite", "regenerate", "split", "merge"];

export async function POST(request: Request) {
  if (!hasCredentials()) {
    return Response.json(
      { error: "演示模式下不支持单镜精修，请配置 ANTHROPIC_API_KEY 后重启服务。" },
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
  const action = o.action as RefineAction;
  if (!ACTIONS.includes(action)) {
    return Response.json({ error: "不支持的操作。" }, { status: 400 });
  }
  if (!o.shot || typeof o.shot !== "object") {
    return Response.json({ error: "缺少镜头数据。" }, { status: 400 });
  }

  const medium: Medium = MEDIA.some((m) => m.id === o.medium)
    ? (o.medium as Medium)
    : "vertical";

  try {
    const shots = await refineLive({
      action,
      medium,
      shot: o.shot as never,
      next: (o.next ?? undefined) as never,
      beat: (o.beat ?? undefined) as never,
      sourceText: typeof o.sourceText === "string" ? o.sourceText : "",
    });
    return Response.json({ shots });
  } catch (err) {
    const message = describeError(err);
    const status = message.includes("演示模式") ? 503 : 502;
    return Response.json({ error: message }, { status });
  }
}
