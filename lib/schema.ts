import {
  SHOT_SIZES,
  type Beat,
  type Shot,
  type ShotSize,
  type StreamEvent,
} from "./types";

/**
 * 模型输出的清洗层。
 *
 * 提示词能约束大部分情况，但不能指望它 100% 生效：实测中模型仍会
 * 偶尔吐出「全景镜头」「close-up」这类不在枚举里的景别、把时长写成
 * 字符串、把台词改写得「更通顺」、或者引用一个不存在的节拍 id。
 *
 * 这里的原则是：**能修的修，不能修的标出来给用户看，绝不静默放行。**
 * 尤其是台词——被模型润色过的台词是改编里最难发现的错误，因为读起来
 * 很顺，只有对照原文才知道变了。所以每句台词都回原文做核验。
 */

// --- 字段名归一化 -----------------------------------------------------------

/**
 * 实测发现：换一个模型（尤其是中文能力强的国产模型）时，它会自作主张
 * 把字段名写成中文——「景别」「运镜」「画面」「时长」，而不是提示词里
 * 要求的 shotSize / camera / description / durationSec。
 * 输出本身完全正确，只是键名不同。这种差异不该让整次生成失败，
 * 所以在清洗层做一层别名映射，而不是去和模型的服从性较劲。
 */
const KEY_ALIASES: Record<string, string> = {
  // shot
  画面: "description",
  画面描述: "description",
  描述: "description",
  镜头描述: "description",
  内容: "description",
  景别: "shotSize",
  镜头景别: "shotSize",
  镜头: "shotSize",
  运镜: "camera",
  机位: "camera",
  摄法: "camera",
  镜头运动: "camera",
  运动: "camera",
  台词: "dialogue",
  对白: "dialogue",
  字幕: "dialogue",
  声音: "sfx",
  音效: "sfx",
  音响: "sfx",
  时长: "durationSec",
  时长秒: "durationSec",
  秒数: "durationSec",
  长度: "durationSec",
  依据: "rationale",
  理由: "rationale",
  设计依据: "rationale",
  设计理由: "rationale",
  目的: "rationale",
  节拍: "beatId",
  节拍id: "beatId",
  所属节拍: "beatId",
  // beat
  摘要: "summary",
  节拍内容: "summary",
  概括: "summary",
  情绪: "emotion",
  情绪基调: "emotion",
  情感: "emotion",
  强度: "intensity",
  情感强度: "intensity",
  张力: "intensity",
  // meta
  标题: "title",
};

function normalizeKeys(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    // shotSize / shot_size / SHOTSIZE 都收敛到同一个规范名
    const snake = key.replace(/[_\s-]/g, "").toLowerCase();
    const canonical =
      KEY_ALIASES[key] ??
      KEY_ALIASES[snake] ??
      SNAKE_TO_CAMEL[snake] ??
      key;
    if (!(canonical in out)) out[canonical] = value;
  }
  return out;
}

const SNAKE_TO_CAMEL: Record<string, string> = {
  shotsize: "shotSize",
  camera: "camera",
  cameramove: "camera",
  description: "description",
  desc: "description",
  dialogue: "dialogue",
  lines: "dialogue",
  sfx: "sfx",
  sound: "sfx",
  audio: "sfx",
  durationsec: "durationSec",
  duration: "durationSec",
  durationseconds: "durationSec",
  rationale: "rationale",
  reason: "rationale",
  beatid: "beatId",
  beat: "beatId",
  summary: "summary",
  emotion: "emotion",
  intensity: "intensity",
  title: "title",
};

// --- 景别归一化 -------------------------------------------------------------

const SHOT_SIZE_ALIASES: Record<string, ShotSize> = {
  // 别名
  全境: "全景",
  全景镜头: "全景",
  大全景: "全景",
  全身: "全景",
  全身景: "全景",
  中近: "中近景",
  半身: "中近景",
  半身景: "中近景",
  近景镜头: "近景",
  特写镜头: "特写",
  极特写: "大特写",
  超特写: "大特写",
  大特写镜头: "大特写",
  远景镜头: "远景",
  大远景镜头: "大远景",
  中景镜头: "中景",
  // 英文（模型偶尔会串语言）
  "extreme long shot": "大远景",
  els: "大远景",
  "long shot": "远景",
  ls: "远景",
  "wide shot": "全景",
  ws: "全景",
  "full shot": "全景",
  "medium shot": "中景",
  ms: "中景",
  "medium close-up": "中近景",
  mcu: "中近景",
  "medium closeup": "中近景",
  "close-up": "近景",
  closeup: "近景",
  "close up": "近景",
  cu: "近景",
  "extreme close-up": "大特写",
  "extreme closeup": "大特写",
  ecu: "大特写",
};

/** 把任意景别写法收敛到八个合法值之一；实在认不出就落到中景并标记。 */
export function normalizeShotSize(raw: unknown): { value: ShotSize; degraded: boolean } {
  if (typeof raw !== "string") return { value: "中景", degraded: true };
  const trimmed = raw.trim();
  if ((SHOT_SIZES as readonly string[]).includes(trimmed)) {
    return { value: trimmed as ShotSize, degraded: false };
  }
  const alias = SHOT_SIZE_ALIASES[trimmed.toLowerCase()] ?? SHOT_SIZE_ALIASES[trimmed];
  if (alias) return { value: alias, degraded: true };
  // 兜底：包含关系，如「大特写（眼部）」
  for (const size of [...SHOT_SIZES].sort((a, b) => b.length - a.length)) {
    if (trimmed.includes(size)) return { value: size, degraded: true };
  }
  return { value: "中景", degraded: true };
}

// --- 时长 -------------------------------------------------------------------

export function normalizeDuration(raw: unknown): number {
  let n = typeof raw === "number" ? raw : Number.parseFloat(String(raw ?? ""));
  if (!Number.isFinite(n) || n <= 0) n = 2.5;
  // 上限 15 秒：一个镜头超过这个长度，在这个产品里几乎肯定是模型失控
  n = Math.min(15, Math.max(0.5, n));
  return Math.round(n * 10) / 10;
}

// --- 文本 -------------------------------------------------------------------

export function cleanText(raw: unknown, maxLen = 400): string {
  if (typeof raw !== "string") return "";
  return raw.replace(/\s+/g, " ").trim().slice(0, maxLen);
}

/** 去掉所有标点与空白，用于「这句台词是否真的出自原文」的核验。 */
function stripPunctuation(s: string): string {
  return s.replace(/[\s，。！？；：、""''「」『』（）()《》〈〉…—·,.!?;:"'`\-]/g, "");
}

/**
 * 回原文核验台词。
 * 这是防幻觉的关键一步——模型经常把原文台词「润色」得更通顺，
 * 读起来毫无破绽，只有和原文逐字比对才会暴露。
 */
export function verifyDialogue(dialogue: string, sourceText: string): boolean {
  const d = stripPunctuation(dialogue);
  if (!d) return true;
  return stripPunctuation(sourceText).includes(d);
}

// --- 节拍与镜头的构造 -------------------------------------------------------

export function makeBeat(raw: unknown, index: number): Beat | null {
  const o = normalizeKeys(raw);
  const summary = cleanText(o.summary, 80);
  if (!summary) return null;
  const intensity = Math.round(
    Math.min(5, Math.max(1, Number(o.intensity) || 3)),
  );
  return {
    id: `B${index}`,
    summary,
    emotion: cleanText(o.emotion, 20) || "未标注",
    intensity,
  };
}

export interface ShotBuildResult {
  shot: Shot | null;
  flags: string[];
}

/** 由模型输出构造一个合法 Shot，收集所有被修正过的问题。 */
export function makeShot(
  raw: unknown,
  index: number,
  knownBeatIds: string[],
  sourceText: string,
): ShotBuildResult {
  const o = normalizeKeys(raw);
  if (Object.keys(o).length === 0) return { shot: null, flags: ["镜头数据不是对象"] };
  const flags: string[] = [];

  const description = cleanText(o.description, 400);
  if (!description) return { shot: null, flags: ["镜头缺少画面描述"] };

  const { value: shotSize, degraded } = normalizeShotSize(o.shotSize);
  // 归一化的目标要报实际落到的那个值：别名「全境」归到的是「全景」，
  // 写死「已归为中景」会让分镜师按错误的景别去核对
  if (degraded) {
    flags.push(`景别「${cleanText(o.shotSize, 20)}」不在标准表内，已归为${shotSize}`);
  }

  // 引用了不存在的节拍 → 挂到最后一个已知节拍上，并标记
  let beatId = cleanText(o.beatId, 10);
  if (!knownBeatIds.includes(beatId)) {
    if (knownBeatIds.length > 0) {
      flags.push(`引用了不存在的节拍 ${beatId || "(空)"}，已归到 ${knownBeatIds.at(-1)}`);
      beatId = knownBeatIds[knownBeatIds.length - 1];
    } else {
      beatId = "";
    }
  }

  const dialogue = cleanText(o.dialogue, 200);
  if (dialogue && !verifyDialogue(dialogue, sourceText)) {
    flags.push("台词与原文不一致（可能被改写）");
  }

  return {
    shot: {
      id: `S${index}`,
      beatId,
      shotSize,
      camera: cleanText(o.camera, 20) || "固定",
      description,
      dialogue,
      sfx: cleanText(o.sfx, 100),
      durationSec: normalizeDuration(o.durationSec),
      rationale: cleanText(o.rationale, 200),
      ...(flags.length > 0 ? { flags } : {}),
    },
    flags,
  };
}

// --- 流式行解析 -------------------------------------------------------------

/**
 * 有状态的 NDJSON 行解析器。
 * 逐行喂进来，合法事件吐出去，坏行直接丢掉——单个镜头解析失败
 * 不应该让整次生成白费。
 */
export class StreamSanitizer {
  private beats: string[] = [];
  private shotCount = 0;

  constructor(private readonly sourceText: string) {}

  get beatIds(): string[] {
    return this.beats;
  }

  parseLine(line: string): StreamEvent | null {
    const trimmed = line.trim();
    if (!trimmed || trimmed === "```" || trimmed.startsWith("```")) return null;

    let obj: Record<string, unknown>;
    try {
      obj = JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      return null; // 半行 / 说明文字，跳过
    }

    switch (obj.t) {
      case "meta": {
        return { t: "meta", title: cleanText(obj.title, 30) || "未命名", source: "live" };
      }
      case "beat": {
        const beat = makeBeat(obj.beat, this.beats.length + 1);
        if (!beat) return null;
        this.beats.push(beat.id);
        return { t: "beat", beat };
      }
      case "shot": {
        const { shot, flags } = makeShot(
          obj.shot,
          this.shotCount + 1,
          this.beats,
          this.sourceText,
        );
        if (!shot) return null;
        this.shotCount += 1;
        if (flags.length > 0) {
          return { t: "shot", shot };
        }
        return { t: "shot", shot };
      }
      case "done":
        return { t: "done" };
      default:
        return null;
    }
  }
}

/** 精修接口返回的单行 JSON 解析。 */
export function parseRefineShots(
  text: string,
  sourceText: string,
): { shots: Array<Omit<Shot, "id" | "beatId">> } {
  const cleaned = text.replace(/```(?:json)?/g, "").trim();
  const start = cleaned.indexOf("{");
  const end = cleaned.lastIndexOf("}");
  if (start < 0 || end <= start) throw new Error("模型没有返回 JSON");
  const obj = JSON.parse(cleaned.slice(start, end + 1)) as Record<string, unknown>;
  const list = Array.isArray(obj.shots) ? obj.shots : [];
  const shots = list.flatMap((raw) => {
    // 精修 / 节拍重跑的返回里本来就不含 beatId——归属由调用方按被改的那个
    // 节拍决定。所以这里传空的已知节拍表，让 makeShot 走「没有节拍可挂」的
    // 分支（静默留空），而不是拿一个哨兵值去比，那会给每个镜头都扣上一条
    // 「引用了不存在的节拍」的假警告，还把内部哨兵漏给用户看。
    const built = makeShot(raw, 0, [], sourceText);
    if (!built.shot) return [];
    // 精修不改变 id / beatId，由调用方保留
    const { id: _id, beatId: _beatId, ...rest } = built.shot;
    return [rest];
  });
  if (shots.length === 0) throw new Error("模型返回的镜头为空");
  return { shots };
}
