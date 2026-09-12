"use client";

import { useMemo } from "react";

import {
  diagnose,
  formatDuration,
  sizeDistribution,
  totalDuration,
} from "@/lib/storyboard";
import {
  INTENSITY_COLORS,
  type Beat,
  type RefineAction,
  type Shot,
  type Storyboard as Board,
} from "@/lib/types";
import { BeatCard } from "./BeatCard";
import { ShotCard } from "./ShotCard";
import { Chip } from "./ui";

export interface StoryboardProps {
  board: Board;
  live: boolean;
  busyShotId: string | null;
  busyBeatId: string | null;
  onEditShot: (id: string, patch: Partial<Shot>) => void;
  onAction: (id: string, action: RefineAction) => void;
  onDelete: (id: string) => void;
  onMove: (id: string, dir: -1 | 1) => void;
  onEditBeat: (id: string, patch: Partial<Beat>) => void;
  onMoveBeat: (id: string, dir: -1 | 1) => void;
  onDeleteBeat: (id: string) => void;
  onRegenerateBeat: (id: string) => void;
}

export function Storyboard({
  board,
  live,
  busyShotId,
  busyBeatId,
  onEditShot,
  onAction,
  onDelete,
  onMove,
  onEditBeat,
  onMoveBeat,
  onDeleteBeat,
  onRegenerateBeat,
}: StoryboardProps) {
  const { beats, shots } = board;
  const total = totalDuration(shots);
  // 节拍重跑和单镜精修都在打模型，任何一种在跑就不允许再发起另一种
  const anyBusy = busyShotId !== null || busyBeatId !== null;
  const diagnostics = useMemo(() => diagnose(board), [board]);
  const dist = useMemo(() => sizeDistribution(shots), [shots]);
  const beatsById = useMemo(() => new Map(beats.map((b) => [b.id, b])), [beats]);

  if (shots.length === 0 && beats.length === 0) return null;

  const target = board.meta.targetDurationSec;
  const driftPct = target > 0 ? Math.round(((total - target) / target) * 100) : 0;
  const withinTolerance = Math.abs(driftPct) <= 15;
  const asl = shots.length > 0 ? total / shots.length : 0;
  const maxCount = Math.max(...dist.map((d) => d.count), 1);

  return (
    <div className="space-y-4">
      {/* 节奏条：每一段宽度 = 镜头时长，颜色 = 所属节拍的情绪强度。
          这是全片节奏最直接的一张图——哪里拖、哪里赶，一眼能看出来。 */}
      <section>
        <SectionTitle
          left="节奏条"
          right={`${formatDuration(total)} / 目标 ${target}s`}
        />
        <div className="flex h-9 w-full overflow-hidden rounded border border-line">
          {shots.map((s) => {
            const beat = beatsById.get(s.beatId);
            const color = INTENSITY_COLORS[(beat?.intensity ?? 1) - 1];
            const pct = total > 0 ? (s.durationSec / total) * 100 : 0;
            return (
              <button
                key={s.id}
                type="button"
                title={`${s.id} · ${s.shotSize} · ${s.durationSec}s\n${s.description}`}
                onClick={() =>
                  document
                    .getElementById(`shot-${s.id}`)
                    ?.scrollIntoView({ behavior: "smooth", block: "center" })
                }
                style={{ width: `${pct}%`, background: color }}
                className="group relative h-full border-r border-ink/40 opacity-70 transition-opacity last:border-r-0 hover:opacity-100"
              >
                <span className="font-mono text-[9px] text-ink/70 group-hover:text-ink">
                  {pct > 4 ? s.id : ""}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* 节拍轴 */}
      {beats.length > 0 && (
        <section>
          <SectionTitle left="戏剧节拍" right={`${beats.length} 个`} />
          <ol className="flex gap-2 overflow-x-auto pb-1">
            {beats.map((beat, i) => (
              <BeatCard
                key={beat.id}
                beat={beat}
                index={i}
                total={beats.length}
                shotCount={shots.filter((s) => s.beatId === beat.id).length}
                busy={busyBeatId === beat.id}
                anyBusy={anyBusy}
                live={live}
                onEdit={(patch) => onEditBeat(beat.id, patch)}
                onMove={(dir) => onMoveBeat(beat.id, dir)}
                onDelete={() => onDeleteBeat(beat.id)}
                onRegenerate={() => onRegenerateBeat(beat.id)}
              />
            ))}
          </ol>
        </section>
      )}

      {/* 统计 */}
      <section className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat
          label="总时长"
          value={formatDuration(total)}
          hint={`目标 ${target}s，偏差 ${driftPct > 0 ? "+" : ""}${driftPct}%`}
          tone={withinTolerance ? "ok" : "warn"}
        />
        <Stat label="镜头数" value={`${shots.length}`} hint={`${beats.length} 个节拍`} />
        <Stat
          label="平均镜头长度"
          value={`${asl.toFixed(1)}s`}
          hint={asl < 1.5 ? "偏碎" : asl > 5 ? "偏慢" : "正常区间"}
        />
        <Stat
          label="景别跨度"
          value={`${dist.length} 种`}
          hint={dist.length <= 3 ? "偏单调" : "有层次"}
          tone={dist.length <= 3 ? "warn" : "ok"}
        />
      </section>

      {/* 景别分布 */}
      <section className="rounded border border-line bg-panel p-3">
        <SectionTitle left="景别分布" right="按由远到近排列" />
        <ul className="space-y-1">
          {dist.map((d) => (
            <li key={d.size} className="flex items-center gap-2 text-xs">
              <span className="w-14 shrink-0 text-muted">{d.size}</span>
              <span className="h-3 flex-1 overflow-hidden rounded-sm bg-panel3">
                <span
                  className="block h-full rounded-sm bg-accent/60"
                  style={{ width: `${(d.count / maxCount) * 100}%` }}
                />
              </span>
              <span className="w-24 shrink-0 text-right font-mono text-faint">
                {d.count} 镜 · {d.seconds.toFixed(1)}s
              </span>
            </li>
          ))}
        </ul>
      </section>

      {/* 体检 */}
      {diagnostics.length > 0 && (
        <section className="space-y-1.5 rounded border border-line bg-panel p-3">
          <SectionTitle left="分镜体检" right={`${diagnostics.length} 条`} />
          <ul className="space-y-1.5">
            {diagnostics.map((d, i) => (
              <li key={i} className="flex items-start gap-2 text-xs leading-relaxed">
                <span className={d.level === "warn" ? "text-danger" : "text-muted"}>
                  {d.level === "warn" ? "⚠" : "ⓘ"}
                </span>
                <span className={d.level === "warn" ? "text-fg" : "text-muted"}>
                  {d.message}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 镜头列表 */}
      <section>
        <SectionTitle
          left="镜头表"
          right={live ? "点击任意字段可直接修改" : "演示数据 · 编辑不受影响"}
        />
        <ul className="space-y-2">
          {shots.map((shot, i) => (
            <ShotCard
              key={shot.id}
              shot={shot}
              beat={beatsById.get(shot.beatId)}
              domId={`shot-${shot.id}`}
              index={i}
              total={shots.length}
              busy={busyShotId === shot.id}
              anyBusy={anyBusy}
              live={live}
              onEdit={(patch) => onEditShot(shot.id, patch)}
              onAction={(action) => onAction(shot.id, action)}
              onDelete={() => onDelete(shot.id)}
              onMove={(dir) => onMove(shot.id, dir)}
            />
          ))}
        </ul>
      </section>
    </div>
  );
}

function SectionTitle({ left, right }: { left: string; right?: string }) {
  return (
    <div className="mb-2 flex items-baseline justify-between">
      <h2 className="text-xs font-medium tracking-wide text-fg">{left}</h2>
      {right && <span className="font-mono text-[11px] text-faint">{right}</span>}
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "ok" | "warn";
}) {
  const toneClass =
    tone === "warn" ? "text-danger" : tone === "ok" ? "text-ok" : "text-fg";
  return (
    <div className="rounded border border-line bg-panel px-3 py-2">
      <div className="text-[11px] text-faint">{label}</div>
      <div className={`mt-0.5 font-mono text-lg leading-tight ${toneClass}`}>
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-faint">{hint}</div>}
    </div>
  );
}

export function StreamingPlaceholder({
  beats,
  thinking = "",
}: {
  beats: Beat[];
  thinking?: string;
}) {
  return (
    <div className="space-y-3">
      <Chip tone="accent">生成中…</Chip>
      {thinking && (
        <div className="rounded border border-line bg-panel px-2.5 py-2">
          <div className="text-[11px] text-faint">导演正在权衡</div>
          <p className="mt-1 font-mono text-[11px] leading-relaxed text-muted opacity-70">
            …{thinking}
          </p>
        </div>
      )}
      {beats.length > 0 && (
        <ol className="flex gap-2 overflow-x-auto">
          {beats.map((b) => (
            <li
              key={b.id}
              className="animate-rise min-w-[168px] flex-1 rounded border border-line bg-panel px-2.5 py-2"
              style={{ borderTop: `2px solid ${INTENSITY_COLORS[b.intensity - 1]}` }}
            >
              <div className="font-mono text-[11px] text-faint">{b.id}</div>
              <p className="mt-1 text-xs leading-snug">{b.summary}</p>
              <p className="mt-1 text-[11px] text-muted">{b.emotion}</p>
            </li>
          ))}
        </ol>
      )}
      {[0, 1, 2].map((i) => (
        <div key={i} className="skeleton h-24 rounded-md border border-line" />
      ))}
    </div>
  );
}
