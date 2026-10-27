"use client";

import React from "react";
import { GitFork, Check, X, ArrowRight, ShieldCheck, Clock } from "lucide-react";
import { AgentPlanResponse } from "@/types/agent";

interface AgentPlanPreviewModalProps {
  plan: AgentPlanResponse | null;
  isOpen: boolean;
  onClose: () => void;
  onExecute: () => void;
  isExecuting?: boolean;
}

export const AgentPlanPreviewModal: React.FC<AgentPlanPreviewModalProps> = ({
  plan,
  isOpen,
  onClose,
  onExecute,
  isExecuting = false,
}) => {
  if (!isOpen || !plan) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-2xl bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl p-6 space-y-5">
        {/* 弹窗头部 */}
        <div className="flex items-center justify-between pb-3 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
              <GitFork className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-slate-100">
                Agent 动态任务拆分方案预览
              </h3>
              <p className="text-xs text-slate-400">
                在自主执行前审核拆解的研究子目标与预估资源开销
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 提问内容 */}
        <div className="p-3 bg-slate-950/80 rounded-xl border border-slate-800 text-xs text-slate-300">
          <span className="text-slate-500 font-medium">研究课题: </span>
          <span className="font-semibold text-slate-200">{plan.question}</span>
        </div>

        {/* 拆分出的子步骤列表 */}
        <div className="space-y-2">
          <div className="text-xs font-semibold text-slate-300 flex items-center justify-between">
            <span>规划研究步骤 ({plan.proposed_steps.length} 步):</span>
            <span className="text-indigo-400 font-mono text-[11px]">
              预计工具调用 ~{plan.estimated_tool_calls} 次
            </span>
          </div>
          <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
            {plan.proposed_steps.map((step, idx) => (
              <div
                key={idx}
                className="p-3 bg-slate-950/60 border border-slate-800/80 rounded-xl text-xs text-slate-300 flex items-start gap-3"
              >
                <span className="flex-shrink-0 w-5 h-5 rounded-full bg-indigo-500/20 text-indigo-400 border border-indigo-500/30 flex items-center justify-center font-mono text-[10px] font-bold">
                  {idx + 1}
                </span>
                <span className="leading-relaxed mt-0.5">{step}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 涉及的目标历史实体 */}
        {plan.target_entities && plan.target_entities.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap text-xs pt-1">
            <span className="text-slate-500">重点考据实体:</span>
            {plan.target_entities.map((ent, i) => (
              <span
                key={i}
                className="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 border border-indigo-500/30 text-[11px]"
              >
                {ent}
              </span>
            ))}
          </div>
        )}

        {/* 底部按钮栏 */}
        <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => {
              onClose();
              onExecute();
            }}
            disabled={isExecuting}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white shadow-lg shadow-indigo-500/20 transition-all"
          >
            <span>批准并启动 Agent 自主考据</span>
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
};
