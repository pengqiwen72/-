import { SHOT_SIZES, type Beat, type Shot, type ShotSize, type Storyboard } from "./types";

/** 分镜表的统计、整理与导出。这些是分镜师每天要交出去的东西。 */

export function totalDuration(shots: Shot[]): number {
  return Math.round(shots.reduce((sum, s) => sum + s.durationSec, 0) * 10) / 10;
}

export function formatDuration(sec: number): string {
  if (sec < 60) return `${sec.toFixed(1)} 秒`;
  const m = Math.floor(sec / 60);
  const s = Math.round((sec - m * 60) * 10) / 10;
  return `${m} 分 ${s.toFixed(s % 1 === 0 ? 0 : 1)} 秒`;
}

export interface SizeBucket {
  size: ShotSize;
  count: number;
  seconds: number;
}

/** 按由远到近的顺序统计景别，零值不出现——分布本身就是一张视觉单调性体检表。 */
export function sizeDistribution(shots: Shot[]): SizeBucket[] {
  const map = new Map<ShotSize, SizeBucket>();
  for (const shot of shots) {
    const bucket = map.get(shot.shotSize) ?? { size: shot.shotSize, count: 0, seconds: 0 };
    bucket.count += 1;
    bucket.seconds = Math.round((bucket.seconds + shot.durationSec) * 10) / 10;
    map.set(shot.shotSize, bucket);
  }
  return SHOT_SIZES.filter((s) => map.has(s)).map((s) => map.get(s)!);
}

export interface Diagnostic {
  level: "warn" | "info";
  message: string;
}

/**
 * 分镜体检。这里刻意只报「可被证伪」的问题：
 * 连续的相同景别、时长严重偏离目标、模型改写过台词、节拍下没有镜头。
 */
export function diagnose(board: Storyboard): Diagnostic[] {
  const out: Diagnostic[] = [];
  const { shots, beats, meta } = board;

  if (shots.length === 0) return out;

  // 1. 景别连续重复
  let run = 1;
  for (let i = 1; i < shots.length; i++) {
    if (shots[i].shotSize === shots[i - 1].shotSize) {
      run += 1;
      if (run === 3) {
        out.push({
          level: "warn",
          message: `第 ${i - 1}-${i + 1} 镜连续三个「${shots[i].shotSize}」，视觉偏平，考虑换景别或改运镜。`,
        });
      }
    } else {
      run = 1;
    }
  }

  // 2. 时长偏差
  const total = totalDuration(shots);
  if (meta.targetDurationSec > 0) {
    const drift = (total - meta.targetDurationSec) / meta.targetDurationSec;
    if (Math.abs(drift) > 0.15) {
      const pct = Math.round(Math.abs(drift) * 100);
      out.push({
        level: "warn",
        message: `总时长 ${total.toFixed(1)}s，比目标 ${meta.targetDurationSec}s ${drift > 0 ? "多" : "少"}了 ${pct}%，超出 ±15% 的容差。`,
      });
    }
  }

  // 3. 台词被改写（最危险的一类错误，因为读起来很顺）
  const rewritten = shots.filter((s) => s.flags?.some((f) => f.includes("台词")));
  if (rewritten.length > 0) {
    out.push({
      level: "warn",
      message: `第 ${rewritten.map((s) => s.id.replace("S", "")).join("、")} 镜的台词与原文不一致，已在卡片上标出。`,
    });
  }

  // 4. 景别被归一化过（模型没按枚举输出）
  const normalized = shots.filter((s) => s.flags?.some((f) => f.includes("景别")));
  if (normalized.length > 0) {
    out.push({
      level: "info",
      message: `有 ${normalized.length} 个镜头的景别不在标准表内，已自动归并，请人工确认。`,
    });
  }

  // 5. 空节拍
  const used = new Set(shots.map((s) => s.beatId));
  const empty = beats.filter((b) => !used.has(b.id));
  if (empty.length > 0) {
    out.push({
      level: "warn",
      message: `节拍 ${empty.map((b) => b.id).join("、")} 下没有任何镜头，这段戏会被漏掉。`,
    });
  }

  return out;
}

export function beatsWithShots(beats: Beat[], shots: Shot[]): Array<{ beat: Beat; shots: Shot[] }> {
  return beats.map((beat) => ({
    beat,
    shots: shots.filter((s) => s.beatId === beat.id),
  }));
}

// --- 整理 -------------------------------------------------------------------

/** 镜号连续。删改 / 移动之后必须重排，否则界面和导出都会露出跳号。 */
export function renumber(shots: Shot[]): Shot[] {
  return shots.map((s, i) => ({ ...s, id: `S${i + 1}` }));
}

/**
 * 节拍改动之后的整理：重编节拍号、按新节拍顺序重排镜头、改写镜头的 beatId。
 *
 * 这三件事必须一起做，因为 `Beat.id` 是 `Shot.beatId` 的外键。最容易漏的是
 * 第三条——只重建节拍编号而不动镜头，镜头就会挂到语义错误的节拍下面，
 * 界面出现假的「空节拍」警告（diagnose 靠 beatId 分组），而真正的镜头跑了。
 *
 * 同理，`shots` 是独立的有序数组，而界面按节拍分组渲染、CSV 按数组顺序输出
 * （见 toCSV）。节拍换了顺序而镜头数组不动，导出的镜号顺序就和读者看到的
 * 顺序对不上。所以这里把顺序也一并归一：同一节拍内的镜头保持原有先后。
 */
export function normalizeBeats(
  beats: Beat[],
  shots: Shot[],
): { beats: Beat[]; shots: Shot[] } {
  const remap = new Map<string, string>();
  const nextBeats = beats.map((beat, i) => {
    const id = `B${i + 1}`;
    remap.set(beat.id, id);
    return { ...beat, id };
  });

  const rank = new Map(nextBeats.map((beat, i) => [beat.id, i]));
  const reordered = shots
    .map((shot, i) => ({ shot, i }))
    .sort((a, b) => {
      // 认不出的 beatId 排到最后，且不掩盖问题——diagnose 会把它报出来
      const ra = rank.get(remap.get(a.shot.beatId) ?? "") ?? Number.MAX_SAFE_INTEGER;
      const rb = rank.get(remap.get(b.shot.beatId) ?? "") ?? Number.MAX_SAFE_INTEGER;
      return ra - rb || a.i - b.i;
    })
    .map(({ shot }) => ({ ...shot, beatId: remap.get(shot.beatId) ?? shot.beatId }));

  return { beats: nextBeats, shots: renumber(reordered) };
}

/**
 * 自查结论的指纹。
 *
 * 用的是输入给 /api/audit 的**全部字段**：节拍表（buildAuditPrompt 会把
 * summary / emotion / intensity 全发过去）和镜头表（含 rationale）。改了
 * 其中任何一个，之前那份结论就不再是对着这份分镜做的，界面必须提示过期。
 */
export function auditSignature(beats: Beat[], shots: Shot[]): string {
  return [
    ...beats.map((b) => `B|${b.id}|${b.summary}|${b.emotion}|${b.intensity}`),
    ...shots.map(
      (s) =>
        `S|${s.id}|${s.beatId}|${s.shotSize}|${s.description}|${s.dialogue}|${s.rationale}`,
    ),
  ].join("\n");
}

// --- 导出 -------------------------------------------------------------------

function csvCell(value: string | number): string {
  const s = String(value ?? "");
  return `"${s.replace(/"/g, '""')}"`;
}

export function toCSV(board: Storyboard): string {
  const beatsById = new Map(board.beats.map((b) => [b.id, b]));
  const header = [
    "镜号",
    "节拍",
    "节拍内容",
    "情绪",
    "强度",
    "景别",
    "运镜",
    "画面描述",
    "台词",
    "音效",
    "时长(秒)",
    "设计依据",
  ];
  const rows = board.shots.map((s) => {
    const beat = beatsById.get(s.beatId);
    return [
      s.id,
      s.beatId,
      beat?.summary ?? "",
      beat?.emotion ?? "",
      beat?.intensity ?? "",
      s.shotSize,
      s.camera,
      s.description,
      s.dialogue,
      s.sfx,
      s.durationSec.toFixed(1),
      s.rationale,
    ].map(csvCell);
  });
  const total = totalDuration(board.shots);
  const footer = [["", "", "", "", "", "", "", "", "", "合计", total.toFixed(1), ""].map(csvCell)];

  // BOM 让 Excel 正确识别 UTF-8 中文
  return "﻿" + [header, ...rows, ...footer].map((r) => r.join(",")).join("\r\n");
}

export function toMarkdown(board: Storyboard): string {
  const total = totalDuration(board.shots);
  const lines: string[] = [];

  lines.push(`# ${board.title} · 分镜表`);
  lines.push("");
  lines.push(
    `共 ${board.shots.length} 个镜头 / ${board.beats.length} 个节拍 · 总时长 ${formatDuration(total)}（目标 ${board.meta.targetDurationSec} 秒）`,
  );
  lines.push("");

  for (const { beat, shots } of beatsWithShots(board.beats, board.shots)) {
    lines.push(`## ${beat.id} · ${beat.summary}`);
    lines.push(`情绪：${beat.emotion} · 强度 ${beat.intensity}/5 · ${shots.length} 个镜头`);
    lines.push("");
    lines.push("| 镜号 | 景别 | 运镜 | 时长 | 画面 | 台词 | 音效 |");
    lines.push("| --- | --- | --- | --- | --- | --- | --- |");
    for (const s of shots) {
      const cell = (v: string) => v.replace(/\|/g, "\\|").replace(/\n/g, "<br>") || "—";
      lines.push(
        `| ${s.id} | ${s.shotSize} | ${cell(s.camera)} | ${s.durationSec.toFixed(1)}s | ${cell(s.description)} | ${cell(s.dialogue)} | ${cell(s.sfx)} |`,
      );
    }
    lines.push("");
    lines.push("**设计依据**");
    lines.push("");
    for (const s of shots) {
      lines.push(`- **${s.id}**（${s.shotSize}）：${s.rationale || "—"}`);
    }
    lines.push("");
  }

  lines.push("---");
  lines.push("");
  lines.push("### 景别分布");
  lines.push("");
  const dist = sizeDistribution(board.shots);
  for (const d of dist) {
    lines.push(`- ${d.size}：${"█".repeat(d.count)} ${d.count} 镜 / ${d.seconds.toFixed(1)}s`);
  }
  lines.push("");

  return lines.join("\n");
}

export function download(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
