/**
 * 冒烟测试：不经过 UI，直接验证「模型 → NDJSON → 解析」这条链路。
 *
 * 用法：
 *   node scripts/smoke.mjs                # 用环境变量里的配置
 *   ANTHROPIC_MODEL=claude-opus-5 node scripts/smoke.mjs
 *
 * 它回答三个问题：凭据是否可用、这个网关是否接受请求参数、
 * 模型是否真的按 NDJSON 逐行输出（而不是包成一个大 JSON 或加代码块）。
 */
import Anthropic from "@anthropic-ai/sdk";

const MODEL = process.env.ANTHROPIC_MODEL?.trim() || "claude-opus-5";
const BASE = process.env.ANTHROPIC_BASE_URL?.trim();
const isClaude = MODEL.startsWith("claude-");

const client = new Anthropic(BASE ? { baseURL: BASE } : {});

const SOURCE = `雨从傍晚就没停过。林晚站在老宅门口，钥匙在锁孔里转了三圈，门纹丝不动。
她退后半步，抬头。二楼那扇窗亮着灯。
「谁？」她开口，声音比自己预想的要稳。`;

const system = `你是资深分镜师。把小说翻译成镜头语言。
先做节拍分解，再做镜头设计。输出严格 NDJSON，每行一个 JSON 对象，不要代码块、不要解释：
第一行 {"t":"meta","title":"标题"}
节拍行 {"t":"beat","beat":{"id":"B1","summary":"...","emotion":"...","intensity":4}}
镜头行 {"t":"shot","shot":{"id":"S1","beatId":"B1","shotSize":"特写","camera":"固定","description":"...","dialogue":"","sfx":"...","durationSec":2.5,"rationale":"..."}}
shotSize 只能取：大远景/远景/全景/中景/中近景/近景/特写/大特写
台词必须一字不改来自原文，没有就填空字符串。
最后一行 {"t":"done"}`;

const params = {
  model: MODEL,
  max_tokens: 8000,
  system,
  messages: [
    { role: "user", content: `【原文】\n${SOURCE}\n【目标时长】约 40 秒` },
  ],
};

// 这些是 Anthropic 专有参数。非 Anthropic 的兼容网关通常不认，会直接 400。
if (isClaude) {
  params.thinking = { type: "adaptive" };
  params.output_config = { effort: "medium" };
}

console.log(`model=${MODEL}  base=${BASE ?? "(default)"}  专有参数=${isClaude ? "开" : "关"}`);

const started = Date.now();
const stream = client.messages.stream(params);

let buffer = "";
const lines = [];
let firstLineAt = null;

for await (const event of stream) {
  if (event.type === "content_block_delta" && event.delta.type === "text_delta") {
    buffer += event.delta.text;
    let idx;
    while ((idx = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (line) {
        firstLineAt ??= Date.now() - started;
        lines.push(line);
      }
    }
  }
}

const final = await stream.finalMessage();
const elapsed = Date.now() - started;

let beats = 0;
let shots = 0;
const bad = [];
for (const line of lines) {
  try {
    const o = JSON.parse(line);
    if (o.t === "beat") beats++;
    else if (o.t === "shot") shots++;
    else if (o.t !== "meta" && o.t !== "done") bad.push(line.slice(0, 60));
  } catch {
    bad.push(line.slice(0, 60));
  }
}

console.log(`\nstop_reason=${final.stop_reason}`);
console.log(`首个 NDJSON 行到达：${firstLineAt ?? "—"}ms   全部完成：${elapsed}ms`);
console.log(`usage: in=${final.usage.input_tokens} out=${final.usage.output_tokens}`);
console.log(`解析结果：${beats} 个节拍 / ${shots} 个镜头 / ${bad.length} 行非 NDJSON`);
if (bad.length) {
  console.log("无法解析的行：");
  for (const b of bad.slice(0, 5)) console.log("  " + b);
}
console.log(shots > 0 && bad.length === 0 ? "\n✅ NDJSON 链路可用" : "\n⚠️ 需要调整提示词或参数");
