import { describeError, hasCredentials, regenerateBeatLive } from "@/lib/llm";
import { MEDIA, type Medium } from "@/lib/types";

export const runtime = "nodejs";
export const maxDuration = 120;

/**
 * 按节拍重跑镜头。
 *
 * 与 /api/refine 分开是有意的：那边是镜头级动作（拆分/合并/改写/重生成），
 * 请求体围绕「一个镜头 + 可选的下一个镜头」；这里是节拍级动作，请求体是
 * 「一个节拍 + 它下面的全部镜头」，返回的镜头数也不固定。共用一个端点只会
 * 让两边的校验互相迁就。
 */
export async function POST(request: Request) {
  if (!hasCredentials()) {
    return Response.json(
      { error: "演示模式下不支持重跑节拍，请配置 ANTHROPIC_API_KEY 后重启服务。" },
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
  if (!o.beat || typeof o.beat !== "object") {
    return Response.json({ error: "缺少节拍数据。" }, { status: 400 });
  }
  if (!Array.isArray(o.shots)) {
    return Response.json({ error: "缺少镜头数据。" }, { status: 400 });
  }

  const medium: Medium = MEDIA.some((m) => m.id === o.medium)
    ? (o.medium as Medium)
    : "vertical";

  // 空节拍（下面一个镜头都没有）是合法输入——那正是最需要生成镜头的情况，
  // 此时没有参照时长，交给提示词按信息量自行分配。
  const raw = Number(o.targetDurationSec);
  const targetDurationSec = Number.isFinite(raw) && raw > 0 ? raw : 0;

  try {
    const shots = await regenerateBeatLive({
      medium,
      beat: o.beat as never,
      shots: o.shots as never,
      targetDurationSec,
      sourceText: typeof o.sourceText === "string" ? o.sourceText : "",
    });
    return Response.json({ shots });
  } catch (err) {
    return Response.json({ error: describeError(err) }, { status: 502 });
  }
}
