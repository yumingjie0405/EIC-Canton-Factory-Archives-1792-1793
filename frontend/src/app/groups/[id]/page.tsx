"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowLeft,
  BookOpen,
  FileUp,
  Loader2,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { groupsApi } from "@/lib/groupsApi";
import { kgApi } from "@/lib/kgApi";
import { documentApi } from "@/lib/api";
import { ExtractionPanel } from "@/components/kg/graph/ExtractionPanel";
import { GroupGraphScope } from "@/types/kg";
import { useHydrated } from "@/lib/useHydrated";
import {
  ImportFileResult,
  MAX_IMPORT_FILE_MB,
  classifyImportResponse,
  groupImportErrorMessage,
  isFileTooLarge,
  summarizeImportResults,
  oversizedFileMessage,
} from "@/lib/groupImport";
import {
  batchIsPending,
  batchProgressLabel,
  importStatusLabel,
  importStatusTone,
  importSummary,
} from "@/lib/groupScope";
import {
  GroupMemberDocument,
  ImportBatchResponse,
  ImportBatchStatusResponse,
  ResearchGroupDetailResponse,
} from "@/types/group";
import { autoResearchHeadline, autoOutcomeLabel, autoResearchTone } from "@/lib/graphScope";

const TONE_CLASS: Record<string, string> = {
  ok: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  warn: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  bad: "bg-rose-500/10 text-rose-300 border-rose-500/30",
  idle: "bg-slate-800 text-slate-300 border-slate-700",
};

export default function GroupDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const hydrated = useHydrated();
  const groupId = Number(params?.id);

  const [detail, setDetail] = useState<ResearchGroupDetailResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [batch, setBatch] = useState<ImportBatchResponse | null>(null);
  const [batchStatus, setBatchStatus] = useState<ImportBatchStatusResponse | null>(null);
  const [isImporting, setIsImporting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [importProgress, setImportProgress] = useState<string | null>(null);
  const [importResults, setImportResults] = useState<ImportFileResult[]>([]);
  const [availableDocs, setAvailableDocs] = useState<
    { id: number; original_filename?: string; filename: string; status: string }[]
  >([]);
  const [graphScope, setGraphScope] = useState<GroupGraphScope | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadGraphScope = useCallback(async () => {
    if (!Number.isInteger(groupId) || groupId < 1) return;
    try {
      setGraphScope(await kgApi.getGroupGraphScope(groupId));
    } catch {
      // 图谱范围状态失败不应阻断文档组管理页面
      setGraphScope(null);
    }
  }, [groupId]);

  useEffect(() => {
    void loadGraphScope();
  }, [loadGraphScope]);

  // 自动研究进行中时轮询台账，让导入后的进度无需手动刷新即可呈现
  const activeExtraction =
    graphScope?.extraction_run != null &&
    (graphScope.extraction_run.status === "queued" ||
      graphScope.extraction_run.status === "running");
  useEffect(() => {
    if (!activeExtraction) return;
    const timer = setTimeout(() => {
      void loadGraphScope();
    }, 4000);
    return () => clearTimeout(timer);
  }, [activeExtraction, loadGraphScope, graphScope?.extraction_run?.attempted_blocks]);

  const [isTogglingAuto, setIsTogglingAuto] = useState(false);
  const handleToggleAutoExtract = async (next: boolean) => {
    setIsTogglingAuto(true);
    setActionError(null);
    setNotice(null);
    try {
      await groupsApi.setAutoExtract(groupId, next);
      setNotice(
        next
          ? "已开启本组的自动研究：此后导入或新加入的 Word 会自动排队抽取。"
          : "已关闭本组的自动研究：新导入的文档不会自动排队，可手动启动。"
      );
      await load();
      await loadGraphScope();
    } catch (err: any) {
      setActionError(err?.message || "修改自动研究开关失败。");
    } finally {
      setIsTogglingAuto(false);
    }
  };

  useEffect(() => {
    if (!detail?.group) return;
    setEditName(detail.group.name);
    setEditDescription(detail.group.description || "");
  }, [detail?.group.id, detail?.group.name, detail?.group.description]);

  const load = useCallback(async () => {
    if (!Number.isInteger(groupId) || groupId < 1) {
      setError("无效的文档组编号。");
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      setDetail(await groupsApi.detail(groupId));
    } catch (err: any) {
      setError(err?.message || "无法加载文档组详情。");
    } finally {
      setIsLoading(false);
    }
  }, [groupId]);

  useEffect(() => {
    load();
  }, [load]);

  // Documents that exist but are not members yet — attachable without re-upload.
  useEffect(() => {
    let cancelled = false;
    documentApi
      .list(1, 100)
      .then((data: any) => {
        if (cancelled) return;
        const items = (data.items || []).map((doc: any) => ({
          id: doc.id,
          filename: doc.filename,
          original_filename: doc.original_filename,
          status: String(doc.status),
        }));
        setAvailableDocs(items);
      })
      .catch(() => {
        /* the attach panel simply stays empty; import still works */
      });
    return () => {
      cancelled = true;
    };
  }, [detail?.documents.length]);

  // Poll a batch until every document inside it is finished.
  useEffect(() => {
    if (!batch) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const status = await groupsApi.batchStatus(batch.group_id, batch.batch_id);
        if (cancelled) return;
        setBatchStatus(status);
        if (!batchIsPending(status.status_counts)) {
          await load();
          return;
        }
        timer = setTimeout(tick, 3000);
      } catch {
        /* keep the last known status; the manual refresh button still works */
      }
    };
    let timer = setTimeout(tick, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batch?.batch_id]);

  const handleImport = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const selected = Array.from(files);
    setIsImporting(true);
    setActionError(null);
    setNotice(null);
    setImportResults([]);
    const autoNotes: string[] = [];
    const results: ImportFileResult[] = [];
    try {
      // Upload one file per request: a single multipart body containing several
      // large documents (these exports embed page images and can be hundreds of
      // MB) fails in the browser before it is ever sent, leaving no server trace.
      for (let index = 0; index < selected.length; index += 1) {
        const file = selected[index];
        setImportProgress(`正在导入 ${index + 1}/${selected.length}：${file.name}`);
        if (isFileTooLarge(file.size)) {
          results.push({
            name: file.name,
            ok: false,
            message: oversizedFileMessage(file.name, file.size, MAX_IMPORT_FILE_MB),
          });
          setImportResults([...results]);
          continue;
        }
        try {
          const result = await groupsApi.importFiles(groupId, [file], true);
          setBatch(result);
          const outcome = classifyImportResponse(result);
          results.push({ name: file.name, ok: outcome.ok, message: outcome.message });
          const autoOutcome = result.auto_research?.[0];
          if (autoOutcome) {
            autoNotes.push(`${file.name}：${autoOutcomeLabel(autoOutcome.reason)}`);
            if (autoOutcome.dispatch_error) {
              setActionError(
                `自动研究任务已登记，但队列投递失败：${autoOutcome.dispatch_error}`
              );
            }
          }
        } catch (err: any) {
          results.push({ name: file.name, ok: false, message: groupImportErrorMessage(err) });
        }
        setImportResults([...results]);
      }
      const failedCount = results.filter((result) => !result.ok).length;
      const autoSummary = autoNotes.length
        ? " 自动研究：" + autoNotes.join("；") + "。"
        : "";
      setNotice(
        failedCount === 0
          ? summarizeImportResults(results) + autoSummary
          : summarizeImportResults(results) + " 失败原因见下方逐文件结果。" + autoSummary
      );
      await load();
      await loadGraphScope();
    } finally {
      setIsImporting(false);
      setImportProgress(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleRename = async (event: React.FormEvent) => {
    event.preventDefault();
    if (isSaving || isDeleting) return;
    if (!editName.trim()) {
      setActionError("文档组名称不能为空。");
      return;
    }
    setIsSaving(true);
    setActionError(null);
    setNotice(null);
    try {
      await groupsApi.rename(groupId, editName.trim(), editDescription.trim() || undefined);
      setNotice("已保存文档组信息。");
      await load();
    } catch (err: any) {
      setActionError(err?.message || "保存文档组信息失败。");
    } finally {
      setIsSaving(false);
    }
  };

  const handleDelete = async () => {
    if (isDeleting) return;
    setIsDeleting(true);
    setActionError(null);
    try {
      await groupsApi.remove(groupId);
      router.push("/groups");
    } catch (err: any) {
      setActionError(err?.message || "删除文档组失败。");
      setIsDeleting(false);
    }
  };

  const handleRetry = async (documentId: number) => {
    setActionError(null);
    try {
      await groupsApi.retryDocument(groupId, documentId);
      await load();
    } catch (err: any) {
      setActionError(err?.message || "重试失败。");
    }
  };

  const handleDetach = async (documentId: number) => {
    setActionError(null);
    try {
      await groupsApi.detachDocument(groupId, documentId);
      await load();
      setNotice(`已将文档 #${documentId} 从本组移除（原始档案与 OCR 结果保留）。`);
    } catch (err: any) {
      setActionError(err?.message || "移除失败。");
    }
  };

  const members: GroupMemberDocument[] = detail?.documents || [];
  const memberIds = new Set(members.map((member) => member.document_id));
  const attachable = availableDocs.filter((doc) => !memberIds.has(doc.id));

  const [extractingDocumentId, setExtractingDocumentId] = useState<number | null>(null);

  const handleExtractDocument = async (documentId: number) => {
    setActionError(null);
    setNotice(null);
    setExtractingDocumentId(documentId);
    try {
      const created = await kgApi.startGroupExtraction(groupId, { documentId });
      setNotice(
        `已为文档 #${documentId} 创建抽取任务 #${created.run_id}（${created.total_blocks} 个文本区块）。`
      );
      await loadGraphScope();
    } catch (err: any) {
      setActionError(err?.message || "启动该文档的抽取失败。");
    } finally {
      setExtractingDocumentId(null);
    }
  };

  const handleAttach = async (documentId: number) => {
    setActionError(null);
    try {
      const result = await groupsApi.attachDocuments(groupId, [documentId]);
      if (result.duplicates.length) {
        setNotice(`文档 #${documentId} 已在该组中（不会生成重复成员）。`);
      } else if (result.added.length) {
        setNotice(`已加入文档 #${documentId}（仅新增成员关系，未复制档案数据）。`);
      } else if (result.missing.length) {
        setActionError(`文档 #${documentId} 不存在。`);
      }
      await load();
    } catch (err: any) {
      setActionError(err?.message || "加入文档失败。");
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-8">
      <div className="max-w-6xl mx-auto space-y-6">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <Link href="/groups" className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200">
            <ArrowLeft className="w-3.5 h-3.5" />
            返回文档组列表
          </Link>
          <div className="flex items-center gap-2">
            <Link
              href={`/rag?group=${groupId}`}
              className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[11px]"
            >
              在该组开展智能问答
            </Link>
            <Link
              href={`/agent?group=${groupId}`}
              className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[11px]"
            >
              在该组开展 AI 研究
            </Link>
            <Link
              href={`/kg/graph?group=${groupId}`}
              data-group-graph-link="true"
              className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[11px]"
            >
              查看该组人物图谱
            </Link>
            <button
              type="button"
              onClick={load}
              disabled={isLoading}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[11px] disabled:opacity-50"
            >
              <RefreshCw className={`w-3 h-3 ${isLoading ? "animate-spin" : ""}`} />
              刷新状态
            </button>
          </div>
        </div>

        {isLoading && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            正在加载文档组…
          </div>
        )}

        {!isLoading && error && (
          <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">
            {error}
          </div>
        )}

        {detail && (
          <>
            <header className="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 space-y-1">
              <h1 className="text-lg font-semibold" data-group-name="true">
                {detail.group.name}
              </h1>
              <p className="text-xs text-slate-400">{detail.group.description || "（无描述）"}</p>
              <p className="text-xs text-slate-300">
                文档 {detail.group.document_count} 篇 · 可检索 {detail.group.ready_count} 篇
                {detail.group.failed_count > 0 && (
                  <span className="text-rose-300"> · 失败 {detail.group.failed_count} 篇</span>
                )}
              </p>
              {detail.warning && (
                <p className="text-[11px] text-amber-200" data-group-warning="true">
                  {detail.warning}
                </p>
              )}
            </header>

            <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 space-y-3" data-auto-research-section="true">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm text-slate-200">
                  <Sparkles className="w-4 h-4 text-indigo-400" />
                  自动研究（导入后自动排队抽取人物与关系）
                </div>
                <label className="flex items-center gap-2 text-[11px] text-slate-300">
                  <input
                    type="checkbox"
                    checked={detail.group.auto_extract}
                    disabled={!hydrated || isTogglingAuto}
                    onChange={(event) => handleToggleAutoExtract(event.target.checked)}
                    data-auto-research-toggle="true"
                  />
                  <span>
                    本组参与自动研究
                    {isTogglingAuto ? "（保存中…）" : ""}
                  </span>
                </label>
              </div>
              {detail.auto_research && (
                <div
                  className={`rounded-xl border px-3 py-2 text-[11px] ${
                    autoResearchTone(detail.auto_research) === "ok"
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
                      : autoResearchTone(detail.auto_research) === "warn"
                        ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                        : "border-slate-700 bg-slate-800/50 text-slate-300"
                  }`}
                  data-auto-research-state={detail.auto_research.reason}
                >
                  <p className="font-medium">{autoResearchHeadline(detail.auto_research)}</p>
                  <p className="mt-0.5 opacity-90">{detail.auto_research.detail}</p>
                  <p className="mt-0.5 text-[10px] opacity-70">
                    部署开关：{detail.auto_research.global_enabled ? "已启用" : "未启用"} ·
                    模型配置：{detail.auto_research.model_configured ? "已配置" : "缺失"} ·
                    单次上限：{detail.auto_research.budget_blocks} 个区块
                  </p>
                </div>
              )}
              {graphScope && (
                <p className="text-[11px] text-slate-400" data-auto-research-progress="true">
                  当前图谱状态：
                  {graphScope.extraction_run
                    ? `任务 #${graphScope.extraction_run.run_id} · ${graphScope.extraction_run.status} · 已处理 ${graphScope.extraction_run.attempted_blocks}/${graphScope.extraction_run.total_blocks} 个区块`
                    : "尚无抽取任务"}
                  {" · "}已抽取区块 {graphScope.blocks_with_extraction}/{graphScope.block_count} ·
                  人物 {graphScope.person_count} · 正式关系 {graphScope.relationship_count_official} ·
                  候选关系 {graphScope.relationship_count_candidates}
                </p>
              )}
              <p className="text-[10px] text-slate-500">
                自动研究只产生候选人物与候选关系（含区块证据），不会自动通过审核；
                历史文档不会因开关或重启自动补抽取，需要使用下方的手动抽取入口。
              </p>
            </section>

            <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 space-y-3">
              <div className="flex items-center gap-2 text-sm text-slate-200">
                <BookOpen className="w-4 h-4 text-indigo-400" />
                组信息维护
              </div>
              <form
                onSubmit={handleRename}
                data-group-edit-ready={hydrated ? "true" : "false"}
                className="grid grid-cols-1 md:grid-cols-3 gap-3"
              >
                <input
                  aria-label="文档组名称"
                  data-group-name-input="true"
                  value={editName}
                  onChange={(event) => setEditName(event.target.value)}
                  disabled={!hydrated || isSaving || isDeleting}
                  className="rounded-lg bg-slate-800 border border-slate-700 text-xs px-3 py-2"
                />
                <input
                  aria-label="文档组描述"
                  data-group-description-input="true"
                  value={editDescription}
                  onChange={(event) => setEditDescription(event.target.value)}
                  placeholder="描述（可选）"
                  disabled={!hydrated || isSaving || isDeleting}
                  className="md:col-span-2 rounded-lg bg-slate-800 border border-slate-700 text-xs px-3 py-2"
                />
                <div className="md:col-span-3 flex flex-wrap items-center gap-2">
                  <button
                    type="submit"
                    data-group-save="true"
                    disabled={!hydrated || isSaving || isDeleting}
                    className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium disabled:opacity-50"
                  >
                    {!hydrated ? "准备中…" : isSaving ? "保存中…" : "保存组信息"}
                  </button>
                  {!confirmDelete ? (
                    <button
                      type="button"
                      data-group-delete="true"
                      onClick={() => setConfirmDelete(true)}
                      disabled={!hydrated || isDeleting}
                      className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-rose-600/40 text-xs text-rose-200 disabled:opacity-50"
                    >
                      删除文档组
                    </button>
                  ) : (
                    <>
                      <span className="text-[11px] text-rose-200">
                        删除后组成员关系一并移除，档案与 OCR 结果保留。确认删除？
                      </span>
                      <button
                        type="button"
                        data-group-delete-confirm="true"
                        onClick={handleDelete}
                        disabled={isDeleting}
                        className="px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-500 text-xs font-medium disabled:opacity-50"
                      >
                        {isDeleting ? "删除中…" : "确认删除"}
                      </button>
                      <button
                        type="button"
                        data-group-delete-cancel="true"
                        onClick={() => setConfirmDelete(false)}
                        disabled={isDeleting}
                        className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs disabled:opacity-50"
                      >
                        取消
                      </button>
                    </>
                  )}
                </div>
              </form>
            </section>

            <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-sm text-slate-200">
                  <FileUp className="w-4 h-4 text-indigo-400" />
                  批量导入档案（PDF / 图片 / Word）
                </div>
                <label
                  className={`px-3 py-1.5 rounded-lg text-xs ${
                    hydrated && !isImporting
                      ? "bg-indigo-600 hover:bg-indigo-500 cursor-pointer"
                      : "bg-slate-700 text-slate-400 cursor-not-allowed"
                  }`}
                  data-import-label-ready={hydrated ? "true" : "false"}
                >
                  {!hydrated ? "准备中…" : isImporting ? "导入中…" : "选择文件并导入"}
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp,.docx"
                    className="hidden"
                    disabled={isImporting || !hydrated}
                    onChange={(event) => handleImport(event.target.files)}
                  />
                </label>
              </div>
              <p className="text-[11px] text-slate-400">
                重复文件按内容哈希去重（同组内不会产生重复成员）；单个文件失败只影响该文件。
                Word (.docx) 直接读取文字段落，导入后即可检索，不进 OCR 队列。
                多选时逐个上传，单个文件上限 {MAX_IMPORT_FILE_MB} MB。
              </p>
              {importProgress && (
                <p className="text-[11px] text-slate-300 flex items-center gap-1.5" data-import-progress="true">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  {importProgress}
                </p>
              )}
              {notice && (
                <p className="text-[11px] text-emerald-300" data-import-notice="true">
                  {notice}
                </p>
              )}
              {importResults.length > 0 && (
                <ul className="space-y-1" data-import-results="true">
                  {importResults.map((result) => (
                    <li
                      key={result.name}
                      data-import-result={result.ok ? "ok" : "failed"}
                      className={`text-[11px] ${result.ok ? "text-emerald-300" : "text-rose-300"}`}
                    >
                      {result.ok ? "✔" : "✘"} {result.name}：{result.message}
                    </li>
                  ))}
                </ul>
              )}
              {actionError && (
                <p className="text-[11px] text-rose-300 flex items-center gap-1.5" data-action-error="true">
                  <AlertCircle className="w-3.5 h-3.5" />
                  {actionError}
                </p>
              )}
              {batch && (
                <p className="text-[11px] text-slate-300" data-batch-progress="true">
                  批次 {batch.batch_id.slice(0, 8)}：
                  {batchStatus
                    ? batchProgressLabel(batchStatus.total, batchStatus.status_counts)
                    : `${batch.accepted_count} 篇已受理`}
                </p>
              )}
            </section>

            <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 space-y-2">
              <div className="flex items-center gap-2 text-sm text-slate-200">
                <BookOpen className="w-4 h-4 text-indigo-400" />
                加入已有文档（不重复上传）
              </div>
              <p className="text-[11px] text-slate-400">
                只建立组成员关系；同一文档再次加入同一组不会产生重复成员。
              </p>
              {attachable.length === 0 ? (
                <p className="text-[11px] text-slate-500">没有可加入的文档。</p>
              ) : (
                <ul className="grid grid-cols-1 md:grid-cols-2 gap-1.5" data-attachable-docs="true">
                  {attachable.slice(0, 12).map((doc) => (
                    <li
                      key={doc.id}
                      className="flex items-center justify-between gap-2 text-[11px] bg-slate-800/50 rounded-lg px-2 py-1"
                    >
                      <span className="truncate text-slate-300" title={doc.original_filename || doc.filename}>
                        #{doc.id} {doc.original_filename || doc.filename}
                      </span>
                      <button
                        type="button"
                        onClick={() => handleAttach(doc.id)}
                        className="shrink-0 px-2 py-0.5 rounded bg-slate-700 hover:bg-indigo-600 text-[10px]"
                      >
                        加入
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 space-y-3" data-group-extraction-section="true">
              <div className="flex items-center gap-2 text-sm text-slate-200">
                <BookOpen className="w-4 h-4 text-indigo-400" />
                人物图谱抽取（可选，手动触发）
              </div>
              <p className="text-[11px] text-slate-400">
                导入档案不会自动调用模型。建议先抽取一篇代表性文档，确认质量与成本后再抽取整组；
                任务可暂停、可恢复，进度与 token 消耗来自持久化台账。
              </p>
              {graphScope ? (
                <div className="rounded-xl bg-slate-800/40 p-3">
                  <ExtractionPanel scope={graphScope} onChanged={loadGraphScope} />
                </div>
              ) : (
                <p className="text-[11px] text-slate-500">图谱范围状态暂不可用。</p>
              )}
            </section>

            <section className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 space-y-3">
              <div className="flex items-center gap-2 text-sm text-slate-200">
                <BookOpen className="w-4 h-4 text-indigo-400" />
                组内文档（{members.length}）
              </div>
              {members.length === 0 ? (
                <p className="text-xs text-slate-500">该组暂无文档。导入或从“文档列表”添加后即可研究。</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs" data-group-members="true">
                    <thead className="text-slate-400">
                      <tr className="text-left">
                        <th className="py-1.5 pr-3">文档</th>
                        <th className="py-1.5 pr-3">定位</th>
                        <th className="py-1.5 pr-3">导入状态</th>
                        <th className="py-1.5 pr-3">失败原因</th>
                        <th className="py-1.5">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {members.map((member) => (
                        <tr key={member.document_id} className="border-t border-slate-800/70">
                          <td className="py-2 pr-3">
                            <div className="text-slate-200">{member.original_filename || member.filename}</div>
                            <div className="text-[10px] text-slate-500">
                              #{member.document_id} · {member.file_type} · {member.page_count} 页 ·{" "}
                              {member.block_count} 块
                            </div>
                          </td>
                          <td className="py-2 pr-3 text-slate-400">
                            <Link href={`/viewer/${member.document_id}`} className="underline hover:text-slate-200">
                              查看档案
                            </Link>
                          </td>
                          <td className="py-2 pr-3">
                            <span
                              className={`px-1.5 py-0.5 rounded border text-[10px] ${
                                TONE_CLASS[importStatusTone(member.import_status)]
                              }`}
                              data-import-status={member.import_status}
                            >
                              {importStatusLabel(member.import_status)}
                            </span>
                          </td>
                          <td className="py-2 pr-3 text-rose-300 text-[10px] max-w-[220px] truncate" title={member.import_error || ""}>
                            {member.import_error || "—"}
                          </td>
                          <td className="py-2">
                            <div className="flex items-center gap-1.5">
                              <button
                                type="button"
                                onClick={() => handleRetry(member.document_id)}
                                className="flex items-center gap-1 px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-[10px]"
                              >
                                <RotateCcw className="w-3 h-3" />
                                重试
                              </button>
                              <button
                                type="button"
                                onClick={() => handleDetach(member.document_id)}
                                className="flex items-center gap-1 px-2 py-0.5 rounded bg-slate-800 hover:bg-rose-600/40 text-[10px]"
                              >
                                <Trash2 className="w-3 h-3" />
                                移出组
                              </button>
                              <button
                                type="button"
                                onClick={() => handleExtractDocument(member.document_id)}
                                disabled={extractingDocumentId === member.document_id}
                                data-extract-document={member.document_id}
                                className="flex items-center gap-1 px-2 py-0.5 rounded bg-slate-800 hover:bg-indigo-600 text-[10px] disabled:opacity-50"
                              >
                                {extractingDocumentId === member.document_id ? (
                                  <Loader2 className="w-3 h-3 animate-spin" />
                                ) : (
                                  <BookOpen className="w-3 h-3" />
                                )}
                                抽取人物
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
