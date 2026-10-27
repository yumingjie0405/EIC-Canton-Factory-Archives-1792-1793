"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { documentApi } from "@/lib/api";
import { Upload, FileText, X, ArrowRight, FileImage, Loader2, Clock3 } from "lucide-react";
import { cn, formatFileSize } from "@/lib/utils";

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const droppedFile = e.dataTransfer.files[0];
    if (droppedFile) {
      validateAndSetFile(droppedFile);
    }
  }, []);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile) {
      validateAndSetFile(selectedFile);
    }
  }, []);

  const validateAndSetFile = (selectedFile: File) => {
    setError(null);
    const validTypes = [
      "application/pdf",
      "image/png",
      "image/jpeg",
      "image/jpg",
      "image/gif",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "application/msword",
    ];
    const isDocx = /\.docx$/i.test(selectedFile.name);
    if (!validTypes.includes(selectedFile.type) && !isDocx) {
      setError("请上传 PDF、图片 (PNG, JPG, GIF) 或 Word (.docx) 文件");
      return;
    }
    if (/\.doc$/i.test(selectedFile.name)) {
      setError("旧版 .doc 暂不支持，请另存为 .docx 后上传");
      return;
    }
    if (selectedFile.size > 100 * 1024 * 1024) {
      setError("文件大小不能超过 100MB");
      return;
    }
    setFile(selectedFile);
  };

  const handleUpload = async () => {
    if (!file) return;

    setIsUploading(true);
    setUploadProgress(15);
    setError(null);

    try {
      const document = await documentApi.upload(file);
      setUploadProgress(100);
      router.push(`/documents/${document.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
      setIsUploading(false);
    }
  };

  const clearFile = () => {
    setFile(null);
    setError(null);
  };

  return (
    <div className="app-shell py-10 md:py-14">
      <div className="grid gap-8 lg:grid-cols-[0.75fr_1fr]">
        <div className="space-y-6">
          <div>
            <p className="mb-3 text-sm font-medium uppercase tracking-[0.24em] text-primary/70">Upload Workspace</p>
            <h1 className="section-title">上传待识别文档</h1>
            <p className="section-description mt-3">
              支持 PDF、常见图片与 Word (.docx) 格式。PDF/图片进入 OCR 与版面分析流程；Word 文档直接读取其文字段落，无需 OCR。
            </p>
          </div>

          <div className="soft-card p-6">
            <div className="flex items-start gap-4">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <FileImage className="h-6 w-6" />
              </div>
              <div className="space-y-2 text-sm text-slate-600">
                <p className="font-medium text-slate-900">建议上传高质量扫描件</p>
                <p>清晰的页面边缘、足够的对比度和完整的页码结构能提升 OCR 与版面解析质量。</p>
              </div>
            </div>
          </div>
        </div>

        <Card className="soft-card border-0">
          <CardHeader className="space-y-2 pb-6">
            <CardTitle className="text-2xl text-slate-900">导入文件</CardTitle>
            <CardDescription className="text-base text-slate-600">
              支持 PDF、图片 (PNG, JPG, GIF) 与 Word (.docx)，最大 100MB
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!file ? (
              <div
                className={cn(
                  "rounded-[2rem] border-2 border-dashed p-10 text-center transition-all",
                  isDragging
                    ? "border-primary bg-primary/5 shadow-lg shadow-primary/10"
                    : "border-slate-200 bg-slate-50/70 hover:border-primary/40 hover:bg-white"
                )}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => document.getElementById("file-input")?.click()}
              >
                <input
                  id="file-input"
                  type="file"
                  className="hidden"
                  accept=".pdf,.png,.jpg,.jpeg,.gif,.docx"
                  onChange={handleFileSelect}
                />
                <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-3xl bg-primary/10 text-primary">
                  <Upload className="h-8 w-8" />
                </div>
                <p className="text-xl font-semibold text-slate-900">拖拽文件到此处，或点击选择</p>
                <p className="mt-2 text-sm text-slate-500">支持 PDF、PNG、JPG、GIF、DOCX 格式</p>
              </div>
            ) : (
              <div className="space-y-5">
                <div className="flex items-center justify-between rounded-[1.5rem] border border-slate-200 bg-slate-50/80 p-4">
                  <div className="flex items-center gap-4">
                    <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                      <FileText className="h-6 w-6" />
                    </div>
                    <div>
                      <p className="font-medium text-slate-900">{file.name}</p>
                      <p className="text-sm text-slate-500">{formatFileSize(file.size)}</p>
                    </div>
                  </div>
                  {!isUploading && (
                    <Button variant="ghost" size="icon" onClick={clearFile}>
                      <X className="h-4 w-4" />
                    </Button>
                  )}
                </div>

                {isUploading && (
                  <div className="space-y-3 rounded-[1.5rem] border border-primary/15 bg-primary/5 p-4">
                    <Progress value={uploadProgress} />
                    <div className="flex items-center justify-between text-sm text-slate-600">
                      <span className="inline-flex items-center gap-2">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        正在上传文档...
                      </span>
                      <span>{uploadProgress}%</span>
                    </div>
                    <div className="rounded-xl bg-white/80 p-3 text-sm text-slate-600 shadow-sm">
                      <div className="mb-1 inline-flex items-center gap-2 text-slate-900">
                        <Clock3 className="h-4 w-4 text-primary" />
                        接下来
                      </div>
                      <p>
                        上传完成后将跳转到文档详情页，那里会展示真实 OCR 处理进度与页数阶段信息。
                      </p>
                    </div>
                  </div>
                )}

                {!isUploading && (
                  <Button className="w-full rounded-full py-6 text-base shadow-lg shadow-primary/20" size="lg" onClick={handleUpload}>
                    开始上传并进入处理流程
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </Button>
                )}
              </div>
            )}

            {error && (
              <div className="mt-4 rounded-2xl border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
                {error}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
