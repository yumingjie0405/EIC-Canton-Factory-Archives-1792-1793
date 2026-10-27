"use client";

import React, { useState } from "react";
import { MentionReviewQueue } from "@/components/kg/review/MentionReviewQueue";
import { EntityMergeModal } from "@/components/kg/review/EntityMergeModal";
import { AuditHistoryDrawer } from "@/components/kg/review/AuditHistoryDrawer";
import { ShieldCheck, GitMerge, History, Sparkles, CheckCircle2 } from "lucide-react";

export default function KnowledgeReviewPage() {
  const [isMergeModalOpen, setIsMergeModalOpen] = useState(false);
  const [isAuditDrawerOpen, setIsAuditDrawerOpen] = useState(false);

  return (
    <div className="min-h-[calc(100vh-5rem)] bg-slate-50/60 p-6 lg:p-8">
      <div className="mx-auto max-w-7xl space-y-6">
        {/* 顶部操作区 */}
        <div className="flex flex-wrap items-center justify-between gap-4 rounded-3xl border border-white/70 bg-white/80 p-6 shadow-xl backdrop-blur-xl">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-600 text-white shadow-lg shadow-indigo-600/20">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-bold text-slate-900">历史知识消歧与人机协同审核工作台</h1>
                <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-semibold text-indigo-700">
                  知识修正闭环
                </span>
              </div>
              <p className="text-xs text-slate-500">
                对档案自动提取的人物消歧建议、多维时空相容性评分及实体关系进行人工确认、驳回与原子化合并
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setIsMergeModalOpen(true)}
              className="flex items-center gap-1.5 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-700 shadow-xs hover:border-slate-300 hover:bg-slate-50 transition-all active:scale-95"
            >
              <GitMerge className="h-4 w-4 text-indigo-600" />
              <span>实体合并工具</span>
            </button>

            <button
              type="button"
              onClick={() => setIsAuditDrawerOpen(true)}
              className="flex items-center gap-1.5 rounded-2xl bg-slate-900 px-4 py-2 text-xs font-semibold text-white shadow-md hover:bg-slate-800 transition-all active:scale-95"
            >
              <History className="h-4 w-4" />
              <span>操作审计日志 (可逆回滚)</span>
            </button>
          </div>
        </div>

        {/* 核心审核队列与打分交互面板 */}
        <MentionReviewQueue />
      </div>

      {/* 实体合并模态框 */}
      <EntityMergeModal
        isOpen={isMergeModalOpen}
        onClose={() => setIsMergeModalOpen(false)}
      />

      {/* 审计日志抽屉 (支持一键拆分与可逆恢复) */}
      <AuditHistoryDrawer
        isOpen={isAuditDrawerOpen}
        onClose={() => setIsAuditDrawerOpen(false)}
      />
    </div>
  );
}
