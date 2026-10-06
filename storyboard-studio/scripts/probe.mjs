/**
 * 网关探针：搞清楚这个端点到底往外吐什么事件、模型把预算花在哪。
 * 换网关或换模型时先跑这个，比在应用里猜快得多。
 *
 *   node scripts/probe.mjs
 */
import Anthropic from "@anthropic-ai/sdk";

const MODEL = process.env.ANTHROPIC_MODEL?.trim() || "claude-opus-5";
const BASE = process.env.ANTHROPIC_BASE_URL?.trim();
const client = new Anthropic(BASE ? { baseURL: BASE } : {});

const stream = client.messages.stream({
  model: MODEL,
  max_tokens: 4000,
  system: '严格输出 NDJSON。第一行 {"t":"meta","title":"标题"}，然后每行一个 {"t":"shot","shot":{...}}。不要代码块，不要解释。',
  messages: [{ role: "user", content: "把这句话拆成 3 个镜头：雨从傍晚就没停过，林晚站在老宅门口。" }],
});

const eventTypes = new Map();
const deltaTypes = new Map();
let textChars = 0;
let head = "";

for await (const event of stream) {
  eventTypes.set(event.type, (eventTypes.get(event.type) ?? 0) + 1);
  if (event.type === "content_block_delta") {
    const d = event.delta.type;
    deltaTypes.set(d, (deltaTypes.get(d) ?? 0) + 1);
    if (d === "text_delta") {
      textChars += event.delta.text.length;
      if (head.length < 400) head += event.delta.text;
    }
  }
  if (event.type === "content_block_start") {
    const t = event.content_block.type;
    eventTypes.set(`block:${t}`, (eventTypes.get(`block:${t}`) ?? 0) + 1);
  }
}

const final = await stream.finalMessage();

console.log("事件类型:", Object.fromEntries(eventTypes));
console.log("delta 类型:", Object.fromEntries(deltaTypes));
console.log("文本字符数:", textChars);
console.log("stop_reason:", final.stop_reason);
console.log("usage:", JSON.stringify(final.usage));
console.log("\n--- 文本开头 ---\n" + (head || "(没有文本内容)"));
