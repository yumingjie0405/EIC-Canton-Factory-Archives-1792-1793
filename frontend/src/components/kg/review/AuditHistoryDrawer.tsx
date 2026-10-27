"use client";

import React, { useEffect, useState } from "react";
import { X, History, RotateCcw, CheckCircle, AlertCircle, RefreshCw, FileText } from "lucide-react";
import { AuditRecord } from "@/types/kg";
import { kgApi } from "@/lib/kgApi";

interface AuditHistoryDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  onReverted?: () => void;
}

export function AuditHistoryDrawer({ isOpen, onClose, onReverted }: AuditHistoryDrawerProps) {
  const [logs, setLogs] = useState<AuditRecord[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [revertingId, setRevertingId] = useState<number | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const loadLogs = async () => {
    setIsLoading(true);
    setFeedback(null);
    try {
      const res = await kgApi.listAuditLogs(50);
      setLogs(res || []);
    } catch (err: any) {
      setFeedback("拉取审计日志失败");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      loadLogs();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleRollback = async (logId: number) => {
    setRevertingId(logId);
    setFeedback(null);
    try {
      await kgApi.splitMerge({
        audit_id: logId,
        operator: "Dr. Historian",
        reason: "人工审核审计回滚，重新拆分还原实体",
      });
      setFeedback(`审计记录 #${logId} 已成功回滚，被合并的实体已 100% 无损还原！`);
      loadLogs();
      onReverted && onReverted();
    } catch (err: any) {
      setFeedback(err.message || "回滚拆分失败");
    } finally {
      setRevertingId(null);
    }
  };

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-white/60 bg-white/95 shadow-2xl backdrop-blur-2xl animate-in slide-in-from-right duration-200">
      <div className="flex items-center justify-between border-b border-slate-100 p-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-100 text-slate-700">
            <History className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-sm font-bold text-slate-900">知识图谱操作审计历史</h3>
            <p className="text-xs text-slate-500">实体合并、拆分与审校操作全量可溯源日志</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={loadLogs}
            className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <RefreshCw className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`} />
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      {feedback && (
        <div className="m-4 flex items-center gap-2 rounded-xl bg-slate-100 p-3 text-xs text-slate-800 border border-slate-200">
          <AlertCircle className="h-4 w-4 text-primary shrink-0" />
          <span>{feedback}</span>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {isLoading && logs.length === 0 ? (
          <div className="py-16 text-center text-xs text-slate-400">加载审计历史中...</div>
        ) : logs.length === 0 ? (
          <div className="py-16 text-center text-xs text-slate-400">暂无操作审计日志</div>
        ) : (
          logs.map((log) => {
            const isMerge = log.operation_type === "merge";
            const canRevert = isMerge && !log.is_reverted;

            return (
              <div
                key={log.id}
                className="flex flex-col gap-2 rounded-2xl border border-slate-100 bg-slate-50/70 p-3.5 text-xs transition-all hover:bg-slate-50"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${
                        isMerge
                          ? "bg-indigo-100 text-indigo-700"
                          : log.operation_type === "split"
                          ? "bg-purple-100 text-purple-700"
                          : "bg-slate-200 text-slate-700"
                      }`}
                    >
                      {log.operation_type}
                    </span>
                    <span className="text-slate-400">#{log.id}</span>
                  </div>
                  <span className="text-[10px] text-slate-400">
                    {new Date(log.created_at).toLocaleString()}
                  </span>
                </div>

                <div className="text-slate-700">
                  主实体: <strong>Person #{log.primary_person_id}</strong>
                  {log.secondary_person_id && (
                    <span>
                      {" "}
                      · 次实体: <strong>Person #{log.secondary_person_id}</strong>
                    </span>
                  )}
                </div>

                {log.reason && (
                  <p className="text-[11px] text-slate-500 italic font-serif">
                    理由: {log.reason}
                  </p>
                )}

                <div className="flex items-center justify-between pt-1 border-t border-slate-200/50 text-[10px] text-slate-400">
                  <span>操作人: {log.operator || "系统"}</span>
                  {log.is_reverted ? (
                    <span className="text-amber-600 font-medium">已被拆分回滚</span>
                  ) : canRevert ? (
                    <button
                      type="button"
                      disabled={revertingId === log.id}
                      onClick={() => handleRollback(log.id)}
                      className="inline-flex items-center gap-1 rounded-lg bg-white border border-rose-200 px-2 py-1 font-semibold text-rose-600 shadow-xs hover:bg-rose-50 disabled:opacity-50"
                    >
                      <RotateCcw className="h-3 w-3" />
                      <span>{revertingId === log.id ? "回滚中..." : "一键可逆拆分还原"}</span>
                    </button>
                  ) : (
                    <span className="text-emerald-600">正常有效</span>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
