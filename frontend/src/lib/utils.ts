import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import { TaskResult } from "@/types";

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

export function formatFileSize(bytes: number): string {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

export function formatDate(dateString: string): string {
    return new Date(dateString).toLocaleString();
}

export function getStatusColor(status: string): string {
    const colors: Record<string, string> = {
        pending: "bg-gray-100 text-gray-800",
        processing: "bg-blue-100 text-blue-800",
        running: "bg-blue-100 text-blue-800",
        ocr_completed: "bg-yellow-100 text-yellow-800",
        polishing: "bg-purple-100 text-purple-800",
        completed: "bg-green-100 text-green-800",
        failed: "bg-red-100 text-red-800",
    };
    return colors[status] || "bg-gray-100 text-gray-800";
}

export function getStatusText(status: string): string {
    const texts: Record<string, string> = {
        pending: "待处理",
        processing: "处理中",
        running: "运行中",
        ocr_completed: "OCR完成",
        polishing: "润色中",
        completed: "已完成",
        failed: "失败",
    };
    return texts[status] || status;
}

export function getOcrStageText(result?: TaskResult | null): string {
    const stage = result?.stage;
    const pageNumber = typeof result?.page_number === "number" ? result.page_number : null;
    const pageCount = typeof result?.page_count === "number" ? result.page_count : null;
    const customMessage = typeof result?.message === "string" ? result.message : null;

    if (customMessage) {
        return customMessage;
    }

    switch (stage) {
        case "queued":
            return "任务已进入队列，等待开始处理";
        case "preparing_document":
            return "正在准备文档与 OCR 环境";
        case "pdf_pages_rendered":
            return pageCount
                ? `PDF 处理较慢，请耐心等待。当前已完成预处理，准备识别 ${pageCount} 页`
                : "PDF 处理较慢，请耐心等待。已完成预处理，准备开始逐页识别";
        case "image_uploaded":
            return "图片已就绪，正在调用 OCR 服务";
        case "persisting_pages":
            if (pageNumber && pageCount) {
                return `正在保存第 ${pageNumber} / ${pageCount} 页的识别结果`;
            }
            return "正在逐页保存识别结果";
        case "finalizing_document":
            return "正在整理全文结果并完成收尾";
        case "completed":
            return "OCR 已完成，可进入阅读器查看";
        case "failed":
            return "处理失败，请检查服务日志后重试";
        default:
            return "正在处理中";
    }
}
