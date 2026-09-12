"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { SAMPLES } from "@/lib/samples";
import {
  auditSignature,
  download,
  normalizeBeats,
  renumber,
  toCSV,
  toMarkdown,
  totalDuration,
} from "@/lib/storyboard";
import {
  MEDIA,
  type AuditResult,
  type Beat,
  type Medium,
  type RefineAction,
  type Shot,
  type StreamEvent,
  type Storyboard as Board,
} from "@/lib/types";
import { AuditPanel } from "./AuditPanel";
import { Storyboard, StreamingPlaceholder } from "./Storyboard";
import { Button, Chip } from "./ui";

type Phase = "idle" | "generating" | "ready" | "error";

interface Status {
  live: boolean;
  model: string | null;
}

const MIN_CHARS = 20;

/** 只把精修需要的字段发给服务端，避免整份状态来回传。 */
function pickShot(s: Shot) {
  return {
    shotSize: s.shotSize,
    camera: s.camera,
    description: s.description,
    dialogue: s.dialogue,
    sfx: s.sfx,
    durationSec: s.durationSec,
    rationale: s.rationale,
  };
}

export default function Studio() {
  const [status, setStatus] = useState<Status | null>(null);
  const [text, setText] = useState(SAMPLES[0].text);
  const [medium, setMedium] = useState<Medium>("vertical");
  const [target, setTarget] = useState(SAMPLES[0].targetDurationSec);

  const [phase, setPhase] = useState<Phase>("idle");
  const [title, setTitle] = useState("");
  const [source, setSource] = useState<"live" | "demo" | null>(null);
  const [beats, setBeats] = useState<Beat[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  /** 模型出字之前的思考增量，只用来显示进度。留尾部即可，不必攒全文。 */
  const [thinking, setThinking] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busyShotId, setBusyShotId] = useState<string | null>(null);
  const [busyBeatId, setBusyBeatId] = useState<string | null>(null);

  const [audit, setAudit] = useState<AuditResult | null>(null);
  /** 自查时镜头的指纹。镜头改过之后自查结论就过期了，必须让用户知道。 */
  const [auditedSignature, setAuditedSignature] = useState<string | null>(null);
  const [auditRunning, setAuditRunning] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/status")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => setStatus({ live: false, model: null }));
  }, []);

  const live = status?.live ?? false;
  const board: Board | null =
    beats.length > 0 || shots.length > 0
      ? {
          title: title || "未命名",
          beats,
          shots,
          meta: {
            source: source ?? "live",
            medium,
            targetDurationSec: target,
            createdAt: new Date().toISOString(),
          },
        }
      : null;

  // --- 生成 -----------------------------------------------------------------

  const generate = useCallback(async () => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    setPhase("generating");
    setBeats([]);
    setShots([]);
    setTitle("");
    setNotice(null);
    setThinking("");
    setError(null);
    setAudit(null);
    setAuditedSignature(null);
    setAuditError(null);

    const handle = (e: StreamEvent) => {
      switch (e.t) {
        case "meta":
          setTitle(e.title);
          setSource(e.source);
          break;
        case "beat":
          setBeats((prev) => [...prev, e.beat]);
          break;
        case "shot":
          setShots((prev) => [...prev, e.shot]);
          break;
        case "thinking":
          setThinking((prev) => (prev + e.text).slice(-240));
          break;
        case "notice":
          setNotice(e.message);
          break;
        case "error":
          setError(e.message);
          setPhase("error");
          break;
        case "done":
          setPhase("ready");
          break;
      }
    };

    try {
      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, medium, targetDurationSec: target }),
        signal: ac.signal,
      });

      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { error?: string };
        setError(data.error ?? `请求失败（${res.status}）`);
        setPhase("error");
        return;
      }
      if (!res.body) {
        setError("服务端没有返回数据流。");
        setPhase("error");
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 1);
          if (!line) continue;
          try {
            handle(JSON.parse(line) as StreamEvent);
          } catch {
            // 单行坏掉不该中断整次生成
          }
        }
      }
    } catch (err) {
      if (ac.signal.aborted) return;
      setError(err instanceof Error ? err.message : "网络错误");
      setPhase("error");
    }
  }, [text, medium, target]);

  // 生成完成后把结果滚进视野
  useEffect(() => {
    if (phase === "ready" && shots.length > 0) {
      resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    // 只在生成结束的那一刻滚一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  // --- 镜头操作 -------------------------------------------------------------

  const editShot = useCallback((id: string, patch: Partial<Shot>) => {
    setShots((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  }, []);

  const deleteShot = useCallback((id: string) => {
    setShots((prev) => renumber(prev.filter((s) => s.id !== id)));
  }, []);

  const moveShot = useCallback((id: string, dir: -1 | 1) => {
    setShots((prev) => {
      const i = prev.findIndex((s) => s.id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return renumber(next);
    });
  }, []);

  const runAction = useCallback(
    async (id: string, action: RefineAction) => {
      const idx = shots.findIndex((s) => s.id === id);
      const shot = shots[idx];
      if (!shot) return;
      const next = shots[idx + 1];
      if (action === "merge" && !next) return;

      setBusyShotId(id);
      setError(null);
      try {
        const res = await fetch("/api/refine", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            action,
            medium,
            shot: pickShot(shot),
            next: action === "merge" && next ? pickShot(next) : undefined,
            beat: beats.find((b) => b.id === shot.beatId),
            sourceText: text,
          }),
        });
        const data = (await res.json()) as {
          shots?: Array<Omit<Shot, "id" | "beatId">>;
          error?: string;
        };
        if (!res.ok || !data.shots) {
          throw new Error(data.error ?? `精修失败（${res.status}）`);
        }

        const replacement: Shot[] = data.shots.map((s) => ({
          ...s,
          id: "",
          beatId: shot.beatId,
          edited: true,
        }));

        setShots((prev) => {
          const list = [...prev];
          const i = list.findIndex((s) => s.id === id);
          if (i < 0) return prev;
          list.splice(i, action === "merge" ? 2 : 1, ...replacement);
          return renumber(list);
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "精修失败");
      } finally {
        setBusyShotId(null);
      }
    },
    [shots, beats, medium, text],
  );

  // --- 节拍操作 -------------------------------------------------------------

  const editBeat = useCallback((id: string, patch: Partial<Beat>) => {
    setBeats((prev) => prev.map((b) => (b.id === id ? { ...b, ...patch } : b)));
  }, []);

  /** 节拍被删，它下面的镜头也就失去了归属，一并删掉（界面上有二次确认）。 */
  const deleteBeat = useCallback(
    (id: string) => {
      const next = normalizeBeats(
        beats.filter((b) => b.id !== id),
        shots.filter((s) => s.beatId !== id),
      );
      setBeats(next.beats);
      setShots(next.shots);
    },
    [beats, shots],
  );

  const moveBeat = useCallback(
    (id: string, dir: -1 | 1) => {
      const i = beats.findIndex((b) => b.id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= beats.length) return;
      const swapped = [...beats];
      [swapped[i], swapped[j]] = [swapped[j], swapped[i]];
      // 镜头数组也要跟着重排：界面按节拍分组渲染，而 CSV 按数组顺序导出，
      // 只换节拍顺序会让导出的镜号顺序和读者看到的对不上。
      const next = normalizeBeats(swapped, shots);
      setBeats(next.beats);
      setShots(next.shots);
    },
    [beats, shots],
  );

  const regenerateBeat = useCallback(
    async (id: string) => {
      const beat = beats.find((b) => b.id === id);
      if (!beat) return;
      const own = shots.filter((s) => s.beatId === id);

      setBusyBeatId(id);
      setError(null);
      try {
        const res = await fetch("/api/beat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            medium,
            beat,
            shots: own.map(pickShot),
            // 保持全片时间轴不变；空节拍传 0，由提示词按信息量自行分配
            targetDurationSec: own.reduce((sum, s) => sum + s.durationSec, 0),
            sourceText: text,
          }),
        });
        const data = (await res.json()) as {
          shots?: Array<Omit<Shot, "id" | "beatId">>;
          error?: string;
        };
        if (!res.ok || !data.shots) {
          throw new Error(data.error ?? `重跑失败（${res.status}）`);
        }

        const replacement: Shot[] = data.shots.map((s) => ({
          ...s,
          id: "",
          beatId: id,
          edited: true,
        }));

        setShots((prev) => {
          const first = prev.findIndex((s) => s.beatId === id);
          if (first < 0) return renumber([...prev, ...replacement]);
          // 新镜头落在原来第一个镜头的位置，其余属于该节拍的旧镜头丢掉。
          // 不用 splice(first, count) 是因为生成时同一节拍的镜头未必严格相邻。
          const next: Shot[] = [];
          let placed = false;
          for (const s of prev) {
            if (s.beatId !== id) {
              next.push(s);
            } else if (!placed) {
              next.push(...replacement);
              placed = true;
            }
          }
          return renumber(next);
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "重跑失败");
      } finally {
        setBusyBeatId(null);
      }
    },
    [beats, shots, medium, text],
  );

  // --- 忠实度自查 -----------------------------------------------------------

  const signature = auditSignature(beats, shots);

  const runAudit = useCallback(async () => {
    setAuditRunning(true);
    setAuditError(null);
    const sig = auditSignature(beats, shots);
    try {
      const res = await fetch("/api/audit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sourceText: text,
          beats,
          shots: shots.map((s) => ({
            id: s.id,
            beatId: s.beatId,
            shotSize: s.shotSize,
            description: s.description,
            dialogue: s.dialogue,
            rationale: s.rationale,
          })),
        }),
      });
      const data = (await res.json()) as AuditResult & { error?: string };
      if (!res.ok) throw new Error(data.error ?? `自查失败（${res.status}）`);
      setAudit({ issues: data.issues ?? [], reviewed: data.reviewed, verdict: data.verdict });
      setAuditedSignature(sig);
    } catch (err) {
      setAuditError(err instanceof Error ? err.message : "自查失败");
    } finally {
      setAuditRunning(false);
    }
  }, [shots, beats, text]);

  const auditStale = audit !== null && auditedSignature !== signature;

  // --- 导出 -----------------------------------------------------------------

  const exportBoard = useCallback(() => {
    if (!board) return;
    return board;
  }, [board]);

  const safeName = (title || "分镜表").replace(/[\\/:*?"<>|]/g, "");

  // --- 交互 -----------------------------------------------------------------

  const canGenerate = text.trim().length >= MIN_CHARS && phase !== "generating";
  const loading = phase === "generating";

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-line bg-ink/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1560px] items-center gap-3 px-5 py-3">
          <h1 className="text-sm font-medium tracking-wide">
            分镜工坊
            <span className="ml-2 text-xs font-normal text-faint">
              小说 → 镜头语言
            </span>
          </h1>
          <div className="ml-auto flex items-center gap-2">
            {status && (
              <Chip
                tone={status.live ? "ok" : "demo"}
                title={
                  status.live
                    ? `实时调用 ${status.model}`
                    : "未检测到 API Key，当前只提供内置样例"
                }
              >
                {status.live ? `实时 · ${status.model}` : "演示数据"}
              </Chip>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1560px] gap-5 p-5 lg:grid-cols-[minmax(330px,380px)_1fr]">
        {/* ---------------- 左栏：输入与设置 ---------------- */}
        <aside className="space-y-3 lg:sticky lg:top-[57px] lg:max-h-[calc(100vh-77px)] lg:overflow-y-auto lg:pr-1">
          {status && !status.live && (
            <div className="rounded border border-accent/40 bg-accent/10 px-3 py-2 text-xs leading-relaxed text-accent">
              未检测到 <code className="font-mono">ANTHROPIC_API_KEY</code>
              ，当前为演示模式：只能生成内置样例的缓存分镜，单镜精修不可用。
              配置 Key 后重启即可实时生成任意文本。
            </div>
          )}

          <section className="rounded border border-line bg-panel">
            <div className="flex items-center justify-between border-b border-line px-3 py-2">
              <span className="text-xs text-muted">原文</span>
              <span className="font-mono text-[11px] text-faint">
                {text.length} 字
              </span>
            </div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              spellCheck={false}
              placeholder="粘贴一章小说、一段剧本，或任何叙事文本…"
              className="h-64 w-full resize-y bg-transparent px-3 py-2.5 text-[13px] leading-relaxed outline-none placeholder:text-faint"
            />
            <div className="flex flex-wrap gap-1 border-t border-line px-3 py-2">
              {SAMPLES.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  title={s.note}
                  onClick={() => {
                    setText(s.text);
                    setTarget(s.targetDurationSec);
                  }}
                  className={`rounded border px-2 py-0.5 text-[11px] transition-colors ${
                    text === s.text
                      ? "border-accent/60 bg-accent/15 text-accent"
                      : "border-line text-muted hover:border-line2 hover:text-fg"
                  }`}
                >
                  {s.title}
                  <span className="ml-1 text-faint">{s.genre}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="space-y-2.5 rounded border border-line bg-panel px-3 py-2.5">
            <label className="block">
              <span className="text-xs text-muted">目标媒介</span>
              <select
                value={medium}
                onChange={(e) => setMedium(e.target.value as Medium)}
                className="mt-1 w-full rounded border border-line2 bg-panel2 px-2 py-1.5 text-[13px]"
              >
                {MEDIA.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-[11px] leading-relaxed text-faint">
                {MEDIA.find((m) => m.id === medium)?.hint}
              </span>
            </label>

            <label className="block">
              <span className="flex items-baseline justify-between text-xs text-muted">
                <span>目标时长</span>
                <span className="font-mono text-fg">{target}s</span>
              </span>
              <input
                type="range"
                min={15}
                max={300}
                step={5}
                value={target}
                onChange={(e) => setTarget(Number(e.target.value))}
                className="mt-1.5 w-full accent-[color:var(--color-accent)]"
              />
            </label>

            <Button
              variant="primary"
              onClick={generate}
              disabled={!canGenerate}
              className="w-full py-2 text-[13px]"
            >
              {loading ? "生成中…" : "生成分镜"}
            </Button>
            <p className="text-center text-[11px] text-faint">
              快捷键 ⌘/Ctrl + Enter
            </p>
          </section>

          {board && (
            <section className="space-y-2 rounded border border-line bg-panel px-3 py-2.5">
              <div className="flex items-baseline justify-between">
                <span className="text-xs text-muted">导出</span>
                <span className="font-mono text-[11px] text-faint">
                  {shots.length} 镜 · {totalDuration(shots).toFixed(1)}s
                </span>
              </div>
              <div className="flex gap-1.5">
                <Button
                  onClick={() => {
                    const b = exportBoard();
                    if (b) {
                      download(`${safeName}-分镜表.csv`, toCSV(b), "text/csv;charset=utf-8");
                    }
                  }}
                  className="flex-1"
                >
                  导出 CSV
                </Button>
                <Button
                  onClick={() => {
                    const b = exportBoard();
                    if (b) {
                      download(`${safeName}-分镜表.md`, toMarkdown(b), "text/markdown;charset=utf-8");
                    }
                  }}
                  className="flex-1"
                >
                  导出 Markdown
                </Button>
              </div>
            </section>
          )}

          <details className="rounded border border-line bg-panel px-3 py-2 text-xs text-muted">
            <summary className="cursor-pointer select-none text-muted">
              完成边界与实现说明
            </summary>
            <div className="mt-2 space-y-1.5 leading-relaxed">
              <p>
                <span className="text-ok">真实可用：</span>
                实时调用大模型做节拍分解与镜头设计（流式逐镜返回）、单镜拆分/合并/改写/重生成、
                就地编辑、景别分布与节奏统计、CSV / Markdown 导出。
                节拍层同样可操作：改摘要/情绪/强度、上下重排、删除（连带其下镜头，需二次确认），
                以及按改过的节拍重跑这一段戏的全部镜头。
              </p>
              <p>
                <span className="text-accent">缓存或 mock：</span>
                未配置 API Key 时，
                <code className="font-mono">lib/demo-cache.ts</code>{" "}
                里有一份人工撰写的样例分镜会被当作数据源回放，界面顶部会明确标注「演示数据」。
                它只对应内置的《雨夜归人》。
              </p>
              <p>
                <span className="text-danger">尚未完成：</span>
                分镜图/关键帧生成、多人协作与版本历史、节拍的拆分与手工新增、
                导出为专业软件（如 Storyboarder / Premiere）的工程格式。
              </p>
            </div>
          </details>
        </aside>

        {/* ---------------- 右栏：结果 ---------------- */}
        <section ref={resultRef} className="min-w-0 space-y-4">
          {error && (
            <div className="flex items-start gap-2 rounded border border-danger/40 bg-danger/10 px-3 py-2.5 text-xs leading-relaxed text-fg">
              <span className="text-danger">⚠</span>
              <div className="flex-1">{error}</div>
              <button
                type="button"
                onClick={() => setError(null)}
                className="text-faint hover:text-fg"
              >
                ✕
              </button>
            </div>
          )}

          {notice && !error && (
            <div className="rounded border border-line bg-panel px-3 py-2 text-xs leading-relaxed text-muted">
              {notice}
            </div>
          )}

          {phase === "idle" && shots.length === 0 && (
            <EmptyState onPick={() => generate()} disabled={!canGenerate} />
          )}

          {loading && shots.length === 0 && (
            <StreamingPlaceholder beats={beats} thinking={thinking} />
          )}

          {board && shots.length > 0 && (
            <>
              <div className="flex items-baseline gap-3">
                <h2 className="text-base font-medium">{board.title}</h2>
                <span className="font-mono text-xs text-faint">
                  {board.meta.source === "demo" ? "内置样例" : "实时生成"}
                </span>
              </div>
              <AuditPanel
                result={audit}
                running={auditRunning}
                error={auditError}
                onRun={runAudit}
                canRun={live}
                reason={
                  live
                    ? ""
                    : "演示模式下不支持自查：这个功能需要把分镜和原文一起发给模型逐镜比对。"
                }
              />
              {auditStale && (
                <p className="text-[11px] text-accent">
                  ⚠ 自查之后分镜已被改动（节拍或镜头），上面的结论可能已经过期，建议重新自查。
                </p>
              )}
              <Storyboard
                board={board}
                live={live}
                busyShotId={busyShotId}
                busyBeatId={busyBeatId}
                onEditShot={editShot}
                onAction={runAction}
                onDelete={deleteShot}
                onMove={moveShot}
                onEditBeat={editBeat}
                onMoveBeat={moveBeat}
                onDeleteBeat={deleteBeat}
                onRegenerateBeat={regenerateBeat}
              />
            </>
          )}
        </section>
      </main>

      <KeyboardShortcut onFire={generate} enabled={canGenerate} />
    </div>
  );
}

function KeyboardShortcut({
  onFire,
  enabled,
}: {
  onFire: () => void;
  enabled: boolean;
}) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && enabled) {
        e.preventDefault();
        onFire();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onFire, enabled]);
  return null;
}

function EmptyState({
  onPick,
  disabled,
}: {
  onPick: () => void;
  disabled: boolean;
}) {
  return (
    <div className="rounded border border-dashed border-line2 bg-panel/50 px-6 py-14 text-center">
      <p className="text-sm text-fg">左侧已载入一段样例，直接生成即可</p>
      <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-muted">
        也可以换成你自己的小说或剧本。生成会先拆出戏剧节拍，再为每个节拍设计镜头，
        每个镜头都会附上「为什么这样切」的依据。
      </p>
      <div className="mt-5">
        <Button variant="primary" onClick={onPick} disabled={disabled} className="px-4 py-2">
          生成分镜
        </Button>
      </div>
    </div>
  );
}
