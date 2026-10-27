"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  NetworkGraph,
  NetworkGraphHandle,
} from "@/components/kg/graph/NetworkGraph";
import { GraphControls } from "@/components/kg/graph/GraphControls";
import { GraphFilterBar } from "@/components/kg/graph/GraphFilterBar";
import { GraphLegend } from "@/components/kg/graph/GraphLegend";
import { PersonDetailDrawer } from "@/components/kg/person/PersonDetailDrawer";
import { GroupSelector } from "@/components/groups/GroupSelector";
import { ExtractionPanel } from "@/components/kg/graph/ExtractionPanel";
import { ProvenanceBadge } from "@/components/kg/review/ProvenanceBadge";
import {
  GraphNode,
  GraphLink,
  SNAGraphFilterParams,
  SNANetworkSummary,
  Person,
  Relationship as ApiRelationship,
  GroupGraphOverview,
  GroupGraphScope,
  GroupGraphRelationship,
  GroupRelationshipEvidence,
  GroupPersonMentions,
  GraphFacets,
  GraphView,
  RelationType,
} from "@/types/kg";
import { kgApi } from "@/lib/kgApi";
import { parseGroupIdParam } from "@/lib/groupScope";
import {
  SCOPE_STATE_LABEL,
  canRenderGraph,
  coverageLabel,
  evidenceLocator,
  evidenceTrustLine,
  excludedHint,
  groupEvidenceByDocument,
  metricsNotice,
  personMentionHeadline,
  personMentionNotice,
  relationTypeLabel,
  relationshipPanelFacts,
  relationshipEvidenceLabel,
  scopeBanner,
  scopeStateTone,
  trustNotice,
  viewLabel,
} from "@/lib/graphScope";
import {
  Search,
  Camera,
  Activity,
  GitFork,
  Users,
  Layers,
  Sparkles,
  CheckCircle2,
  AlertCircle,
  Loader2,
  BookOpen,
  X,
  PanelLeftClose,
  PanelLeftOpen,
  Tags,
  Share2,
} from "lucide-react";

const TONE_CLASS: Record<string, string> = {
  ok: "bg-emerald-50 text-emerald-800 border-emerald-200",
  warn: "bg-amber-50 text-amber-800 border-amber-200",
  bad: "bg-rose-50 text-rose-800 border-rose-200",
  idle: "bg-slate-100 text-slate-700 border-slate-200",
};

export default function KnowledgeGraphPage() {
  const graphRef = useRef<NetworkGraphHandle | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [showAllLabels, setShowAllLabels] = useState(false);
  const [layoutMode, setLayoutMode] = useState<"network" | "components">("network");
  const [filters, setFilters] = useState<SNAGraphFilterParams>({
    include_isolates: true,
    include_provisional: false,
    include_unknown_dates: true,
    directed: true,
    group_id: undefined,
    view: undefined,
  });
  const groupId = filters.group_id ?? null;
  // A group shows everything extracted from its own evidence; the global view keeps
  // the legacy review-gated default.
  const view: GraphView = (filters.view as GraphView) || (groupId != null ? "all" : "official");

  const [summary, setSummary] = useState<SNANetworkSummary | null>(null);
  const [scope, setScope] = useState<GroupGraphScope | null>(null);
  const [overview, setOverview] = useState<GroupGraphOverview | null>(null);
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [links, setLinks] = useState<GraphLink[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const [selectedRelationship, setSelectedRelationship] = useState<GroupGraphRelationship | null>(null);
  const [relationshipEvidence, setRelationshipEvidence] = useState<GroupRelationshipEvidence | null>(null);
  const [personMentions, setPersonMentions] = useState<GroupPersonMentions | null>(null);
  const [panelError, setPanelError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [facets, setFacets] = useState<GraphFacets | null>(null);
  const [facetsLoading, setFacetsLoading] = useState(false);
  const [facetsError, setFacetsError] = useState<string | null>(null);
  const [isSnapshotSaving, setIsSnapshotSaving] = useState(false);
  const [snapshotMsg, setSnapshotMsg] = useState<string | null>(null);
  const requestToken = useRef(0);

  // 搜索输入与结果弹窗
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<Person[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showSearchDropdown, setShowSearchDropdown] = useState(false);

  // 组 ID 可从 URL 读取（便于刷新与分享研究范围）
  useEffect(() => {
    const fromUrl = parseGroupIdParam(
      new URLSearchParams(window.location.search).get("group")
    );
    if (fromUrl != null) {
      setFilters((prev) => (prev.group_id === fromUrl ? prev : { ...prev, group_id: fromUrl }));
    }
  }, []);

  const handleGroupChange = (nextGroupId: number | null) => {
    setFilters((prev) => ({
      ...prev,
      group_id: nextGroupId ?? undefined,
      // 切换研究范围时重置视图口径，交给新范围自己的默认值
      view: undefined,
    }));
    setSelectedNodeId(null);
    setSelectedRelationship(null);
    setRelationshipEvidence(null);
    setPersonMentions(null);
    // 让 URL 保持与当前研究范围一致
    const url = new URL(window.location.href);
    if (nextGroupId == null) url.searchParams.delete("group");
    else url.searchParams.set("group", String(nextGroupId));
    window.history.replaceState({}, "", url.toString());
  };

  // 筛选项：与图谱同源（后端用同一个 scoped_network）。失败时清空，绝不沿用上一组。
  const loadFacets = useCallback(
    async (token: number, scopeGroupId: number) => {
      setFacetsLoading(true);
      setFacetsError(null);
      try {
        const next = await kgApi.getGroupGraphFacets(scopeGroupId, {
          ...filters,
          group_id: scopeGroupId,
        });
        if (requestToken.current !== token) return;
        setFacets(next);
      } catch (err: any) {
        if (requestToken.current !== token) return;
        setFacets(null);
        setFacetsError(err?.message || "筛选项加载失败。");
      } finally {
        if (requestToken.current === token) setFacetsLoading(false);
      }
    },
    [filters]
  );

  // 加载网络数据
  const loadNetworkData = useCallback(async () => {
    const token = requestToken.current + 1;
    requestToken.current = token;
    setIsLoading(true);
    setSnapshotMsg(null);
    setLoadError(null);
    setSelectedRelationship(null);
    setRelationshipEvidence(null);
    setPersonMentions(null);
    setPanelError(null);
    try {
      if (groupId != null) {
        // —— 组级：证据优先，节点/边/指标都来自同一子图定义 ——
        const groupScope = await kgApi.getGroupGraphScope(groupId);
        if (requestToken.current !== token) return;
        setScope(groupScope);

        if (!canRenderGraph(groupScope.extraction_state)) {
          // 空/未抽取不是错误，但绝不能退回到全库图谱
          setNodes([]);
          setLinks([]);
          setOverview(null);
          setSummary(null);
          // 筛选项必须跟着组走：拿不到就清空，绝不沿用上一组
          await loadFacets(token, groupId);
          return;
        }

        const [groupOverview, scopedSummary] = await Promise.all([
          kgApi.getGroupGraphOverview(groupId, filters),
          kgApi.getNetworkSummary({ ...filters, group_id: groupId }),
        ]);
        if (requestToken.current !== token) return;

        setOverview(groupOverview);
        setSummary(scopedSummary);
        await loadFacets(token, groupId);
        if (requestToken.current !== token) return;

        const degree = new Map<number, number>();
        groupOverview.relationships.forEach((rel) => {
          degree.set(rel.source_person_id, (degree.get(rel.source_person_id) || 0) + 1);
          degree.set(rel.target_person_id, (degree.get(rel.target_person_id) || 0) + 1);
        });

        const graphNodes: GraphNode[] = groupOverview.persons.map((person) => ({
          id: person.id,
          label: person.display_name,
          // role_category comes from the backend taxonomy; falling back to the raw
          // role keeps older payloads working.
          role: person.role_category || person.primary_role,
          organization: person.organization,
          status: person.identity_status,
          degree: degree.get(person.id) || 0,
        }));
        const graphLinks: GraphLink[] = groupOverview.relationships.map((rel) => ({
          id: `link_${rel.id}`,
          source: rel.source_person_id,
          target: rel.target_person_id,
          relationType: rel.relation_type,
          direction: rel.directionality === "undirected" ? "undirected" : "directed",
          relationCount: 1,
          evidenceCount: rel.evidence_count,
          evidenceBlockIds: rel.evidence_block_ids,
          claimStatus: rel.claim_status,
        }));

        setNodes(
          filters.include_isolates === false
            ? graphNodes.filter((node) => (node.degree || 0) > 0)
            : graphNodes
        );
        setLinks(graphLinks);
        return;
      }

      // —— 全库视图：完整取数（分页循环），不再因 100/150 截断而误报完整性 ——
      setScope(null);
      setOverview(null);
      const sum = await kgApi.getNetworkSummary(filters);
      if (requestToken.current !== token) return;
      setSummary(sum);

      const persons: Person[] = [];
      let personPage = 0;
      for (;;) {
        const res = await kgApi.listPersons({
          identity_status: filters.include_provisional ? undefined : "accepted",
          skip: personPage * 200,
          limit: 200,
        });
        persons.push(...res.items);
        if (res.items.length < 200 || persons.length >= res.total) break;
        personPage += 1;
      }
      if (requestToken.current !== token) return;

      const relationships: ApiRelationship[] = [];
      let relationshipPage = 0;
      for (;;) {
        const res = await kgApi.listRelationships({
          skip: relationshipPage * 200,
          limit: 200,
        });
        relationships.push(...res.items);
        if (res.items.length < 200 || relationships.length >= res.total) break;
        relationshipPage += 1;
      }
      if (requestToken.current !== token) return;

      const personMap = new Map<number, Person>();
      persons.forEach((p) => personMap.set(p.id, p));

      const nodeDegreeMap = new Map<number, number>();
      const graphLinks: GraphLink[] = [];
      relationships.forEach((r) => {
        if (!personMap.has(r.source_person_id) || !personMap.has(r.target_person_id)) return;
        if (r.source_person_id === r.target_person_id) return;
        if (filters.relation_types && filters.relation_types.length > 0) {
          if (!filters.relation_types.includes(r.relation_type)) return;
        }
        graphLinks.push({
          id: `link_${r.id}`,
          source: r.source_person_id,
          target: r.target_person_id,
          relationType: r.relation_type,
          direction: r.directionality === "undirected" ? "undirected" : "directed",
          relationCount: 1,
          evidenceCount: r.evidences?.length || 1,
          evidenceBlockIds: r.evidences?.map((e) => e.block_id) || [],
          claimStatus: r.claim_status,
        });
        nodeDegreeMap.set(r.source_person_id, (nodeDegreeMap.get(r.source_person_id) || 0) + 1);
        nodeDegreeMap.set(r.target_person_id, (nodeDegreeMap.get(r.target_person_id) || 0) + 1);
      });

      const graphNodes: GraphNode[] = persons.map((p) => ({
        id: p.id,
        label: p.display_name,
        role: p.primary_role,
        organization: p.organization,
        status: p.identity_status,
        degree: nodeDegreeMap.get(p.id) || 0,
      }));

      setNodes(
        filters.include_isolates === false
          ? graphNodes.filter((node) => (node.degree || 0) > 0)
          : graphNodes
      );
      setLinks(graphLinks);
    } catch (err: any) {
      if (requestToken.current !== token) return;
      // 失败必须显式呈现：既不能沿用旧组数据，也不能悄悄改成全库
      setNodes([]);
      setLinks([]);
      setSummary(null);
      setLoadError(err?.message || "图谱数据加载失败。");
    } finally {
      if (requestToken.current === token) setIsLoading(false);
    }
  }, [filters, groupId]);

  useEffect(() => {
    loadNetworkData();
  }, [loadNetworkData]);

  // 只刷新范围/台账状态：轮询时不动已选中的证据面板
  const refreshScope = useCallback(async () => {
    if (groupId == null) return;
    try {
      const next = await kgApi.getGroupGraphScope(groupId);
      setScope(next);
    } catch {
      // 由主加载器统一呈现错误，轮询失败不再叠加提示
    }
  }, [groupId]);

  const activeRun =
    scope?.extraction_run != null &&
    (scope.extraction_run.status === "queued" || scope.extraction_run.status === "running");

  useEffect(() => {
    if (!activeRun) return;
    const timer = setTimeout(() => {
      void refreshScope();
    }, 4000);
    return () => clearTimeout(timer);
  }, [activeRun, refreshScope, scope?.extraction_run?.attempted_blocks]);

  // 任务从“进行中”变为终态或暂停后，图谱本身需要重新取数
  const lastRunStatus = useRef<string | null>(null);
  useEffect(() => {
    const status = scope?.extraction_run?.status ?? null;
    const previous = lastRunStatus.current;
    lastRunStatus.current = status;
    if (
      previous != null &&
      status != null &&
      previous !== status &&
      (previous === "queued" || previous === "running") &&
      status !== "queued" &&
      status !== "running"
    ) {
      void loadNetworkData();
    }
  }, [scope, loadNetworkData]);

  const handleSelectRelationship = async (relationship: GroupGraphRelationship) => {
    setSelectedRelationship(relationship);
    setSelectedNodeId(null);
    setPersonMentions(null);
    setPanelError(null);
    if (groupId == null) {
      setRelationshipEvidence(null);
      return;
    }
    try {
      const evidence = await kgApi.getGroupRelationshipEvidence(groupId, relationship.id);
      setRelationshipEvidence(evidence);
    } catch (err: any) {
      setRelationshipEvidence(null);
      setPanelError(err?.message || "证据加载失败。");
    }
  };

  const handleSelectNode = async (nodeId: number | null) => {
    setSelectedNodeId(nodeId);
    setSelectedRelationship(null);
    setRelationshipEvidence(null);
    setPanelError(null);
    setPersonMentions(null);
    if (nodeId == null || groupId == null) return;
    try {
      const mentions = await kgApi.getGroupPersonMentions(groupId, nodeId);
      setPersonMentions(mentions);
    } catch (err: any) {
      setPersonMentions(null);
      setPanelError(err?.message || "人物提及加载失败。");
    }
  };

  const personName = (id: number): string =>
    overview?.persons.find((person) => person.id === id)?.display_name || `#${id}`;

  // 局部增量展开邻居 (按需加载子图)
  const handleExpandNode = async (nodeId: number) => {
    try {
      const neighborData = await kgApi.getPersonNeighbors(nodeId, filters);
      if (!neighborData || !neighborData.neighbors) return;

      const newNodes = [...nodes];
      const newLinks = [...links];
      const existingNodeIds = new Set(nodes.map((n) => n.id));

      neighborData.neighbors.forEach((nbr) => {
        if (!existingNodeIds.has(nbr.person_id)) {
          newNodes.push({
            id: nbr.person_id,
            label: nbr.display_name,
            role: nbr.primary_role,
            organization: nbr.organization,
            status: "accepted",
            degree: 1,
          });
          existingNodeIds.add(nbr.person_id);
        }

        const linkId = `link_${nodeId}_${nbr.person_id}`;
        if (!newLinks.find((l) => l.id === linkId)) {
          newLinks.push({
            id: linkId,
            source: nodeId,
            target: nbr.person_id,
            relationType: nbr.relation_types[0] || "commercial",
            direction: nbr.direction,
            relationCount: nbr.relation_count,
            evidenceCount: nbr.evidence_count,
            evidenceBlockIds: nbr.evidence_block_ids,
          });
        }
      });

      setNodes(newNodes);
      setLinks(newLinks);
    } catch (err) {
      console.error("Expand node failed:", err);
    }
  };

  // 搜索人物
  const handleSearch = async (val: string) => {
    setSearchQuery(val);
    if (!val.trim()) {
      setSearchResults([]);
      setShowSearchDropdown(false);
      return;
    }

    setIsSearching(true);
    try {
      const res = await kgApi.listPersons({
        q: val.trim(),
        limit: 8,
        ...(groupId != null ? { group_id: groupId } : {}),
      });
      setSearchResults(res.items || []);
      setShowSearchDropdown(true);
    } catch (err) {
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  };

  // 选中搜索人物并在图谱中定位
  const handleSelectSearchResult = (person: Person) => {
    setShowSearchDropdown(false);
    setSearchQuery(person.display_name);

    // 如果图中没有该人物，增量加入图中
    if (!nodes.find((n) => n.id === person.id)) {
      setNodes((prev) => [
        ...prev,
        {
          id: person.id,
          label: person.display_name,
          // role_category comes from the backend taxonomy; falling back to the raw
          // role keeps older payloads working.
          role: person.role_category || person.primary_role,
          organization: person.organization,
          status: person.identity_status,
          degree: 1,
        },
      ]);
    }

    // Select through the same path as a canvas click: it also loads the group-scoped
    // mentions. Calling setSelectedNodeId directly left the drawer without data and the
    // panel then rendered the "0 条" fallback for people that do have mentions.
    handleSelectNode(person.id);

    // Centre the camera on the person now (the node is usually already laid out) …
    graphRef.current?.focusNode(person.id);

    // … and again once the neighbour expansion has been laid out: every data change
    // re-fits the camera, which is what made the search "not zoom anywhere".
    void handleExpandNode(person.id).finally(() => {
      requestAnimationFrame(() => graphRef.current?.focusNode(person.id));
    });
  };

  // 持久化保存快照（携带当前研究范围与过滤参数，便于日后复现）
  const handleSaveSnapshot = async () => {
    setIsSnapshotSaving(true);
    setSnapshotMsg(null);
    try {
      const runId = `canton_snap_${Date.now()}`;
      await kgApi.createSnapshot({
        run_id: runId,
        graph_scope:
          groupId != null ? `research_group:${groupId}` : "canton_factory_archives",
        filters,
      });
      setSnapshotMsg(`快照已成功持久化落库 (批次: ${runId})`);
    } catch (err: any) {
      setSnapshotMsg(`保存失败: ${err.message}`);
    } finally {
      setIsSnapshotSaving(false);
    }
  };

  return (
    <div className="relative flex h-[calc(100vh-5rem)] flex-col overflow-hidden bg-slate-50/60">
      {/* 顶部控制栏与宏观指标状态条：自身可滚动且有高度上限，绝不把画布挤没 */}
      <div className="z-20 flex max-h-[55vh] shrink-0 flex-col gap-2.5 overflow-y-auto border-b border-white/60 bg-white/70 px-6 py-3 backdrop-blur-xl shadow-xs">
        {/* 第一行：搜索、快照与拓扑指标 */}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <GitFork className="h-4 w-4" />
            </div>
            <div>
              <h1 className="text-sm font-bold text-slate-900">广州商馆人物关系知识图谱</h1>
              <p className="text-[11px] text-slate-500">
                1792–1793 英国东印度公司十三行涉外商贸与涉华社会网络
              </p>
            </div>
          </div>

          {/* 宏观拓扑关键指标摘要 */}
          {summary && (
            <div className="hidden lg:flex items-center gap-3 text-xs text-slate-600 bg-slate-100/60 px-3.5 py-1.5 rounded-2xl border border-slate-200/50">
              <span className="flex items-center gap-1 font-medium">
                <Users className="h-3.5 w-3.5 text-primary" />
                节点: <strong className="text-slate-900">{summary.node_count}</strong>
              </span>
              <span className="text-slate-300">|</span>
              <span className="flex items-center gap-1 font-medium">
                <Activity className="h-3.5 w-3.5 text-indigo-500" />
                关系: <strong className="text-slate-900">{summary.edge_count}</strong>
              </span>
              <span className="text-slate-300">|</span>
              <span className="flex items-center gap-1 font-medium">
                <Layers className="h-3.5 w-3.5 text-emerald-500" />
                密度: <strong className="text-slate-900">{summary.density.toFixed(4)}</strong>
              </span>
              <span className="text-slate-300">|</span>
              <span className="flex items-center gap-1 font-medium">
                分量: <strong className="text-slate-900">{summary.connected_components_count}</strong>
              </span>
              {(summary.synthetic_edge_count ?? 0) + (summary.unknown_edge_count ?? 0) > 0 && (
                <>
                  <span className="text-slate-300">|</span>
                  <span
                    className="flex items-center gap-1 font-medium text-amber-700"
                    title={metricsNotice(summary) ?? undefined}
                    data-metrics-trust="unverified"
                  >
                    <AlertCircle className="h-3.5 w-3.5" />
                    指标含未验证关系
                  </span>
                </>
              )}
            </div>
          )}

          {/* 搜索与快照按钮 */}
          <div className="flex items-center gap-2 relative">
            <div className="relative">
              <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
              <input
                type="text"
                placeholder="搜索历史人物 (浩官/Browne/潘有度)..."
                value={searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
                onFocus={() => searchResults.length > 0 && setShowSearchDropdown(true)}
                className="h-8 w-64 rounded-full border border-slate-200 bg-white/90 pl-8 pr-3 text-xs text-slate-800 placeholder:text-slate-400 outline-none focus:border-primary shadow-xs"
              />

              {/* 搜索下拉弹窗 */}
              {showSearchDropdown && searchResults.length > 0 && (
                <div className="absolute left-0 top-10 z-50 w-72 rounded-2xl border border-white/80 bg-white p-2 shadow-2xl">
                  <div className="text-[10px] font-semibold text-slate-400 px-2 py-1">人物检索结果</div>
                  <div className="flex flex-col gap-1 max-h-56 overflow-y-auto">
                    {searchResults.map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        onClick={() => handleSelectSearchResult(p)}
                        className="flex flex-col rounded-xl px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-slate-50"
                      >
                        <div className="font-semibold text-slate-800">{p.display_name}</div>
                        <div className="text-[10px] text-slate-400">
                          {p.primary_role || "历史人物"} · {p.organization || "独立"}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <button
              type="button"
              disabled={isSnapshotSaving}
              onClick={handleSaveSnapshot}
              className="flex items-center gap-1.5 rounded-full bg-slate-900 px-3.5 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-slate-800 disabled:opacity-50"
            >
              <Camera className="h-3.5 w-3.5" />
              <span>{isSnapshotSaving ? "持久化中..." : "保存SNA快照"}</span>
            </button>
          </div>
        </div>

        {/* 快照反馈提示 */}
        {snapshotMsg && (
          <div className="flex items-center gap-2 rounded-xl bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-800 border border-emerald-200">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
            <span>{snapshotMsg}</span>
          </div>
        )}

        {/* 研究范围（文档组）：服务端按组内证据过滤，缺失/无效组不会退回全库 */}
        <div className="flex flex-wrap items-center gap-3" data-graph-scope-bar="true">
          <GroupSelector
            value={groupId}
            onChange={handleGroupChange}
            disabled={isLoading}
            compact
          />
          <div className="flex items-center gap-1 rounded-lg bg-slate-100 p-0.5" data-graph-view-toggle="true">
            {(groupId != null
              ? (["all", "official", "candidates"] as GraphView[])
              : (["official", "candidates"] as GraphView[])
            ).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() =>
                  setFilters((prev) => ({ ...prev, view: option }))
                }
                className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
                  view === option
                    ? "bg-white text-slate-900 shadow-sm"
                    : "text-slate-500 hover:text-slate-800"
                }`}
                data-graph-view-option={option}
              >
                {viewLabel(option)}
              </button>
            ))}
          </div>
          {scope && (
            <span className="text-[11px] text-slate-500" data-graph-scope-banner="true">
              {scopeBanner(scope)}
            </span>
          )}
          {scope && (
            <span
              className={`px-1.5 py-0.5 rounded border text-[10px] ${TONE_CLASS[scopeStateTone(scope.extraction_state)]}`}
              data-graph-scope-state={scope.extraction_state}
            >
              {SCOPE_STATE_LABEL[scope.extraction_state]}
            </span>
          )}
          {scope && (
            <span className="text-[10px] text-slate-400" data-graph-coverage="true">
              {coverageLabel(scope)}
            </span>
          )}
        </div>

        {loadError && (
          <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs text-rose-800" data-graph-error="true">
            <AlertCircle className="h-3.5 w-3.5" />
            <span>图谱加载失败：{loadError}</span>
          </div>
        )}

        {scope && groupId != null && trustNotice(scope.data_trust) && (
          <div
            className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
            data-graph-trust-warning="true"
          >
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <div className="space-y-0.5">
              <p className="font-medium">数据来源：Mock 模型（不是档案发现）</p>
              <p className="text-[11px]">{trustNotice(scope.data_trust)}</p>
            </div>
          </div>
        )}

        {scope && groupId != null && (
          <ExtractionPanel scope={scope} onChanged={loadNetworkData} />
        )}

        {scope && !canRenderGraph(scope.extraction_state) && (
          <div className="flex items-start gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700" data-graph-state-panel="true">
            <BookOpen className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <div className="space-y-0.5">
              <p>{scope.state_message}</p>
              <p className="text-[10px] text-slate-500">
                说明：空组与未抽取都不回退为全库图谱；抽取进度、暂停原因与 token 消耗均来自持久化台账，无法核实的数字不会显示。
              </p>
            </div>
          </div>
        )}

        {scope && excludedHint(scope, view) && (
          <p className="text-[11px] text-slate-500" data-graph-view-hint="true">
            {excludedHint(scope, view)}
          </p>
        )}


        {/* 多维过滤栏：选项全部来自后端 facets（与图谱同一子图） */}
        <GraphFilterBar
          filters={filters}
          onChange={setFilters}
          onRefresh={loadNetworkData}
          isLoading={isLoading}
          facets={facets}
          facetsLoading={facetsLoading}
          facetsError={facetsError}
        />
      </div>

      {/* 核心画布视口：左侧关系栏参与布局（可收起），画布不再被覆盖层挡住 */}
      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        {sidebarOpen && groupId != null && (
          <aside
            className="flex w-72 shrink-0 flex-col overflow-hidden border-r border-slate-200 bg-white/85 backdrop-blur"
            data-graph-relationships="true"
          >
            <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
              <span className="text-[11px] font-semibold text-slate-700">
                组内关系（{overview?.relationships.length ?? 0}）
              </span>
              <div className="flex items-center gap-1">
                <span className="text-[10px] text-slate-400">{viewLabel(view)}</span>
                <button
                  type="button"
                  onClick={() => setSidebarOpen(false)}
                  className="rounded p-0.5 text-slate-400 hover:bg-slate-100"
                  aria-label="收起关系列表"
                  data-sidebar-toggle="true"
                >
                  <PanelLeftClose className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
              {(overview?.relationships ?? []).map((relationship) => (
                <li key={relationship.id}>
                  <button
                    type="button"
                    data-graph-relationship={relationship.id}
                    onClick={() => {
                      handleSelectRelationship(relationship);
                      graphRef.current?.focusNode(relationship.source_person_id);
                    }}
                    className={`w-full rounded-lg px-2 py-1.5 text-left text-[11px] transition-colors ${
                      selectedRelationship?.id === relationship.id
                        ? "bg-primary/10 text-slate-900"
                        : "text-slate-700 hover:bg-slate-50"
                    }`}
                  >
                    <div className="font-medium">
                      {personName(relationship.source_person_id)} →{" "}
                      {personName(relationship.target_person_id)}
                    </div>
                    <div className="text-[10px] text-slate-500">
                      {relationship.relation_type} · {relationship.claim_status} ·{" "}
                      {relationshipEvidenceLabel(relationship)}
                    </div>
                    <div className="mt-1">
                      <ProvenanceBadge
                        kind={relationship.source_kind}
                        synthetic={relationship.synthetic}
                        counts={relationship.source_kinds}
                      />
                    </div>
                  </button>
                </li>
              ))}
              {groupId != null && (overview?.relationships.length ?? 0) === 0 && (
                <li className="px-2 py-4 text-center text-[11px] text-slate-500">
                  当前筛选下没有关系。
                </li>
              )}
            </ul>
          </aside>
        )}

        <div className="relative min-w-0 flex-1 overflow-hidden">
          {!sidebarOpen && (
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              className="absolute left-2 top-2 z-20 flex items-center gap-1 rounded-lg border border-slate-200 bg-white/90 px-2 py-1 text-[10px] text-slate-600 shadow"
              data-sidebar-toggle="true"
            >
              <PanelLeftOpen className="h-3.5 w-3.5" /> 关系列表
            </button>
          )}

          <NetworkGraph
            ref={graphRef}
            nodes={nodes}
            links={links}
            selectedNodeId={selectedNodeId}
            onSelectNode={(n) => handleSelectNode(n ? n.id : null)}
            onSelectLink={(link) => {
              // Edge ids are prefixed ("link_14620") while relationship ids are numeric;
              // comparing the two directly never matched, so clicking an edge selected it
              // on the canvas but opened nothing. Resolve through the prefixed id.
              const relationship = overview?.relationships.find(
                (item) => `link_${item.id}` === String(link?.id)
              );
              if (relationship) handleSelectRelationship(relationship);
            }}
            onExpandNode={handleExpandNode}
            isLoading={isLoading}
            showAllLabels={showAllLabels}
            layoutMode={layoutMode}
          />

          {/* 视口浮动控制器 (左下角) */}
          <div className="absolute bottom-5 left-5 z-20 flex flex-col gap-2">
            <GraphControls
              onZoomIn={() => graphRef.current?.zoomBy(1.25)}
              onZoomOut={() => graphRef.current?.zoomBy(1 / 1.25)}
              onReset={() => graphRef.current?.resetView()}
              onFitView={() => graphRef.current?.fitToView()}
            />
            <div className="flex items-center gap-1.5 rounded-2xl border border-white/60 bg-white/80 p-1.5 shadow-lg backdrop-blur-xl">
              <button
                type="button"
                onClick={() => setShowAllLabels((value) => !value)}
                title="始终显示全部人物姓名"
                data-label-toggle="true"
                data-label-mode={showAllLabels ? "all" : "auto"}
                className={`flex h-9 items-center gap-1 rounded-xl px-2 text-[11px] transition-all ${
                  showAllLabels
                    ? "bg-primary/10 text-primary"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                <Tags className="h-4 w-4" />
                <span className="hidden sm:inline">{showAllLabels ? "全部姓名" : "按缩放显示"}</span>
              </button>
              <div className="h-4 w-[1px] bg-slate-200" />
              <button
                type="button"
                onClick={() =>
                  setLayoutMode((mode) => (mode === "network" ? "components" : "network"))
                }
                title="在关系网布局与连通分量分离布局之间切换"
                data-layout-toggle="true"
                data-layout-mode={layoutMode}
                className="flex h-9 items-center gap-1 rounded-xl px-2 text-[11px] text-slate-600 transition-all hover:bg-slate-100 hover:text-slate-900"
              >
                <Share2 className="h-4 w-4" />
                <span className="hidden sm:inline">
                  {layoutMode === "network" ? "关系网布局" : "分组布局"}
                </span>
              </button>
            </div>
          </div>

          {/* 图例 (右下角)：只列出当前视图中真实存在的角色分类 */}
          <div className="absolute bottom-5 right-5 z-20">
            <GraphLegend
              roleCounts={overview?.role_counts ?? null}
              totalPersons={overview?.persons.length ?? null}
              knownRoleCount={overview?.role_known_count ?? null}
              unknownRoleCount={overview?.role_unknown_count ?? null}
            />
          </div>
        </div>
      </div>

      {/* 右侧证据面板：组内关系证据 → 组内人物提及 → 全库人物档案 */}
      {selectedRelationship && groupId != null && (
        <aside
          className="absolute right-4 top-4 z-30 max-h-[80%] w-[26rem] overflow-y-auto rounded-2xl border border-white/70 bg-white/95 p-4 shadow-2xl backdrop-blur"
          data-relationship-evidence="true"
        >
          <div className="mb-2 flex items-start justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">
                {personName(selectedRelationship.source_person_id)} →{" "}
                {personName(selectedRelationship.target_person_id)}
              </h3>
              <p className="text-[11px] text-slate-500">
                {relationTypeLabel(selectedRelationship.relation_type)} · 主张状态{" "}
                {selectedRelationship.claim_status} · 审核 {selectedRelationship.review_status}
              </p>
              {(() => {
                const facts = relationshipPanelFacts(
                  selectedRelationship,
                  overview?.relationships ?? []
                );
                return (
                  <div className="mt-1 space-y-0.5 text-[10px] text-slate-500">
                    <p data-panel-direction="true">方向：{facts.directionLabel}</p>
                    <p data-panel-time="true">时间：{facts.timeLabel}</p>
                    {facts.parallelLabel && (
                      <p className="text-amber-700" data-panel-parallel="true">
                        {facts.parallelLabel}
                      </p>
                    )}
                  </div>
                );
              })()}
              <div className="mt-1.5 flex flex-wrap gap-1">
                <button
                  type="button"
                  onClick={() => {
                    setFilters((prev) => ({
                      ...prev,
                      relation_types: [
                        selectedRelationship.relation_type as RelationType,
                      ],
                    }));
                    setSelectedRelationship(null);
                    setRelationshipEvidence(null);
                  }}
                  className="rounded-full border border-slate-200 px-2 py-0.5 text-[10px] text-slate-600 hover:bg-slate-50"
                  data-panel-filter-type="true"
                >
                  只看此关系类型（{relationTypeLabel(selectedRelationship.relation_type)}）
                </button>
                <button
                  type="button"
                  onClick={() => handleSelectNode(selectedRelationship.source_person_id)}
                  className="rounded-full border border-slate-200 px-2 py-0.5 text-[10px] text-slate-600 hover:bg-slate-50"
                  data-panel-goto-source="true"
                >
                  查看源人物
                </button>
              </div>
            </div>
            <button
              type="button"
              onClick={() => {
                setSelectedRelationship(null);
                setRelationshipEvidence(null);
              }}
              className="rounded-lg p-1 text-slate-400 hover:bg-slate-100"
              aria-label="关闭关系证据面板"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {panelError && (
            <p className="mb-2 rounded-lg bg-rose-50 px-2 py-1 text-[11px] text-rose-700">
              {panelError}
            </p>
          )}

          {!relationshipEvidence && !panelError && (
            <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
              <Loader2 className="h-3 w-3 animate-spin" /> 正在读取组内证据…
            </p>
          )}

          {relationshipEvidence && (
            <div className="space-y-3">
              <div className="rounded-lg bg-slate-50 px-2 py-1.5 text-[11px] text-slate-700">
                <div className="font-medium">
                  {relationshipEvidence.distinct_block_count ?? 0} 个组内区块 ·{" "}
                  {relationshipEvidence.in_group_evidence_count ??
                    relationshipEvidence.items.length}{" "}
                  条证据行
                  {typeof relationshipEvidence.distinct_quote_count === "number" && (
                    <> · {relationshipEvidence.distinct_quote_count} 句不同原文</>
                  )}
                </div>
                <div className="text-[10px] text-slate-500">
                  同一句关系在多个区块重复出现时，仍然只是同一条主张。
                </div>
                {evidenceTrustLine({
                  evidence_rows: relationshipEvidence.in_group_evidence_count,
                  verified_evidence_count: relationshipEvidence.verified_evidence_count,
                  synthetic_evidence_count: relationshipEvidence.synthetic_evidence_count,
                  unknown_evidence_count: relationshipEvidence.unknown_evidence_count,
                }) && (
                  <div className="mt-1 text-[10px] text-amber-700">
                    {evidenceTrustLine({
                      evidence_rows: relationshipEvidence.in_group_evidence_count,
                      verified_evidence_count: relationshipEvidence.verified_evidence_count,
                      synthetic_evidence_count: relationshipEvidence.synthetic_evidence_count,
                      unknown_evidence_count: relationshipEvidence.unknown_evidence_count,
                    })}
                  </div>
                )}
              </div>
              {relationshipEvidence.truncated && (
                <p className="text-[11px] text-slate-500">
                  （当前展示前 {relationshipEvidence.items.length} 条，每条证据的来源分别保留）
                </p>
              )}
              {relationshipEvidence.out_of_group_evidence_count > 0 && (
                <p className="text-[11px] text-amber-700">
                  （另有 {relationshipEvidence.out_of_group_evidence_count} 条证据属于其它文档组，未计入本组）
                </p>
              )}
              {relationshipEvidence.items.length === 0 && (
                <p className="rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800">
                  该关系在本组没有可追溯证据：全局审核状态不能替代当前组的证据资格。
                </p>
              )}
              {groupEvidenceByDocument(
                relationshipEvidence.items.map((item) => ({
                  evidence_id: item.evidence_id,
                  document_id: item.document_id,
                  document_name: item.document_name,
                  page_number: item.page_number,
                  block_id: item.block_id,
                  quote_text: item.quote_text,
                  block_text: item.block_text,
                  evidence_role: item.evidence_role,
                }))
              ).map((group) => (
                <div key={group.document_id} className="rounded-xl border border-slate-200 p-2">
                  <p className="mb-1 text-[11px] font-semibold text-slate-700">
                    {group.document_name}
                  </p>
                  <ul className="space-y-1.5">
                    {group.rows.map((row) => (
                      <li key={row.evidence_id} className="text-[11px] text-slate-600">
                        <p className="text-[10px] text-slate-400">
                          {evidenceLocator(row)} · {row.evidence_role}
                        </p>
                        <p className="mt-0.5 rounded bg-slate-50 px-2 py-1 leading-relaxed">
                          {row.quote_text || row.block_text || "（该证据没有可用引文）"}
                        </p>
                        {(() => {
                          const item = relationshipEvidence.items.find(
                            (candidate) => candidate.evidence_id === row.evidence_id
                          );
                          return item ? (
                            <div className="mt-1">
                              <ProvenanceBadge
                                kind={item.source_kind}
                                synthetic={item.synthetic}
                              />
                            </div>
                          ) : null;
                        })()}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </aside>
      )}

      {groupId != null && selectedNodeId != null && !selectedRelationship && (
        <aside
          className="absolute right-4 top-4 z-30 max-h-[80%] w-[24rem] overflow-y-auto rounded-2xl border border-white/70 bg-white/95 p-4 shadow-2xl backdrop-blur"
          data-person-mentions="true"
        >
          <div className="mb-2 flex items-start justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">
                {personMentions?.display_name || `人物 #${selectedNodeId}`}
              </h3>
              <p className="text-[11px] text-slate-500">
                身份状态 {personMentions?.identity_status || "—"} ·{" "}
                {personMentionHeadline(personMentions)}
                {personMentions?.truncated
                  ? `（展示前 ${personMentions.items.length} 条）`
                  : ""}
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                setSelectedNodeId(null);
                setPersonMentions(null);
              }}
              className="rounded-lg p-1 text-slate-400 hover:bg-slate-100"
              aria-label="关闭人物提及面板"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          {panelError && (
            <p className="mb-2 rounded-lg bg-rose-50 px-2 py-1 text-[11px] text-rose-700">
              {panelError}
            </p>
          )}
          {personMentions && personMentions.items.length === 0 && (
            <p
              className={
                personMentionNotice(personMentions).tone === "warn"
                  ? "rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800"
                  : "text-[11px] text-slate-500"
              }
              data-mention-notice={personMentionNotice(personMentions).tone}
            >
              {personMentionNotice(personMentions).text}
            </p>
          )}
          {personMentions && (personMentions.unlinked_items?.length ?? 0) > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-2">
              <p className="mb-1 text-[10px] font-medium text-amber-800">
                同名但尚未完成身份链接（需人工确认）
              </p>
              <ul className="space-y-1.5">
                {(personMentions.unlinked_items ?? []).map((mention) => (
                  <li key={`unlinked-${mention.mention_id}`} data-unlinked-mention="true">
                    <p className="text-[10px] text-amber-700">
                      {mention.document_name} · 第 {mention.page_number} 页 · Block #{mention.block_id}
                    </p>
                    <p className="text-[11px] leading-relaxed text-slate-700">
                      {mention.evidence_text || mention.raw_text}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <ul className="space-y-2">
            {personMentions?.items.map((mention) => (
              <li key={mention.mention_id} className="rounded-xl border border-slate-200 p-2">
                <p className="text-[10px] text-slate-400" data-mention-locator="true">
                  {mention.document_name} · 第 {mention.page_number} 页 · Block #{mention.block_id}
                  {mention.temporal_year ? ` · ${mention.temporal_year} 年` : ""}
                </p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-slate-700">
                  {mention.evidence_text || mention.raw_text}
                </p>
                <p className="mt-1 text-[10px] text-slate-400">
                  识别：{mention.resolution_status} · 审核：{mention.review_status}
                </p>
              </li>
            ))}
          </ul>
        </aside>
      )}

      {groupId == null && (
        <PersonDetailDrawer
          personId={selectedNodeId}
          filters={filters}
          onClose={() => setSelectedNodeId(null)}
          onSelectNeighbor={(nbrId) => handleSelectNode(nbrId)}
          onExpandNeighbor={handleExpandNode}
        />
      )}
    </div>
  );
}
