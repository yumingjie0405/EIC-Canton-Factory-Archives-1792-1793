"use client";

import React from "react";
import { AlertTriangle, Cpu, ShieldAlert } from "lucide-react";
import { AnswerGeneration } from "@/types/rag";

/**
 * Surfaces where an answer text actually came from.
 *
 * A degraded answer (model service down, wrong/expired API key, timeout) must
 * never look like a normal model answer, and an answer citing evidence numbers
 * that were never retrieved must be visibly flagged.
 */
interface GenerationStatusBannerProps {
  generation?: AnswerGeneration | null;
  className?: string;
}

const REASON_LABELS: Record<string, string> = {
  authentication_failed: "模型鉴权失败（401/403）",
  permission_denied: "模型访问被拒绝（403）",
  timeout: "模型响应超时",
  connection_failed: "无法连接模型服务",
  rate_limited: "模型服务限流（429）",
  not_configured: "未配置模型凭据",
  empty_response: "模型返回空内容",
  server_error: "模型服务端错误（5xx）",
  client_error: "模型请求被拒绝（4xx）",
  unexpected_error: "模型调用异常",
};

export function GenerationStatusBanner({ generation, className }: GenerationStatusBannerProps) {
  if (!generation) return null;

  const citationCheck = generation.citation_check;
  const citationFailed = !!citationCheck && citationCheck.valid === false;
  const degraded = !!generation.degraded;
  const reasonLabel = generation.failure_reason
    ? REASON_LABELS[generation.failure_reason] || generation.failure_reason
    : null;

  if (!degraded && !citationFailed) {
    if (generation.mode === "model") {
      return (
        <div
          className={`flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500 ${className || ""}`}
        >
          <Cpu className="w-3.5 h-3.5" />
          <span>
            回答由模型 <strong className="text-slate-400">{generation.model_name || "未标注"}</strong>{" "}
            生成
          </span>
        </div>
      );
    }
    return null;
  }

  return (
    <div className={`space-y-2 ${className || ""}`}>
      {citationFailed && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-200 text-xs">
          <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5" />
          <div className="space-y-0.5">
            <p className="font-semibold">引用校验未通过：答案不可直接采信</p>
            <p className="text-rose-200/80 leading-relaxed">
              模型引用了不存在的证据编号（
              {citationCheck?.invalid_ids?.map((id) => `证据 #${id}`).join("、") || "未知"}
              ），本次检索仅有 {citationCheck?.available_count ?? 0} 条证据。请以下方证据卡片为准。
            </p>
          </div>
        </div>
      )}

      {degraded && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <div className="space-y-0.5">
            <p className="font-semibold">
              降级结果：模型服务不可用{reasonLabel ? `（${reasonLabel}）` : ""}
            </p>
            <p className="text-amber-200/80 leading-relaxed">
              {generation.failure_message ||
                "当前回答由离线确定性合成生成，未经大语言模型综述，请自行核对原始出处。"}
              {generation.failure_detail ? ` 诊断信息：${generation.failure_detail}` : ""}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default GenerationStatusBanner;
