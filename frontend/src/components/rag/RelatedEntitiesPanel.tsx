"use client";

import React from "react";
import Link from "next/link";
import { Users, Network, Award, ArrowUpRight, Share2 } from "lucide-react";
import { RagEntityReference, RagRelationshipReference } from "@/types/rag";

interface RelatedEntitiesPanelProps {
  entities: RagEntityReference[];
  relationships: RagRelationshipReference[];
}

export const RelatedEntitiesPanel: React.FC<RelatedEntitiesPanelProps> = ({
  entities,
  relationships,
}) => {
  if ((!entities || entities.length === 0) && (!relationships || relationships.length === 0)) {
    return null;
  }

  return (
    <div className="space-y-4">
      {/* 涉及人物实体列表 */}
      {entities && entities.length > 0 && (
        <div className="bg-slate-900/70 border border-slate-800 rounded-2xl p-5 shadow-lg">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <Users className="w-4 h-4 text-indigo-400" />
              <span>知识图谱关联实体档案</span>
            </h3>
            <span className="text-xs text-slate-500 font-mono">{entities.length} 位人物</span>
          </div>

          <div className="space-y-3">
            {entities.map((ent) => (
              <div
                key={ent.id}
                className="p-3.5 rounded-xl bg-slate-950/50 border border-slate-800/80 hover:border-slate-700 transition-all"
              >
                <div className="flex items-start justify-between gap-2 mb-1.5">
                  <div>
                    <h4 className="text-sm font-medium text-slate-100 flex items-center gap-1.5">
                      <span>{ent.display_name}</span>
                      {ent.canonical_name_zh && ent.canonical_name_zh !== ent.display_name && (
                        <span className="text-xs text-slate-400 font-normal">({ent.canonical_name_zh})</span>
                      )}
                    </h4>
                    <p className="text-xs text-slate-400">
                      {ent.primary_role || "历史人物"}
                      {ent.organization && ` · ${ent.organization}`}
                    </p>
                  </div>

                  <Link
                    href={`/kg/graph?person_id=${ent.id}`}
                    target="_blank"
                    className="flex items-center gap-1 text-[11px] text-indigo-400 hover:text-indigo-300 px-2 py-1 rounded bg-indigo-500/10 hover:bg-indigo-500/20 border border-indigo-500/20 transition-all"
                  >
                    <span>图谱定位</span>
                    <ArrowUpRight className="w-3 h-3" />
                  </Link>
                </div>

                {/* 外部学术权威考证记录 */}
                {ent.external_records && ent.external_records.length > 0 && (
                  <div className="mt-2.5 pt-2 border-t border-slate-800/60">
                    <div className="flex items-center gap-1 text-[10px] text-slate-500 mb-1">
                      <Award className="w-3 h-3 text-amber-500/80" />
                      <span>学术规范考证依据 ({ent.external_authorities_count} 项):</span>
                    </div>
                    <div className="space-y-1">
                      {ent.external_records.slice(0, 2).map((rec, rIdx) => (
                        <div
                          key={rIdx}
                          className="text-[11px] text-slate-400 bg-slate-900 px-2 py-1 rounded border border-slate-800 font-mono truncate"
                          title={rec}
                        >
                          {rec}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 社会关系与商业往来事实 */}
      {relationships && relationships.length > 0 && (
        <div className="bg-slate-900/70 border border-slate-800 rounded-2xl p-5 shadow-lg">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <Share2 className="w-4 h-4 text-emerald-400" />
              <span>知识图谱事实关系事实</span>
            </h3>
            <span className="text-xs text-slate-500 font-mono">{relationships.length} 条</span>
          </div>

          <div className="space-y-2.5">
            {relationships.map((rel, rIdx) => (
              <div
                key={rIdx}
                className="p-3 rounded-xl bg-slate-950/50 border border-slate-800/80 text-xs"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <div className="flex items-center gap-1.5 font-medium text-slate-200">
                    <span className="text-indigo-300">{rel.source_person_name}</span>
                    <span className="text-slate-500">⇄</span>
                    <span className="text-indigo-300">{rel.target_person_name}</span>
                  </div>

                  <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px] font-mono">
                    {rel.relation_type}
                  </span>
                </div>

                <div className="flex items-center justify-between text-[11px] text-slate-400">
                  <span>置信度: {(rel.confidence * 100).toFixed(0)}%</span>
                  {rel.time_description && <span>年份: {rel.time_description}</span>}
                  <span className="text-slate-500">状态: {rel.review_status}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
