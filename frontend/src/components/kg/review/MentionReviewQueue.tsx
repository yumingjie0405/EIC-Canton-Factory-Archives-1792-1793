"use client";

import React, { useEffect, useState } from "react";
import { UserCheck, Clock, AlertCircle, RefreshCw, ChevronRight, CheckCircle2, XCircle, PauseCircle } from "lucide-react";
import { PersonMention, MentionDisambiguationEvaluation } from "@/types/kg";
import { kgApi } from "@/lib/kgApi";
import { CandidateScoreCard } from "./CandidateScoreCard";

interface MentionReviewQueueProps {
  onReviewed?: () => void;
}

export function MentionReviewQueue({ onReviewed }: MentionReviewQueueProps) {
  const [mentions, setMentions] = useState<PersonMention[]>([]);
  const [selectedMention, setSelectedMention] = useState<PersonMention | null>(null);
  const [evaluation, setEvaluation] = useState<MentionDisambiguationEvaluation | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("candidate");
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [isLoadingEval, setIsLoadingEval] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [reviewerName, setReviewerName] = useState("Dr. Historian");
  const [reason, setReason] = useState("");
  const [feedbackMsg, setFeedbackMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const loadMentions = async () => {
    setIsLoadingList(true);
    setFeedbackMsg(null);
    try {
      const res = await kgApi.listMentions({
        resolution_status: statusFilter === "all" ? undefined : statusFilter,
        limit: 50,
      });
      setMentions(res.items || []);
      if (res.items.length > 0 && (!selectedMention || !res.items.find((m) => m.id === selectedMention.id))) {
        selectMentionItem(res.items[0]);
      } else if (res.items.length === 0) {
        setSelectedMention(null);
        setEvaluation(null);
      }
    } catch (err: any) {
      setFeedbackMsg({ type: "error", text: err.message || "拉取待审核列表失败" });
    } finally {
      setIsLoadingList(false);
    }
  };

  useEffect(() => {
    loadMentions();
  }, [statusFilter]);

  const selectMentionItem = async (mention: PersonMention) => {
    setSelectedMention(mention);
    setIsLoadingEval(true);
    try {
      const evalData = await kgApi.getMentionCandidates(mention.id);
      setEvaluation(evalData);
    } catch (err: any) {
      setEvaluation(null);
    } finally {
      setIsLoadingEval(false);
    }
  };

  const handleConfirm = async (personId: number) => {
    if (!selectedMention) return;
    setIsSubmitting(true);
    try {
      await kgApi.confirmMentionResolution(selectedMention.id, {
        person_id: personId,
        reviewer: reviewerName,
        reason: reason || "人工审核确认实体绑定",
      });
      setFeedbackMsg({ type: "success", text: `已成功将提及 “${selectedMention.raw_text}” 绑定至人物 #${personId}` });
      setReason("");
      loadMentions();
      onReviewed && onReviewed();
    } catch (err: any) {
      setFeedbackMsg({ type: "error", text: err.message || "确认绑定失败" });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReject = async (personId: number) => {
    if (!selectedMention) return;
    setIsSubmitting(true);
    try {
      await kgApi.rejectMentionCandidate(selectedMention.id, {
        person_id: personId,
        reviewer: reviewerName,
        reason: reason || "人工审核排除该候选匹配",
      });
      setFeedbackMsg({ type: "success", text: `已排除候选人物 #${personId}` });
      // 刷新当前提及的候选
      selectMentionItem(selectedMention);
      onReviewed && onReviewed();
    } catch (err: any) {
      setFeedbackMsg({ type: "error", text: err.message || "排除候选失败" });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDefer = async () => {
    if (!selectedMention) return;
    setIsSubmitting(true);
    try {
      await kgApi.deferMentionResolution(selectedMention.id, {
        reviewer: reviewerName,
        reason: reason || "证据不足，暂缓审校",
      });
      setFeedbackMsg({ type: "success", text: `已将提及 “${selectedMention.raw_text}” 标记为暂缓审校` });
      setReason("");
      loadMentions();
      onReviewed && onReviewed();
    } catch (err: any) {
      setFeedbackMsg({ type: "error", text: err.message || "暂缓操作失败" });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      {/* 左侧：待审提及任务队列 */}
      <div className="flex flex-col gap-3 lg:col-span-4 rounded-3xl border border-white/70 bg-white/80 p-4 shadow-xl backdrop-blur-xl">
        <div className="flex items-center justify-between border-b border-slate-100 pb-3">
          <div className="flex items-center gap-2">
            <UserCheck className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-bold text-slate-900">待审消歧提及队列</h3>
          </div>
          <button
            type="button"
            onClick={loadMentions}
            title="刷新列表"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${isLoadingList ? "animate-spin" : ""}`} />
          </button>
        </div>

        {/* 状态筛选胶囊 */}
        <div className="flex flex-wrap gap-1.5 text-xs">
          {[
            { id: "candidate", label: "待审候选" },
            { id: "unresolved", label: "未关联" },
            { id: "uncertain", label: "存疑/暂缓" },
            { id: "all", label: "全部" },
          ].map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setStatusFilter(item.id)}
              className={`rounded-full px-2.5 py-1 text-xs font-medium transition-all ${
                statusFilter === item.id
                  ? "bg-slate-900 text-white shadow-xs"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        {/* 列表条目 */}
        <div className="flex flex-col gap-2 max-h-[620px] overflow-y-auto pr-1">
          {isLoadingList && mentions.length === 0 ? (
            <div className="py-12 text-center text-xs text-slate-400">加载队列中...</div>
          ) : mentions.length === 0 ? (
            <div className="py-12 text-center text-xs text-slate-400">
              当前分类下暂无待审核提及
            </div>
          ) : (
            mentions.map((m) => {
              const isSelected = selectedMention?.id === m.id;
              return (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => selectMentionItem(m)}
                  className={`flex flex-col gap-1 rounded-2xl border p-3 text-left transition-all ${
                    isSelected
                      ? "border-primary bg-primary/5 shadow-sm"
                      : "border-slate-100 bg-white hover:border-slate-200"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-900">{m.raw_text}</span>
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">
                      Block #{m.block_id}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-slate-500">
                    <span>年份: {m.temporal_year || "未注明"}</span>
                    <span className="capitalize">{m.resolution_status}</span>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </div>

      {/* 右侧：多维打分详情与审核操作面板 */}
      <div className="flex flex-col gap-4 lg:col-span-8 rounded-3xl border border-white/70 bg-white/80 p-5 shadow-xl backdrop-blur-xl">
        {selectedMention ? (
          <>
            {/* 当前选中提及卡片 */}
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-slate-400">待审档案人名提及:</span>
                  <span className="text-base font-bold text-slate-900 font-serif">
                    “{selectedMention.raw_text}”
                  </span>
                  {selectedMention.normalized_text && (
                    <span className="text-xs text-slate-500">
                      (规范形式: {selectedMention.normalized_text})
                    </span>
                  )}
                </div>
                <div className="mt-1 flex flex-wrap gap-3 text-xs text-slate-500">
                  <span>来源文本块: <code>Block #{selectedMention.block_id}</code></span>
                  <span>文献推定年份: <strong>{selectedMention.temporal_year || "未知"}</strong></span>
                  <span>地点: <strong>{selectedMention.spatial_location || "广州 (Canton)"}</strong></span>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={handleDefer}
                  className="flex items-center gap-1 rounded-xl border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
                >
                  <PauseCircle className="h-3.5 w-3.5" />
                  <span>证据存疑暂缓</span>
                </button>
              </div>
            </div>

            {/* 反馈消息 */}
            {feedbackMsg && (
              <div
                className={`flex items-center gap-2 rounded-xl p-3 text-xs font-medium ${
                  feedbackMsg.type === "success"
                    ? "bg-emerald-50 text-emerald-800 border border-emerald-200"
                    : "bg-rose-50 text-rose-800 border border-rose-200"
                }`}
              >
                {feedbackMsg.type === "success" ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                ) : (
                  <AlertCircle className="h-4 w-4 text-rose-600" />
                )}
                <span>{feedbackMsg.text}</span>
              </div>
            )}

            {/* 审核输入栏 */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="text-[11px] font-medium text-slate-500">审校员姓名</label>
                <input
                  type="text"
                  value={reviewerName}
                  onChange={(e) => setReviewerName(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-800 outline-none focus:border-primary"
                />
              </div>
              <div>
                <label className="text-[11px] font-medium text-slate-500">考证备注 / 绑定理由</label>
                <input
                  type="text"
                  placeholder="例如：档案中提及保商担保事宜，与同文行潘氏时空高度吻合"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-800 outline-none focus:border-primary"
                />
              </div>
            </div>

            {/* 候选列表区 */}
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between border-t border-slate-100 pt-3">
                <span className="text-xs font-bold text-slate-800">
                  候选历史人物及多维打分对比 ({evaluation?.candidates?.length || 0})
                </span>
                <span className="text-[11px] text-slate-400">
                  按名称(40%) + 时空(20%) + 身份(15%) + 上下文(10%) + 权威(5%) 加权
                </span>
              </div>

              {isLoadingEval ? (
                <div className="py-12 text-center text-xs text-slate-400">
                  正在进行多维可解释性时空打分与冲突检测...
                </div>
              ) : !evaluation || evaluation.candidates.length === 0 ? (
                <div className="rounded-2xl border border-slate-100 bg-slate-50/50 p-8 text-center text-xs text-slate-400">
                  暂无匹配的候选人物。可在下方创建新实体或暂缓审校。
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  {evaluation.candidates.map((cand) => (
                    <CandidateScoreCard
                      key={cand.person_id}
                      candidate={cand}
                      onConfirm={handleConfirm}
                      onReject={handleReject}
                      isSubmitting={isSubmitting}
                    />
                  ))}
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="flex h-96 flex-col items-center justify-center text-center text-xs text-slate-400">
            请从左侧选择一个待审提及条目
          </div>
        )}
      </div>
    </div>
  );
}
