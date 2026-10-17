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

export interface Plan {
  id: "FREE" | "PRO" | "BUSINESS" | string;
  name: string;
  price_monthly_cents: number;
  currency: string;
  monthly_tokens: number;
  max_machines: number;
  max_tasks_per_day: number;
  max_webhooks: number;
  max_api_keys: number;
  max_bots?: number;
  max_tunnels?: number;
  training_access: boolean;
  inbound_webhooks: boolean;
  features_json: string[];
  sort_order: number;
}

export interface User {
  id: string;
  email: string;
  token_balance: number;
  created_at: string;
  role: "user" | "admin";
  status: "ACTIVE" | "SUSPENDED" | "BANNED";
  plan_id: string;
  subscription_status: "ACTIVE" | "PENDING_UPGRADE" | "PAST_DUE" | "CANCELED";
  current_period_start: string | null;
  current_period_end: string | null;
  tokens_used_period: number;
  last_login_at?: string | null;
  plan?: Plan;
}

export interface Usage {
  machines: number;
  webhooks: number;
  api_keys: number;
  tasks_today: number;
  tokens_used_period: number;
  token_balance: number;
}

export interface UpgradeRequest {
  id: string;
  user_id: string;
  from_plan_id: string;
  requested_plan_id: string;
  status: "PENDING" | "APPROVED" | "REJECTED" | "CANCELED";
  payment_provider: string;
  payment_reference: string | null;
  amount_cents: number;
  months: number;
  user_note: string | null;
  admin_note: string | null;
  created_at: string;
  decided_at: string | null;
  user_email?: string | null;
}

export interface Subscription {
  user: User;
  plan: Plan;
  usage: Usage;
  pending_request: UpgradeRequest | null;
  support_whatsapp: string;
  whatsapp_url: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  api_key?: string;
}

export interface Webhook {
  id: string;
  url: string;
  events_json: string[];
  description: string | null;
  is_active: boolean;
  created_at: string;
  secret: string;
}

export interface Delivery {
  id: string;
  webhook_id: string;
  event: string;
  status: "PENDING" | "SUCCESS" | "FAILED";
  attempts: number;
  response_code: number | null;
  response_body: string | null;
  error: string | null;
  payload_json: unknown;
  created_at: string;
  delivered_at: string | null;
  next_attempt_at: string | null;
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

// ---------------------------------------------------------------- Bot API + Marketplace
export interface BotGroup { master_id: string; master: string; status: string; master_clone: boolean; error: string | null }
export interface Bot {
  id: string; name: string; framework: string; delivery: "poll" | "webhook" | "websocket" | "connector";
  webhook_url: string | null; connector_id: string | null; agent_name: string; token_prefix: string; status: string;
  persona: string | null; skills: { skill_id: string; name?: string; master?: string; installed_at?: string }[];
  online: boolean; last_seen_at: string | null; created_at: string; groups?: BotGroup[];
}
export interface BotMsg {
  update_id: number; kind: string; chat: string; group_id: string | null; sender: string; text: string;
  payload: unknown; reply_to: number | null; direction: "in" | "out"; created_at: string; acked: boolean;
}
export interface Master {
  id: string; slug: string; name: string; avatar: string; tagline: string; description: string; persona: string;
  min_plan_id: string; min_plan_name: string; is_published: boolean; members: number; graduated: number;
  skill_id: string; skill_name: string | null; tools: string[]; test_prompt: string; created_at: string;
  skill_payload?: Record<string, unknown>; tools_json?: Record<string, unknown>[]; welcome_message?: string; test_expect?: string | null;
}
export interface Membership {
  id: string; master_id: string; master: string | null; bot_id: string; bot: string | null; status: string;
  master_clone: boolean; skill_id: string | null; error?: string | null; joined_at: string; graduated_at: string | null;
  mine?: boolean; framework?: string | null;
}

export interface Tunnel {
  id: string;
  slug: string;
  name: string;
  connector_id: string;
  machine_name: string | null;
  local_port: number;
  protected: boolean;
  is_active: boolean;
  online: boolean;
  public_url: string;
  requests_count: number;
  bytes_in: number;
  bytes_out: number;
  last_status: number | null;
  last_used_at: string | null;
  created_at: string;
}
