"use client";

import React from "react";
import { Award, Info, Activity, Share2, Compass, Layers } from "lucide-react";
import { SNAPersonMetrics } from "@/types/kg";

interface SNAMetricsCardProps {
  metrics?: SNAPersonMetrics | null;
  isLoading?: boolean;
}

export function SNAMetricsCard({ metrics, isLoading }: SNAMetricsCardProps) {
  if (isLoading) {
    return (
      <div className="flex h-36 items-center justify-center rounded-2xl border border-slate-100 bg-slate-50/50 p-4">
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          <span>正在计算人物 SNA 拓扑指标...</span>
        </div>
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="rounded-2xl border border-slate-100 bg-slate-50/50 p-4 text-xs text-slate-500 text-center">
        暂无该人物在当前网络参数下的拓扑指标
      </div>
    );
  }

  if (!metrics.in_network) {
    return (
      <div className="rounded-2xl border border-amber-200/60 bg-amber-50/50 p-4 text-xs text-amber-800">
        <div className="flex items-center gap-1.5 font-semibold">
          <Info className="h-4 w-4 text-amber-600" />
          <span>未纳入当前拓扑分析网络</span>
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-amber-700">
          {metrics.note || "该人物在当前筛选参数（如审核状态或孤立节点过滤）下暂未进入正式分析网络。"}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-slate-200/70 bg-white/70 p-4 shadow-sm backdrop-blur-md">
      <div className="flex items-center justify-between border-b border-slate-100 pb-2">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-primary" />
          <span className="text-xs font-semibold text-slate-800">社会网络分析 (SNA) 指标</span>
        </div>
        {metrics.community_id && (
          <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-medium text-indigo-600">
            {metrics.community_id}
          </span>
        )}
      </div>

      {/* 四大核心中心度网格 */}
      <div className="grid grid-cols-2 gap-2 text-xs">
        {/* 度中心性 */}
        <div className="flex flex-col gap-1 rounded-xl bg-slate-50/80 p-2.5">
          <div className="flex items-center justify-between text-slate-500">
            <span className="flex items-center gap-1">
              <Share2 className="h-3 w-3" /> 度中心性
            </span>
            {metrics.rank_degree && (
              <span className="font-semibold text-primary">#{metrics.rank_degree}</span>
            )}
          </div>
          <div className="text-sm font-semibold text-slate-900">
            {metrics.degree_centrality.toFixed(4)}
          </div>
          <div className="text-[10px] text-slate-400">
            入度: {metrics.in_degree} · 出度: {metrics.out_degree}
          </div>
        </div>

        {/* 介数中心性 */}
        <div className="flex flex-col gap-1 rounded-xl bg-slate-50/80 p-2.5">
          <div className="flex items-center justify-between text-slate-500">
            <span className="flex items-center gap-1">
              <Compass className="h-3 w-3" /> 介数中介
            </span>
            {metrics.rank_betweenness && (
              <span className="font-semibold text-primary">#{metrics.rank_betweenness}</span>
            )}
          </div>
          <div className="text-sm font-semibold text-slate-900">
            {metrics.betweenness_centrality.toFixed(4)}
          </div>
          <div className="text-[10px] text-slate-400">Brandes 算法跨群中介力</div>
        </div>

        {/* 接近中心性 */}
        <div className="flex flex-col gap-1 rounded-xl bg-slate-50/80 p-2.5">
          <div className="flex items-center justify-between text-slate-500">
            <span className="flex items-center gap-1">
              <Layers className="h-3 w-3" /> 接近中心度
            </span>
            {metrics.rank_closeness && (
              <span className="font-semibold text-primary">#{metrics.rank_closeness}</span>
            )}
          </div>
          <div className="text-sm font-semibold text-slate-900">
            {metrics.closeness_centrality.toFixed(4)}
          </div>
          <div className="text-[10px] text-slate-400">全网可达平均路径效率</div>
        </div>

        {/* PageRank */}
        <div className="flex flex-col gap-1 rounded-xl bg-slate-50/80 p-2.5">
          <div className="flex items-center justify-between text-slate-500">
            <span className="flex items-center gap-1">
              <Award className="h-3 w-3 text-amber-500" /> PageRank
            </span>
            {metrics.rank_pagerank && (
              <span className="font-semibold text-amber-600">#{metrics.rank_pagerank}</span>
            )}
          </div>
          <div className="text-sm font-semibold text-slate-900">
            {metrics.pagerank.toFixed(5)}
          </div>
          <div className="text-[10px] text-slate-400">阻尼系数 0.85 递归权重</div>
        </div>
      </div>

      {/* 史学认识论声明条 */}
      <div className="flex items-start gap-1.5 rounded-xl bg-slate-100/70 p-2 text-[10px] leading-relaxed text-slate-500">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
        <span>
          <strong>史学认识论说明</strong>：本网络指标反映 1792–1793 年现存英国东印度公司广州商馆档案的文献留存与抽取记录，代表特定档案视野下的文本接触与共现结构，并非历史人物实际全面社会影响力的直接因果测量。
        </span>
      </div>
    </div>
  );
}
