"use client";

import React, { useEffect, useState } from "react";
import { X, User, BookOpen, ExternalLink, ShieldCheck, MapPin, Calendar, Building, Sparkles } from "lucide-react";
import { PersonDetail, SNAPersonMetrics, SNAPersonNeighborItem, SNAGraphFilterParams } from "@/types/kg";
import { kgApi } from "@/lib/kgApi";
import { SNAMetricsCard } from "./SNAMetricsCard";
import { RelationshipList } from "./RelationshipList";

interface PersonDetailDrawerProps {
  personId: number | null;
  filters: SNAGraphFilterParams;
  onClose: () => void;
  onSelectNeighbor?: (personId: number) => void;
  onExpandNeighbor?: (personId: number) => void;
}

export function PersonDetailDrawer({
  personId,
  filters,
  onClose,
  onSelectNeighbor,
  onExpandNeighbor,
}: PersonDetailDrawerProps) {
  const [person, setPerson] = useState<PersonDetail | null>(null);
  const [metrics, setMetrics] = useState<SNAPersonMetrics | null>(null);
  const [neighbors, setNeighbors] = useState<SNAPersonNeighborItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"profile" | "network">("profile");

  useEffect(() => {
    if (!personId) {
      setPerson(null);
      setMetrics(null);
      setNeighbors([]);
      return;
    }

    let isSubscribed = true;
    setIsLoading(true);

    Promise.all([
      kgApi.getPersonDetail(personId).catch(() => null),
      kgApi.getPersonMetrics(personId, filters).catch(() => null),
      kgApi.getPersonNeighbors(personId, filters).catch(() => ({ neighbors: [] })),
    ]).then(([personData, metricsData, neighborsData]) => {
      if (!isSubscribed) return;
      setPerson(personData);
      setMetrics(metricsData);
      setNeighbors(neighborsData?.neighbors || []);
      setIsLoading(false);
    });

    return () => {
      isSubscribed = false;
    };
  }, [personId, filters]);

  if (!personId) return null;

  return (
    <div className="fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col border-l border-white/60 bg-white/90 shadow-2xl backdrop-blur-2xl animate-in slide-in-from-right duration-200">
      {/* 顶部标题栏 */}
      <div className="flex items-center justify-between border-b border-slate-100 p-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary text-white shadow-md shadow-primary/20">
            <User className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-base font-bold text-slate-900">
              {person?.display_name || `人物 #${personId}`}
            </h2>
            <div className="flex items-center gap-2 text-xs text-slate-500">
              {person?.canonical_name_zh && <span>{person.canonical_name_zh}</span>}
              {person?.canonical_name_en && <span>({person.canonical_name_en})</span>}
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* 标签切换栏 */}
      <div className="flex border-b border-slate-100 bg-slate-50/50 p-1 text-xs">
        <button
          type="button"
          onClick={() => setActiveTab("profile")}
          className={`flex-1 py-1.5 font-medium rounded-xl transition-all ${
            activeTab === "profile" ? "bg-white text-slate-900 shadow-xs" : "text-slate-500 hover:text-slate-700"
          }`}
        >
          人物档案与生平
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("network")}
          className={`flex-1 py-1.5 font-medium rounded-xl transition-all ${
            activeTab === "network" ? "bg-white text-slate-900 shadow-xs" : "text-slate-500 hover:text-slate-700"
          }`}
        >
          SNA拓扑与关系 ({neighbors.length})
        </button>
      </div>

      {/* 抽屉内容区 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {isLoading && !person ? (
          <div className="flex h-48 items-center justify-center text-xs text-slate-400">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent mr-2" />
            加载人物档案中...
          </div>
        ) : (
          <>
            {activeTab === "profile" && (
              <div className="space-y-4">
                {/* 身份与状态标签 */}
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="rounded-full bg-slate-900 px-2.5 py-0.5 text-[10px] font-semibold text-white">
                    {person?.primary_role || "历史人物"}
                  </span>
                  <span
                    className={`rounded-full px-2.5 py-0.5 text-[10px] font-semibold ${
                      person?.identity_status === "accepted"
                        ? "bg-emerald-50 text-emerald-700"
                        : "bg-amber-50 text-amber-700"
                    }`}
                  >
                    {person?.identity_status === "accepted" ? "已证实实体" : "待定实体"}
                  </span>
                  {person?.organization && (
                    <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-[10px] font-medium text-indigo-700">
                      {person.organization}
                    </span>
                  )}
                </div>

                {/* 关键历史信息列表 */}
                <div className="flex flex-col gap-2 rounded-2xl border border-slate-100 bg-slate-50/70 p-3.5 text-xs text-slate-600">
                  <div className="flex items-center gap-2">
                    <Calendar className="h-3.5 w-3.5 text-slate-400" />
                    <span>
                      生卒年份:{" "}
                      <strong className="text-slate-800">
                        {person?.birth_year || "?"} – {person?.death_year || "?"}
                      </strong>
                    </span>
                  </div>
                  {person?.base_location && (
                    <div className="flex items-center gap-2">
                      <MapPin className="h-3.5 w-3.5 text-slate-400" />
                      <span>
                        常驻地: <strong className="text-slate-800">{person.base_location}</strong>
                      </span>
                    </div>
                  )}
                  {person?.organization && (
                    <div className="flex items-center gap-2">
                      <Building className="h-3.5 w-3.5 text-slate-400" />
                      <span>
                        所属机构/行商: <strong className="text-slate-800">{person.organization}</strong>
                      </span>
                    </div>
                  )}
                </div>

                {/* 别名与拼写变体 */}
                {person?.aliases && (
                  <div className="flex flex-col gap-1.5 rounded-2xl border border-slate-100 bg-white p-3 text-xs">
                    <span className="font-semibold text-slate-700">档案别名与拼写变体:</span>
                    <div className="flex flex-wrap gap-1">
                      {(Array.isArray(person.aliases)
                        ? person.aliases
                        : Object.keys(person.aliases)
                      ).map((alias: string, i: number) => (
                        <span
                          key={i}
                          className="rounded-lg bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600"
                        >
                          {alias}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* 传记考证笔记 */}
                {person?.biographical_notes && (
                  <div className="flex flex-col gap-1.5 rounded-2xl border border-slate-100 bg-white p-3 text-xs leading-relaxed text-slate-600">
                    <span className="font-semibold text-slate-700">史学考证笔记:</span>
                    <p className="font-serif text-[11px] text-slate-600">{person.biographical_notes}</p>
                  </div>
                )}

                {/* 外部权威库挂接记录 (CBDB / 梁嘉彬考证等) */}
                <div className="flex flex-col gap-2 rounded-2xl border border-slate-100 bg-white p-3 text-xs">
                  <div className="flex items-center gap-1.5 font-semibold text-slate-800">
                    <BookOpen className="h-3.5 w-3.5 text-primary" />
                    <span>外部权威库关联 (External Authorities)</span>
                  </div>

                  {(!person?.external_authorities || person.external_authorities.length === 0) ? (
                    <p className="text-[11px] text-slate-400 italic">暂无外部权威库挂接记录</p>
                  ) : (
                    person.external_authorities.map((auth) => (
                      <div
                        key={auth.id}
                        className="flex flex-col gap-1 rounded-xl bg-slate-50 p-2.5 text-[11px] border border-slate-100"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-slate-700">
                            {auth.authority_source}
                          </span>
                          {auth.external_id && (
                            <span className="rounded bg-slate-200/70 px-1.5 py-0.2 text-[10px] text-slate-600">
                              ID: {auth.external_id}
                            </span>
                          )}
                        </div>
                        {auth.record_title && (
                          <div className="text-slate-600">{auth.record_title}</div>
                        )}
                        {auth.historical_notes && (
                          <p className="text-slate-500 italic font-serif">{auth.historical_notes}</p>
                        )}
                        {auth.external_url && (
                          <a
                            href={auth.external_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-[10px] text-primary hover:underline mt-0.5"
                          >
                            <span>访问外部考证词条</span>
                            <ExternalLink className="h-2.5 w-2.5" />
                          </a>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}

            {activeTab === "network" && (
              <div className="space-y-4">
                {/* SNA 中心度指标卡片 */}
                <SNAMetricsCard metrics={metrics} isLoading={isLoading} />

                {/* 关系网络列表 */}
                <RelationshipList
                  personName={person?.display_name || `人物 #${personId}`}
                  neighbors={neighbors}
                  onSelectNeighbor={onSelectNeighbor}
                  onExpandNeighbor={onExpandNeighbor}
                  isLoading={isLoading}
                />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
