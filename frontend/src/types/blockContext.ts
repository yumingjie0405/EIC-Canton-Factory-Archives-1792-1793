/**
 * Stage 8 R2.4: archival block context types (read-only).
 */

export interface BlockContextBlock {
  block_id: number;
  page_id: number;
  block_index: number;
  block_label?: string | null;
  /** Untouched OCR transcription of the archival page. */
  raw_text?: string | null;
  /** Human/AI-corrected OCR of the same page (still a transcription). */
  corrected_text?: string | null;
  has_text: boolean;
}

export interface BlockContextResponse {
  document_id: number;
  document_name: string;
  page_id: number;
  page_number?: number | null;
  block: BlockContextBlock;
  previous: BlockContextBlock[];
  next: BlockContextBlock[];
  /** `same_page`: neighbours never cross a page boundary. */
  neighbour_scope: string;
  notes: string[];
}
