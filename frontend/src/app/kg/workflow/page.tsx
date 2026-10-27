"use client";

import React, { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { groupsApi } from "@/lib/groupsApi";
import { RelationshipReviewPanel } from "@/components/kg/review/RelationshipReviewPanel";
import { DisambiguationPanel } from "@/components/kg/review/DisambiguationPanel";
import { SnapshotPanel } from "@/components/kg/review/SnapshotPanel";

type Tab = "relationships" | "disambiguation" | "snapshots";

const TAB_LABELS: Record<Tab, string> = {
  relationships: "关系审核",
  disambiguation: "人物消歧",
  snapshots: "图谱快照",
};

interface GroupOption {
  id: number;
  name: string;
}

/**
 * Stage 10 research workflow: relationships, disambiguation, snapshots.
 *
 * The group selector is mandatory — every panel is group-scoped, and switching groups
 * remounts the panel (via `key`) so a previous group's rows can never linger on screen.
 */
export default function WorkflowPage() {
  const searchParams = useSearchParams();
  const initialGroup = Number(searchParams.get("group_id") || 0);
  const [groups, setGroups] = useState<GroupOption[]>([]);
  const [groupId, setGroupId] = useState<number>(initialGroup || 0);
  const [tab, setTab] = useState<Tab>("relationships");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    groupsApi
      .list(1, 100)
      .then((data: { items: GroupOption[] }) => {
        if (cancelled) return;
        setGroups(data.items || []);
        if (!groupId && data.items?.length) setGroupId(data.items[0].id);
      })
      .catch((err: any) => {
        if (!cancelled) setError(err?.message || "加载文档组失败。");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const groupName = groups.find((item) => item.id === groupId)?.name;

  return (
    <div className="min-h-[calc(100vh-5rem)] bg-slate-50/60 p-6 lg:p-8">
      <div className="mx-auto max-w-7xl space-y-5">
        <header className="flex flex-wrap items-center justify-between gap-4 rounded-3xl border border-white/70 bg-white/80 p-5 shadow-xl backdrop-blur-xl">
          <div>
            <h1 className="text-lg font-bold text-slate-900">历史研究审核工作台</h1>
            <p className="text-xs text-slate-500">
              关系审核、人物消歧与图谱快照。来源（真实模型 / 规则桩 / 固定 Mock）与审核状态分开呈现。
            </p>
          </div>
          <div className="flex items-end gap-3">
            <div>
              <label className="text-[11px] font-medium text-slate-500">研究组</label>
              <select
                value={groupId || ""}
                onChange={(event) => setGroupId(Number(event.target.value))}
                className="mt-1 block rounded-lg border border-slate-300 px-2 py-1 text-xs"
                data-group-selector="true"
              >
                <option value="">请选择</option>
                {groups.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name}
                  </option>
                ))}
              </select>
            </div>
            {groupName && (
              <span className="pb-1 text-[11px] text-slate-500">组内证据范围：{groupName}</span>
            )}
          </div>
        </header>

        {error && (
          <div className="rounded-xl border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-800">
            {error}
          </div>
        )}
        {!groupId && (
          <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-500">
            请选择研究组。所有列表都是组级证据范围，不会回退到全局数据。
          </div>
        )}

        {groupId > 0 && (
          <>
            <div className="flex gap-1 rounded-xl bg-slate-100 p-1" data-workflow-tabs="true">
              {(Object.keys(TAB_LABELS) as Tab[]).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setTab(key)}
                  data-tab={key}
                  className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                    tab === key ? "bg-white text-slate-900 shadow-sm" : "text-slate-600"
                  }`}
                >
                  {TAB_LABELS[key]}
                </button>
              ))}
            </div>
            {tab === "relationships" && (
              <RelationshipReviewPanel key={`rel-${groupId}`} groupId={groupId} groupName={groupName} />
            )}
            {tab === "disambiguation" && (
              <DisambiguationPanel key={`dis-${groupId}`} groupId={groupId} />
            )}
            {tab === "snapshots" && <SnapshotPanel key={`snap-${groupId}`} groupId={groupId} />}
          </>
        )}
      </div>
    </div>
  );
}
