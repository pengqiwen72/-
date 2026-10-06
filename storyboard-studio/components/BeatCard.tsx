"use client";

import { useState } from "react";

import { INTENSITY_COLORS, type Beat } from "@/lib/types";
import { Button, EditableText } from "./ui";

/**
 * 一个节拍。
 *
 * 节拍是叙事层，镜头是执行层，所以这张卡片的职责是「让人改得动叙事结构」：
 * 摘要、情绪、强度都能就地改，顺序能调，多出来的节拍能删。改完强度之后
 * 顶边颜色、节奏条、镜头左轨会一起变——三处共用同一套色阶，不是巧合，
 * 强度在这个产品里就是「颜色」这一个语义。
 */
export interface BeatCardProps {
  beat: Beat;
  index: number;
  total: number;
  /** 这个节拍下挂着几个镜头。删除时会一并删掉，所以要显式告诉用户 */
  shotCount: number;
  busy: boolean;
  /** 任何一处正在打模型（含单镜精修），此时禁止再发起操作 */
  anyBusy: boolean;
  live: boolean;
  onEdit: (patch: Partial<Beat>) => void;
  onMove: (dir: -1 | 1) => void;
  onDelete: () => void;
  onRegenerate: () => void;
}

export function BeatCard({
  beat,
  index,
  total,
  shotCount,
  busy,
  anyBusy,
  live,
  onEdit,
  onMove,
  onDelete,
  onRegenerate,
}: BeatCardProps) {
  // 删除会连带删掉这个节拍下的所有镜头，且目前没有撤销，
  // 所以要求再点一次确认，并把「要删掉几个镜头」摆在按钮上。
  const [confirming, setConfirming] = useState(false);

  return (
    <li
      className="animate-rise relative min-w-[230px] flex-1 overflow-hidden rounded border border-line bg-panel px-2.5 py-2"
      style={{ borderTop: `2px solid ${INTENSITY_COLORS[beat.intensity - 1]}` }}
      // 鼠标离开卡片就撤销确认，避免「点了一下，过一会儿回来又点一下」误删
      onMouseLeave={() => setConfirming(false)}
    >
      {busy && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-ink/72 backdrop-blur-[1px]">
          <span className="skeleton rounded px-3 py-1.5 text-xs text-fg">
            正在重跑本节拍…
          </span>
        </div>
      )}

      <div className="flex items-center justify-between text-[11px] text-faint">
        <span className="font-mono">{beat.id}</span>
        <span title="这个节拍下的镜头数">{shotCount} 镜</span>
      </div>

      <div className="mt-1">
        <EditableText
          value={beat.summary}
          placeholder="（无摘要）"
          onCommit={(summary) => onEdit({ summary })}
          className="text-xs leading-snug text-fg"
        />
      </div>
      <div className="mt-1">
        <EditableText
          value={beat.emotion}
          placeholder="（无情绪标注）"
          onCommit={(emotion) => onEdit({ emotion })}
          className="text-[11px] text-muted"
        />
      </div>

      <div className="mt-1.5 flex items-center gap-1">
        <span className="mr-0.5 text-[11px] text-faint">强度</span>
        {INTENSITY_COLORS.map((color, i) => {
          const value = i + 1;
          const active = beat.intensity === value;
          return (
            <button
              key={value}
              type="button"
              title={`情感强度 ${value}`}
              aria-label={`情感强度 ${value}`}
              aria-pressed={active}
              onClick={() => onEdit({ intensity: value })}
              style={{ background: color }}
              className={`h-3.5 w-3.5 rounded-sm border transition-opacity ${
                active
                  ? "border-fg/60 opacity-100"
                  : "border-transparent opacity-30 hover:opacity-70"
              }`}
            />
          );
        })}
      </div>

      <div className="mt-2 space-y-1 border-t border-line/70 pt-1.5">
        <div className="flex items-center gap-1">
          <Button
            onClick={() => onMove(-1)}
            disabled={anyBusy || index === 0}
            title="与前一个节拍交换位置"
          >
            ↑
          </Button>
          <Button
            onClick={() => onMove(1)}
            disabled={anyBusy || index === total - 1}
            title="与后一个节拍交换位置"
          >
            ↓
          </Button>
          <Button
            onClick={onRegenerate}
            disabled={anyBusy || !live}
            className="flex-1"
            title={
              live
                ? "按这个节拍重新设计它下面的全部镜头"
                : "演示模式下不可用"
            }
          >
            重跑镜头
          </Button>
        </div>

        {/* 按钮宽度在两种状态下一样，所以确认时点击目标不会从指针下跑掉 */}
        <Button
          variant="danger"
          onClick={() => (confirming ? onDelete() : setConfirming(true))}
          disabled={anyBusy}
          className="w-full"
          title={confirming ? "再点一次即删除，无法撤销" : "删除这个节拍"}
        >
          {confirming ? `确认删除（含 ${shotCount} 个镜头）` : "删除节拍"}
        </Button>
      </div>
    </li>
  );
}
