"use client";

import type { AuditIssue, AuditResult, AuditSeverity } from "@/lib/types";
import { Button, Chip } from "./ui";

/** 忠实度自查的结果。这是产品的信任层：先告诉创作者哪里不能信。 */

const SEVERITY_STYLE: Record<AuditSeverity, { label: string; dot: string; text: string }> = {
  high: { label: "高", dot: "bg-danger", text: "text-danger" },
  medium: { label: "中", dot: "bg-accent", text: "text-accent" },
  low: { label: "低", dot: "bg-faint", text: "text-muted" },
};

export function AuditPanel({
  result,
  running,
  error,
  onRun,
  canRun,
  reason,
}: {
  result: AuditResult | null;
  running: boolean;
  error: string | null;
  onRun: () => void;
  canRun: boolean;
  reason: string;
}) {
  const counts = result
    ? {
        high: result.issues.filter((i) => i.severity === "high").length,
        medium: result.issues.filter((i) => i.severity === "medium").length,
        low: result.issues.filter((i) => i.severity === "low").length,
      }
    : null;

  return (
    <section className="rounded border border-line bg-panel">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <h2 className="text-xs font-medium">忠实度自查</h2>
        <span className="text-[11px] text-faint">
          让模型拿生成结果回原文对质，专找「无中生有」和「台词被改写」
        </span>

        <div className="ml-auto flex items-center gap-2">
          {counts && (
            <span className="flex items-center gap-1.5">
              {counts.high > 0 && <Chip tone="warn">高 {counts.high}</Chip>}
              {counts.medium > 0 && <Chip>中 {counts.medium}</Chip>}
              {counts.low > 0 && <Chip>低 {counts.low}</Chip>}
              {result && result.issues.length === 0 && (
                <Chip tone="ok">未发现问题</Chip>
              )}
            </span>
          )}
          <Button
            onClick={onRun}
            disabled={!canRun || running}
            title={canRun ? undefined : reason}
            variant="ghost"
          >
            {running ? "核对中…" : result ? "重新自查" : "对照原文自查"}
          </Button>
        </div>
      </div>

      <div className="px-3 py-2.5">
        {!result && !running && (
          <p className="text-xs leading-relaxed text-muted">
            {canRun
              ? "生成的分镜越好看，越容易掩盖它编造的内容。建议在交给画师之前跑一次自查——它会多花一次模型调用。"
              : reason}
          </p>
        )}

        {running && (
          <p className="text-xs text-muted">正在逐镜对照原文，通常需要十几秒…</p>
        )}

        {error && <p className="text-xs text-danger">{error}</p>}

        {result && !running && (
          <div className="space-y-2.5">
            <p className="text-xs leading-relaxed text-muted">
              已核对 {result.reviewed} 个镜头
              {result.verdict && <> · {result.verdict}</>}
            </p>

            {result.issues.length > 0 && (
              <ul className="space-y-2">
                {result.issues.map((issue, i) => (
                  <IssueRow key={`${issue.shotId}-${i}`} issue={issue} />
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function IssueRow({ issue }: { issue: AuditIssue }) {
  const style = SEVERITY_STYLE[issue.severity];
  const shotNumber = issue.shotId.replace(/^S/, "");

  return (
    <li className="rounded border border-line bg-panel2 px-2.5 py-2 text-xs">
      <button
        type="button"
        onClick={() =>
          document
            .getElementById(`shot-${issue.shotId}`)
            ?.scrollIntoView({ behavior: "smooth", block: "center" })
        }
        className="flex w-full items-center gap-2 text-left"
        title="跳转到这一镜"
      >
        <span className={`inline-block size-1.5 shrink-0 rounded-full ${style.dot}`} />
        <span className="font-mono text-fg">#{shotNumber}</span>
        <span className={`${style.text}`}>{issue.kind}</span>
        <span className="ml-auto text-faint">前往 →</span>
      </button>

      <div className="mt-1.5 space-y-1 pl-3.5 leading-relaxed">
        <p className={style.text}>{issue.detail}</p>
        {issue.quote && (
          <p className="text-faint">
            原文：<span className="text-muted">「{issue.quote}」</span>
          </p>
        )}
        {issue.suggestion && (
          <p className="text-muted">
            建议：<span className="text-fg">{issue.suggestion}</span>
          </p>
        )}
      </div>
    </li>
  );
}
