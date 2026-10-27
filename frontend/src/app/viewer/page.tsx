"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { blockApi, pageApi } from "@/lib/api";
import { ArrowLeft, BookOpen, Loader2, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

function ViewerContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const blockIdParam = searchParams.get("block_id");
  const blockId = blockIdParam ? Number(blockIdParam) : null;

  const [loading, setLoading] = useState(Boolean(blockId));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!blockId) {
      setLoading(false);
      return;
    }

    const resolveBlockLocation = async () => {
      try {
        setLoading(true);
        setError(null);
        // Look up block to find page_id
        const block = await blockApi.get(blockId);
        if (!block || !block.page_id) {
          setError(`未能定位文本块 Block #${blockId} 的物理页面信息。`);
          setLoading(false);
          return;
        }

        // Look up page to find document_id & page_number
        const page = await pageApi.get(block.page_id);
        if (!page || !page.document_id) {
          setError(`未能定位所属卷宗文档 (Page ID: ${block.page_id})。`);
          setLoading(false);
          return;
        }

        // Route directly to the document viewer with page & block_id highlighters
        router.replace(
          `/viewer/${page.document_id}?page=${page.page_number}&block_id=${blockId}`
        );
      } catch (err: any) {
        setError(
          `查询切片定位失败 (Block #${blockId}): ${err.message || "档案库中未检索到该记录"}`
        );
        setLoading(false);
      }
    };

    resolveBlockLocation();
  }, [blockId, router]);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center p-4">
        <Loader2 className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
        <p className="text-sm text-slate-300 font-mono">
          正在检索 Block #{blockId} 对应的物理卷宗与页面位置...
        </p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center p-4">
      <div className="max-w-md w-full bg-slate-900 border border-slate-800 rounded-xl p-6 text-center shadow-xl">
        {error ? (
          <div className="flex flex-col items-center gap-3">
            <div className="w-12 h-12 rounded-full bg-rose-500/10 border border-rose-500/30 flex items-center justify-center text-rose-400">
              <AlertCircle className="w-6 h-6" />
            </div>
            <h2 className="text-base font-semibold text-slate-100">档案切片定位存疑</h2>
            <p className="text-xs text-slate-400 leading-relaxed text-left bg-slate-950/60 p-3 rounded-lg border border-slate-800">
              {error}
              <br />
              <span className="text-amber-400/90 mt-2 block">
                提示：若为知识图谱空切片引用（如空白版面），请通过图谱实体或文献列表核验上下文。
              </span>
            </p>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-3">
            <div className="w-12 h-12 rounded-full bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
              <BookOpen className="w-6 h-6" />
            </div>
            <h2 className="text-base font-semibold text-slate-100">档案文献阅览器</h2>
            <p className="text-xs text-slate-400">
              请从档案卷宗列表、Graph RAG 检索结果或 AI Agent 研究报告中选择具体文献切片进入。
            </p>
          </div>
        )}

        <div className="flex items-center justify-center gap-3 mt-6">
          <Link href="/documents">
            <Button variant="outline" size="sm" className="gap-1.5 text-xs">
              <ArrowLeft className="w-3.5 h-3.5" />
              返回文档列表
            </Button>
          </Link>
          <Link href="/agent">
            <Button size="sm" className="gap-1.5 text-xs bg-indigo-600 hover:bg-indigo-500 text-white">
              AI 考据助手
            </Button>
          </Link>
        </div>
      </div>
    </div>
  );
}

export default function ViewerRootPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center p-4">
          <Loader2 className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
          <p className="text-sm text-slate-300 font-mono">正在载入档案阅读器...</p>
        </div>
      }
    >
      <ViewerContent />
    </Suspense>
  );
}
