/**
 * 领域模型：把「小说文字」翻译成「镜头语言」这件事，落到两组对象上。
 *
 * Beat（节拍）是叙事层：一件事、一次转折、一个决定。
 * Shot（镜头）是执行层：导演和画师真正拿去干活的最小单位。
 *
 * 两者的关系（shot.beatId）是这个产品的核心——每个镜头都能回答
 * 「我为什么存在、我服务的是哪一段戏」。没有这层溯源，AI 生成的分镜
 * 就只是一堆无法验证的漂亮句子。
 */

/** 景别由远到近，顺序本身有意义：索引用来做「景别分布」与单调性检查。 */
export const SHOT_SIZES = [
  "大远景",
  "远景",
  "全景",
  "中景",
  "中近景",
  "近景",
  "特写",
  "大特写",
] as const;

export type ShotSize = (typeof SHOT_SIZES)[number];

/** 情感强度 → 色彩，界面上节拍条和镜头卡片共用这套色阶。 */
export const INTENSITY_COLORS = [
  "#3b82f6", // 1 平静
  "#14b8a6", // 2
  "#eab308", // 3
  "#f97316", // 4
  "#ef4444", // 5 爆发
] as const;

export interface Beat {
  id: string;
  /** 一句话说清这个节拍发生了什么 */
  summary: string;
  /** 情绪基调，如「压抑的试探」 */
  emotion: string;
  /** 1-5，驱动景别选择与色阶 */
  intensity: number;
}

export interface Shot {
  id: string;
  beatId: string;
  shotSize: ShotSize;
  /** 机位与运镜，如「固定」「手持跟拍」 */
  camera: string;
  /** 画面描述，必须「可拍」 */
  description: string;
  /** 台词。原文没有则为空字符串——不允许杜撰。 */
  dialogue: string;
  /** 音效 / 音乐提示 */
  sfx: string;
  durationSec: number;
  /** 为什么这样切，必须引用原文依据 */
  rationale: string;
  /** 用户手工改过 */
  edited?: boolean;
  /** 质检发现的问题，如台词不在原文中 */
  flags?: string[];
}

export interface Usage {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
}

/** 标明本次结果来自实时模型还是内置样例，界面上必须让用户看得见。 */
export type GenerationSource = "live" | "demo";

export interface GenerationMeta {
  source: GenerationSource;
  model?: string;
  medium: Medium;
  targetDurationSec: number;
  createdAt: string;
  usage?: Usage;
  /** 降级原因，例如实时调用失败后回退到样例 */
  notice?: string;
}

export const MEDIA = [
  { id: "vertical", label: "竖屏漫剧", hint: "9:16 竖屏，人物常在中近景以上，留白少、信息密度高" },
  { id: "horizontal", label: "横屏动画", hint: "16:9 横屏，可以承载更宽的空间关系与群像" },
  { id: "film", label: "电影长片", hint: "宽银幕，允许多层次景深与长镜头调度" },
] as const;

export type Medium = (typeof MEDIA)[number]["id"];

export interface Storyboard {
  title: string;
  beats: Beat[];
  shots: Shot[];
  meta: GenerationMeta;
}

/**
 * 服务端 → 客户端的流式协议。
 * 用 NDJSON 而不是「一整块 JSON」：镜头可以一边生成一边渲染，
 * 用户不用盯着转圈等到最后才看到结果，也让单行损坏时可被跳过而非全盘失败。
 */
export type StreamEvent =
  | { t: "meta"; title: string; source: GenerationSource; model?: string }
  | { t: "beat"; beat: Beat }
  | { t: "shot"; shot: Shot }
  /** 推理型模型在出字之前的思考增量。只用作进度提示，不参与结果。 */
  | { t: "thinking"; text: string }
  | { t: "notice"; message: string }
  | { t: "done"; usage?: Usage }
  | { t: "error"; message: string };

/** 忠实度自查的结论。severity 决定界面上是红还是黄。 */
export type AuditSeverity = "high" | "medium" | "low";

export interface AuditIssue {
  shotId: string;
  severity: AuditSeverity;
  kind: string;
  /** 原文里对应的一句；原文没有对应内容时为空 */
  quote: string;
  detail: string;
  suggestion: string;
}

export interface AuditResult {
  issues: AuditIssue[];
  reviewed: number;
  verdict: string;
}

export type RefineAction = "rewrite" | "regenerate" | "split" | "merge";

export const REFINE_LABELS: Record<RefineAction, string> = {
  rewrite: "改写描述",
  regenerate: "换个设计",
  split: "拆分",
  merge: "与下一镜合并",
};
