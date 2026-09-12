import { MODEL, hasCredentials } from "@/lib/llm";

export const runtime = "nodejs";

/**
 * 让前端能如实告诉用户「现在是实时生成还是演示数据」。
 * 不做任何缓存——状态变了必须立刻反映到界面上。
 */
export async function GET() {
  return Response.json(
    { live: hasCredentials(), model: hasCredentials() ? MODEL : null },
    { headers: { "Cache-Control": "no-store" } },
  );
}
