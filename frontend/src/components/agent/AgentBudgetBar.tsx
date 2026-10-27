"use client";

import React from "react";
import { CheckCircle2, AlertTriangle, Clock, Wrench, GitFork, ShieldCheck, AlertCircle } from "lucide-react";
import { AgentBudgetUsed } from "@/types/agent";

interface AgentBudgetBarProps {
  taskId: string;
  evidenceSufficient: boolean;
  budgetUsed: AgentBudgetUsed;
  warnings?: string[];
}

export const AgentBudgetBar: React.FC<AgentBudgetBarProps> = ({
  taskId,
  evidenceSufficient,
  budgetUsed,
  warnings = [],
}) => {
  const steps_executed = budgetUsed?.steps_executed ?? 0;
  const max_steps = budgetUsed?.max_steps ?? 4;
  const tool_calls_executed = budgetUsed?.tool_calls_executed ?? 0;
  const max_tool_calls = budgetUsed?.max_tool_calls ?? 6;
  const elapsed_ms = typeof budgetUsed?.elapsed_ms === "number" ? budgetUsed.elapsed_ms : 0;
  const budget_exhausted = Boolean(budgetUsed?.budget_exhausted);

  return (
    <div className="w-full bg-slate-900/80 border border-slate-800 rounded-xl p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3 text-xs">
        {/* 左侧状态标识 */}
        <div className="flex items-center gap-2 flex-wrap">
          <div className="px-2 py-0.5 rounded bg-slate-800 text-slate-400 font-mono text-[11px] border border-slate-700/60">
            Task: {taskId.slice(0, 8)}...
          </div>

          {evidenceSufficient ? (
            <div className="flex items-center gap-1.5 px-2.5 py-0.8 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span>档案证据确凿 (双重校验)</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 px-2.5 py-0.8 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 font-medium">
              <AlertTriangle className="w-3.5 h-3.5" />
              <span>无确凿证据·严谨拒答防伪</span>
            </div>
          )}

          {budget_exhausted && (
            <div className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20">
              <AlertCircle className="w-3.5 h-3.5" />
              <span>达到配置预算上限已收敛</span>
            </div>
          )}
        </div>

        {/* 右侧预算消耗指标 */}
        <div className="flex items-center gap-4 text-slate-400 flex-wrap">
          <div className="flex items-center gap-1.5">
            <GitFork className="w-3.5 h-3.5 text-indigo-400" />
            <span>
              步骤: <strong className="text-slate-200">{steps_executed}</strong> / {max_steps}
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            <Wrench className="w-3.5 h-3.5 text-purple-400" />
            <span>
              工具调用: <strong className="text-slate-200">{tool_calls_executed}</strong> / {max_tool_calls}
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5 text-slate-500" />
            <span>耗时: {elapsed_ms.toFixed(0)} ms</span>
          </div>
        </div>
      </div>

      {/* 告警信息通知 (如果存在) */}
      {warnings && warnings.length > 0 && (
        <div className="pt-2 border-t border-slate-800/60 space-y-1">
          {warnings.map((warn, i) => (
            <div key={i} className="text-[11px] text-amber-300 flex items-center gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5 shrink-0 text-amber-400" />
              <span>{warn}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
