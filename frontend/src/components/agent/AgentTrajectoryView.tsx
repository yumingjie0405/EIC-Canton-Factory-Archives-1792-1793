"use client";

import React, { useState } from "react";
import {
  GitFork,
  ChevronRight,
  ChevronDown,
  Wrench,
  Clock,
  CheckCircle2,
  AlertCircle,
  ShieldAlert,
  BrainCircuit,
  Terminal,
} from "lucide-react";
import { AgentStepRecord, ToolCallRecord } from "@/types/agent";

interface AgentTrajectoryViewProps {
  trajectory: AgentStepRecord[];
}

export const AgentTrajectoryView: React.FC<AgentTrajectoryViewProps> = ({ trajectory }) => {
  const [expandedCalls, setExpandedCalls] = useState<Record<string, boolean>>({});

  if (!trajectory || trajectory.length === 0) {
    return (
      <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-6 text-center text-slate-400 text-xs">
        <GitFork className="w-6 h-6 text-slate-600 mx-auto mb-2" />
        <p>暂无研究步骤执行轨迹记录。</p>
      </div>
    );
  }

  const toggleCall = (callId: string) => {
    setExpandedCalls((prev) => ({
      ...prev,
      [callId]: !prev[callId],
    }));
  };

  return (
    <div className="w-full bg-slate-900/90 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-4">
      <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">
            <GitFork className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-slate-200">
              Agent 思考与检索轨迹审计 (可追溯执行树)
            </h3>
            <p className="text-xs text-slate-400">
              共完成 {trajectory.length} 轮自主推导与工具迭代
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-4 relative before:absolute before:inset-0 before:left-3.5 before:w-0.5 before:bg-slate-800 before:z-0">
        {trajectory.map((step) => {
          return (
            <div key={step.step_number} className="relative z-10 pl-8 space-y-3">
              {/* 步骤节点标记 */}
              <div className="absolute left-1 top-1 w-5 h-5 rounded-full bg-indigo-600 border-2 border-slate-900 text-white flex items-center justify-center text-[10px] font-bold shadow-md">
                {step.step_number}
              </div>

              {/* 步骤卡片 */}
              <div className="bg-slate-950/70 border border-slate-800 rounded-xl p-4 space-y-3">
                {/* 步骤目标 */}
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 border border-indigo-500/30 text-xs font-mono font-medium">
                      步骤 #{step.step_number}
                    </span>
                    <h4 className="text-xs font-semibold text-slate-200">
                      {step.sub_goal}
                    </h4>
                  </div>
                  <span
                    className={`text-[11px] px-2 py-0.5 rounded-full border ${
                      step.step_status === "completed"
                        ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
                        : "bg-amber-500/10 text-amber-400 border-amber-500/20"
                    }`}
                  >
                    {step.step_status === "completed" ? "已完成" : "执行中"}
                  </span>
                </div>

                {/* Agent 意图与思考推理 (Thought) */}
                <div className="flex items-start gap-2 text-xs text-slate-300 bg-slate-900/80 p-3 rounded-lg border border-slate-800">
                  <BrainCircuit className="w-4 h-4 text-purple-400 shrink-0 mt-0.5" />
                  <div className="leading-relaxed font-sans select-text">
                    <strong className="text-purple-300">Agent 推理意图: </strong>
                    {step.thought}
                  </div>
                </div>

                {/* 调用的工具清单 */}
                {step.tool_calls && step.tool_calls.length > 0 && (
                  <div className="space-y-2 pt-1">
                    <div className="text-[11px] text-slate-400 font-medium flex items-center gap-1.5">
                      <Wrench className="w-3.5 h-3.5 text-slate-500" />
                      <span>调用安全工具 ({step.tool_calls.length} 次):</span>
                    </div>

                    <div className="space-y-2">
                      {step.tool_calls.map((call) => {
                        const isExpanded = !!expandedCalls[call.call_id];
                        return (
                          <div
                            key={call.call_id}
                            className="bg-slate-900/50 border border-slate-800/80 rounded-lg p-2.5 text-xs space-y-2 transition-colors hover:border-slate-700"
                          >
                            <div className="flex items-center justify-between gap-2 flex-wrap">
                              <div className="flex items-center gap-2">
                                <span className="font-mono px-2 py-0.5 rounded bg-purple-500/10 text-purple-300 border border-purple-500/30 text-[11px]">
                                  {call.tool_name}
                                </span>

                                <span className="text-[11px] text-slate-400 truncate max-w-xs">
                                  {call.output_summary}
                                </span>
                              </div>

                              <div className="flex items-center gap-2 shrink-0">
                                {call.evidence_count > 0 && (
                                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono">
                                    +{call.evidence_count} 证据
                                  </span>
                                )}

                                <span className="text-[10px] text-slate-500 font-mono flex items-center gap-1">
                                  <Clock className="w-3 h-3" />
                                  {call.latency_ms.toFixed(0)}ms
                                </span>

                                {call.status === "success" && (
                                  <span className="text-emerald-400 flex items-center" title="成功">
                                    <CheckCircle2 className="w-3.5 h-3.5" />
                                  </span>
                                )}
                                {call.status === "intercepted" && (
                                  <span className="text-purple-400 flex items-center" title="安全拦截/越权">
                                    <ShieldAlert className="w-3.5 h-3.5" />
                                  </span>
                                )}
                                {call.status === "error" && (
                                  <span className="text-rose-400 flex items-center" title="调用异常">
                                    <AlertCircle className="w-3.5 h-3.5" />
                                  </span>
                                )}

                                <button
                                  type="button"
                                  onClick={() => toggleCall(call.call_id)}
                                  className="text-slate-400 hover:text-slate-200 transition-colors p-0.5 rounded"
                                  title="查看调用参数与结构化结果"
                                >
                                  {isExpanded ? (
                                    <ChevronDown className="w-3.5 h-3.5" />
                                  ) : (
                                    <ChevronRight className="w-3.5 h-3.5" />
                                  )}
                                </button>
                              </div>
                            </div>

                            {/* 展开的入参与完整出参摘要 */}
                            {isExpanded && (
                              <div className="pt-2 border-t border-slate-800 text-[11px] font-mono space-y-2 bg-slate-950/80 p-2.5 rounded">
                                <div>
                                  <span className="text-slate-500 flex items-center gap-1">
                                    <Terminal className="w-3 h-3" />
                                    调用输入入参:
                                  </span>
                                  <pre className="text-slate-300 overflow-x-auto p-1 bg-slate-900 rounded mt-0.5">
                                    {JSON.stringify(call.arguments, null, 2)}
                                  </pre>
                                </div>
                                <div>
                                  <span className="text-slate-500">结果摘要:</span>
                                  <p className="text-slate-300 font-sans mt-0.5">
                                    {call.output_summary}
                                  </p>
                                </div>
                                {call.error_message && (
                                  <div className="text-rose-400">
                                    <span>异常原因: </span>
                                    <span>{call.error_message}</span>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
