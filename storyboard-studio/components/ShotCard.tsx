"use client";

import {
  INTENSITY_COLORS,
  SHOT_SIZES,
  type Beat,
  type RefineAction,
  type Shot,
} from "@/lib/types";
import { Button, Chip, EditableText } from "./ui";

/** 一个镜头。左轨颜色 = 它所属节拍的情绪强度，扫一眼就能看出节奏起伏。 */

export interface ShotCardProps {
  shot: Shot;
  beat?: Beat;
  /** 节奏条点击后要滚动到这张卡片，所以 id 必须落在列表项本身上 */
  domId: string;
  index: number;
  total: number;
  busy: boolean;
  anyBusy: boolean;
  live: boolean;
  onEdit: (patch: Partial<Shot>) => void;
  onAction: (action: RefineAction) => void;
  onDelete: () => void;
  onMove: (dir: -1 | 1) => void;
}

export function ShotCard({
  shot,
  beat,
  domId,
  index,
  total,
  busy,
  anyBusy,
  live,
  onEdit,
  onAction,
  onDelete,
  onMove,
}: ShotCardProps) {
  const rail = INTENSITY_COLORS[(beat?.intensity ?? 1) - 1];
  const disabled = anyBusy;

  return (
    <li
      id={domId}
      className="animate-rise relative overflow-hidden rounded-md border border-line bg-panel"
      style={{ borderLeft: `3px solid ${rail}` }}
    >
      {busy && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-ink/72 backdrop-blur-[1px]">
          <span className="skeleton rounded px-3 py-1.5 text-xs text-fg">
            正在重写这一镜…
          </span>
        </div>
      )}

      {/* 头部：镜号 / 景别 / 运镜 / 时长 / 所属节拍 */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line/70 bg-panel2/50 px-3 py-2">
        <span className="font-mono text-xs text-faint">#{index + 1}</span>

        <select
          value={shot.shotSize}
          onChange={(e) => onEdit({ shotSize: e.target.value as Shot["shotSize"] })}
          className="rounded border border-line2 bg-panel3 px-1.5 py-0.5 text-xs text-fg"
          title="景别"
        >
          {SHOT_SIZES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <input
          value={shot.camera}
          onChange={(e) => onEdit({ camera: e.target.value })}
          className="w-20 rounded border border-transparent bg-transparent px-1 py-0.5 text-xs text-muted hover:border-line2"
          title="运镜"
          placeholder="运镜"
        />

        <label className="flex items-center gap-1 text-xs text-muted" title="镜头时长（秒）">
          <input
            type="number"
            min={0.5}
            max={15}
            step={0.5}
            value={shot.durationSec}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n)) {
                onEdit({ durationSec: Math.min(15, Math.max(0.5, Math.round(n * 10) / 10)) });
              }
            }}
            className="w-14 rounded border border-transparent bg-transparent px-1 py-0.5 text-right font-mono text-xs text-fg hover:border-line2"
          />
          s
        </label>

        {beat && (
          <span
            className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-faint"
            title={`所属节拍：${beat.summary}`}
          >
            <span
              className="inline-block size-1.5 rounded-full"
              style={{ background: rail }}
            />
            {beat.id} · {beat.emotion}
          </span>
        )}

        <div className={`flex gap-1 ${beat ? "" : "ml-auto"}`}>
          <button
            type="button"
            onClick={() => onMove(-1)}
            disabled={index === 0}
            title="上移"
            className="rounded px-1 text-xs text-faint hover:text-fg disabled:opacity-30"
          >
            ↑
          </button>
          <button
            type="button"
            onClick={() => onMove(1)}
            disabled={index === total - 1}
            title="下移"
            className="rounded px-1 text-xs text-faint hover:text-fg disabled:opacity-30"
          >
            ↓
          </button>
        </div>
      </div>

      {/* 主体 */}
      <div className="space-y-1 px-3 py-2.5 text-[13px] leading-relaxed">
        <Field label="画面">
          <EditableText
            value={shot.description}
            multiline
            placeholder="（缺少画面描述）"
            onCommit={(description) => onEdit({ description, edited: true })}
            className="text-fg"
          />
        </Field>

        <Field label="台词">
          <EditableText
            value={shot.dialogue}
            multiline
            placeholder="—（原文无台词）"
            onCommit={(dialogue) => onEdit({ dialogue, edited: true })}
            className={shot.dialogue ? "text-accent" : ""}
          />
        </Field>

        <Field label="音效">
          <EditableText
            value={shot.sfx}
            placeholder="—"
            onCommit={(sfx) => onEdit({ sfx, edited: true })}
            className="text-muted"
          />
        </Field>

        {shot.rationale && (
          <Field label="依据">
            <EditableText
              value={shot.rationale}
              multiline
              onCommit={(rationale) => onEdit({ rationale, edited: true })}
              className="text-muted"
            />
          </Field>
        )}

        {shot.flags && shot.flags.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-1">
            {shot.flags.map((f) => (
              <Chip key={f} tone="warn" title="生成时被自动修正或存疑，请人工确认">
                ⚠ {f}
              </Chip>
            ))}
          </div>
        )}
      </div>

      {/* 操作区：分镜师日常最高频的四个动作 */}
      <div className="flex flex-wrap items-center gap-1 border-t border-line/70 px-3 py-1.5">
        <Button
          onClick={() => onAction("split")}
          disabled={disabled || !live}
          title={live ? "在情绪转折处把这个镜头拆成两个" : "演示模式下不可用"}
        >
          拆分
        </Button>
        <Button
          onClick={() => onAction("merge")}
          disabled={disabled || !live || index === total - 1}
          title={live ? "与下一个镜头合并" : "演示模式下不可用"}
        >
          合并
        </Button>
        <Button
          onClick={() => onAction("rewrite")}
          disabled={disabled || !live}
          title={live ? "保持景别与时长，把画面描述改得更可拍" : "演示模式下不可用"}
        >
          改写描述
        </Button>
        <Button
          onClick={() => onAction("regenerate")}
          disabled={disabled || !live}
          title={live ? "保留叙事目的，换一套镜头设计" : "演示模式下不可用"}
        >
          换个设计
        </Button>

        <div className="ml-auto flex items-center gap-2">
          {shot.edited && <Chip>已编辑</Chip>}
          <Button
            variant="danger"
            onClick={onDelete}
            disabled={disabled}
            title="删除这一镜"
          >
            删除
          </Button>
        </div>
      </div>
    </li>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <span className="w-8 shrink-0 pt-1 text-[11px] text-faint select-none">
        {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
