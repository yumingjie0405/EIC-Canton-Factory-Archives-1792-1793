"use client";

import React, { useState } from "react";
import { ArrowUpRight, ArrowDownLeft, ArrowLeftRight, FileText, Search, PlusCircle } from "lucide-react";
import { SNAPersonNeighborItem } from "@/types/kg";
import { EvidenceViewerModal } from "./EvidenceViewerModal";

interface RelationshipListProps {
  personName: string;
  neighbors: SNAPersonNeighborItem[];
  onSelectNeighbor?: (personId: number) => void;
  onExpandNeighbor?: (personId: number) => void;
  isLoading?: boolean;
}

export function RelationshipList({
  personName,
  neighbors,
  onSelectNeighbor,
  onExpandNeighbor,
  isLoading,
}: RelationshipListProps) {
  const [activeModalItem, setActiveModalItem] = useState<SNAPersonNeighborItem | null>(null);

  if (isLoading) {
    return (
      <div className="flex h-32 items-center justify-center text-xs text-slate-400">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent mr-2" />
        加载人物关系网络...
      </div>
    );
  }

  if (neighbors.length === 0) {
    return (
      <div className="rounded-xl border border-slate-100 bg-slate-50/50 p-4 text-center text-xs text-slate-400">
        当前筛选条件下暂无相连的历史关系
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between text-xs font-semibold text-slate-700">
        <span>关联人物与关系列表 ({neighbors.length})</span>
      </div>

      <div className="flex flex-col gap-2">
        {neighbors.map((nbr) => {
          const isMutual = nbr.direction === "mutual";
          const isOut = nbr.direction === "out";

          return (
            <div
              key={nbr.person_id}
              className="flex flex-col gap-1.5 rounded-xl border border-slate-100 bg-white p-3 shadow-xs transition-all hover:border-slate-200 hover:shadow-sm"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span
                    className={`flex h-6 w-6 items-center justify-center rounded-lg text-xs ${
                      isMutual
                        ? "bg-purple-50 text-purple-600"
                        : isOut
                        ? "bg-indigo-50 text-indigo-600"
                        : "bg-emerald-50 text-emerald-600"
                    }`}
                    title={isMutual ? "双向往来" : isOut ? "指向对方 (出边)" : "对方指向 (入边)"}
                  >
                    {isMutual ? (
                      <ArrowLeftRight className="h-3.5 w-3.5" />
                    ) : isOut ? (
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    ) : (
                      <ArrowDownLeft className="h-3.5 w-3.5" />
                    )}
                  </span>
                  <div>
                    <button
                      type="button"
                      onClick={() => onSelectNeighbor && onSelectNeighbor(nbr.person_id)}
                      className="text-xs font-semibold text-slate-800 hover:text-primary hover:underline"
                    >
                      {nbr.display_name}
                    </button>
                    {nbr.primary_role && (
                      <span className="ml-1.5 text-[10px] text-slate-400">({nbr.primary_role})</span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-1.5">
                  {onExpandNeighbor && (
                    <button
                      type="button"
                      onClick={() => onExpandNeighbor(nbr.person_id)}
                      title="展开该人物的局部网络"
                      className="flex h-6 w-6 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-primary"
                    >
                      <PlusCircle className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-1.5 pt-1 text-[11px] text-slate-500">
                <div className="flex flex-wrap gap-1">
                  {nbr.relation_types.map((t) => (
                    <span key={t} className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">
                      {t}
                    </span>
                  ))}
                </div>

                <button
                  type="button"
                  onClick={() => setActiveModalItem(nbr)}
                  className="flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
                >
                  <FileText className="h-3 w-3" />
                  <span>{nbr.evidence_count} 条档案证据</span>
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {activeModalItem && (
        <EvidenceViewerModal
          isOpen={true}
          onClose={() => setActiveModalItem(null)}
          personName={personName}
          neighborName={activeModalItem.display_name}
          relationType={activeModalItem.relation_types.join(", ")}
          evidences={activeModalItem.evidences}
        />
      )}
    </div>
  );
}
