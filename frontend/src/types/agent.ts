/**
 * Stage 7: AI Agent Dynamic Workflow and Research Interaction Types
 */
import {
  AnswerGeneration,
  HistoryLink,
  RagEntityReference,
  RagRelationshipReference,
  RagSourceEvidence,
} from "./rag";

export interface ToolDefinition {
  name: string;
  description: string;
  parameters: Record<string, any>;
}

export interface ToolCallRecord {
  call_id: string;
  tool_name: string;
  arguments: Record<string, any>;
  status: "success" | "error" | "intercepted";
  output_summary: string;
  latency_ms: number;
  evidence_count: number;
  error_message?: string | null;
}

export interface AgentStepRecord {
  step_number: number;
  sub_goal: string;
  thought: string;
  tool_calls: ToolCallRecord[];
  step_status: "pending" | "running" | "completed" | "failed";
}

export interface AnswerCategorization {
  archival_facts: string[];
  kg_relationships: string[];
  external_scholarship: string[];
  inferences_and_synthesis: string[];
  unresolved_aspects: string[];
}

export interface AgentResearchRequest {
  /** Document group to research; resolved server-side. */
  group_id?: number;
  question: string;
  max_steps?: number;
  max_tool_calls?: number;
  time_filter?: number | null;
  temperature?: number;
  include_trajectory?: boolean;
}

export interface AgentBudgetUsed {
  steps_executed: number;
  max_steps: number;
  tool_calls_executed: number;
  max_tool_calls: number;
  elapsed_ms: number;
  budget_exhausted: boolean;
}

export interface AgentResearchResponse {
  group_id?: number | null;
  group_name?: string | null;
  task_id: string;
  question: string;
  final_answer: string;
  categorization: AnswerCategorization;
  trajectory: AgentStepRecord[];
  sources: RagSourceEvidence[];
  entities: RagEntityReference[];
  relationships: RagRelationshipReference[];
  evidence_sufficient: boolean;
  budget_used: AgentBudgetUsed;
  warnings: string[];
  generation?: AnswerGeneration;
  history?: HistoryLink;
}

export interface AgentPlanResponse {
  question: string;
  proposed_steps: string[];
  target_entities: string[];
  time_filter?: number | null;
  estimated_tool_calls: number;
}
