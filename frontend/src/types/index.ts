// Document types
export interface Document {
  archived_at?: string | null;
  processing_summary?: {
    text_blocks: number;
    polished_blocks: number;
    polish_status: string;
  };
  id: number;
  filename: string;
  original_filename: string;
  file_type: "pdf" | "image" | "docx";
  file_size: number;
  page_count: number;
  full_markdown?: string;
  status: DocumentStatus;
  error_message?: string;
  created_at: string;
  updated_at: string;
  pages?: PageBrief[];
}

export type DocumentStatus =
  | "pending"
  | "processing"
  | "ocr_completed"
  | "polishing"
  | "completed"
  | "failed";

export interface DocumentListResponse {
  items: Document[];
  total: number;
  page: number;
  page_size: number;
}

// Page types
export interface PageBrief {
  id: number;
  page_number: number;
  image_path?: string;
  markdown_content?: string;
  width?: number;
  height?: number;
}

export interface Page {
  original_page_label?: string;
  folio_side?: string;
  id: number;
  document_id: number;
  page_number: number;
  image_path?: string;
  visualization_path?: string;
  markdown_content?: string;
  pruned_result_path?: string;
  width?: number;
  height?: number;
  created_at: string;
  updated_at: string;
  blocks?: Block[];
}

// Block types
export interface Block {
  id: number;
  page_id: number;
  block_index: number;
  block_label?: string;
  bbox: number[];
  confidence?: number;
  ocr_text_raw?: string;
  ocr_text_corrected?: string;
  modern_english?: string;
  translation_zh?: string;
  review_status: ReviewStatus;
  created_at: string;
  updated_at: string;
}

export type ReviewStatus = "pending" | "approved" | "rejected" | "needs_review";

export interface BlockListResponse {
  items: Block[];
  total: number;
}

// Task types
export interface Task {
  id: number;
  celery_task_id?: string;
  document_id?: number;
  page_id?: number;
  block_id?: number;
  task_type: TaskType;
  status: TaskStatus;
  progress: number;
  result?: TaskResult;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  updated_at: string;
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  calls_with_usage: number;
}

export interface TaskResult {
  stage?: string;
  scope?: "block" | "page" | "document";
  page_count?: number;
  page_number?: number;
  block_index?: number;
  processed?: number;
  skipped?: number;
  total?: number;
  processed_pages?: number;
  skipped_pages?: number;
  llm_calls?: number;
  failed_page_number?: number;
  token_usage?: TokenUsage;
  upload_completed?: boolean;
  message?: string;
  [key: string]: unknown;
}

export type TaskType =
  "ocr" | "ocr_correction" | "modern_english" | "translation_zh" | "export";

export type TaskStatus =
  "pending" | "running" | "completed" | "failed" | "cancelled";

// Export types
export interface Export {
  id: number;
  document_id: number;
  format: ExportFormat;
  status: ExportStatus;
  file_path?: string;
  file_size?: number;
  created_at: string;
  updated_at: string;
}

export type ExportFormat = "json" | "markdown" | "txt" | "docx" | "pdf";
export type ExportStatus = "pending" | "processing" | "completed" | "failed";

export interface ExportListResponse {
  items: Export[];
  total: number;
}

// Request types
export interface RunOCRRequest {
  visualize?: boolean;
  restructure_pages?: boolean;
  merge_tables?: boolean;
  relevel_titles?: boolean;
}

export interface RunPolishRequest {
  modes?: string[];
}

export interface ExportRequest {
  format: ExportFormat;
  text_mode?: "ocr_raw" | "llm_polished";
}

export interface BlockUpdateRequest {
  block_label?: string;
  ocr_text_raw?: string;
  ocr_text_corrected?: string;
  modern_english?: string;
  translation_zh?: string;
  review_status?: ReviewStatus;
}

export * from "./kg";

