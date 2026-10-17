/** Shared API types mirroring backend/app/schemas.py */
export type ConnectorStatus = "ONLINE" | "OFFLINE";

export interface Agent {
  id: string;
  agent_name: string;
  agent_type: string;
  current_injected_skills_json: { skill_id: string; injected_at: string; path?: string }[];
}

export interface Connector {
  id: string;
  machine_name: string;
  status: ConnectorStatus;
  last_heartbeat: string | null;
  created_at: string;
  agents: Agent[];
  api_key?: string;
}

export interface SkillPayload {
  schema_version: string;
  skill_id: string;
  name: string;
  niche: string;
  generated_by: string;
  created_at: string;
  system_prompt: string;
  prompt_templates: { name: string; template: string; variables: string[] }[];
  tools: { name: string; description: string; parameters: Record<string, unknown> }[];
  memory: { type: string; window: number; seed_facts: string[] };
  guardrails: string[];
  evaluation: { input: string; expected_behavior: string }[];
}

export type TrainingStatus = "PENDING" | "TRAINING" | "READY" | "APPROVED" | "FAILED";

export interface TrainingRequest {
  id: string;
  status: TrainingStatus;
  token_cost: number;
  niche_requirements_text: string;
  target_connector_id: string | null;
  target_agent_name: string;
  generated_payload_json: SkillPayload | null;
  generator: string | null;
  error_message: string | null;
  created_at: string;
  approved_at: string | null;
}

export interface User {
  id: string;
  email: string;
  token_balance: number;
  created_at: string;
}

/** Any event pushed over /ws/dashboard */
export interface HubEvent {
  type: string;
  connector_id?: string;
  task_id?: string;
  stream?: "stdout" | "stderr";
  line?: string;
  status?: string;
  exit_code?: number;
  duration_ms?: number;
  error?: string | null;
  [key: string]: unknown;
}
