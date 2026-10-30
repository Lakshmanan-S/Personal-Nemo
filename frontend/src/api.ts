/**
 * Single API client module for the whole frontend: shared fetch helper, every
 * backend endpoint from plan.md section 7, and the TypeScript types that
 * mirror the FastAPI response shapes in backend/app/routes.py.
 */


export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/+$/, "");
export const PUBLIC_DEMO = import.meta.env.VITE_PUBLIC_DEMO === "true";


async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${init?.method ?? "GET"} ${path} failed (${res.status}): ${detail}`);
  }
  return res.json() as Promise<T>;
}


// ---- Shared types ----


export type PrivacyTier = "share" | "ask" | "private";


export interface Connection {
  provider: string;
  status: "disconnected" | "connected" | "demo" | "error";
  last_synced_at: string | null;
  account_email: string | null;
  pending_items: number;
}


export interface Trait {
  id: string;
  category: string;
  key: string;
  value: string;
  confidence: number;
  evidence_count: number;
  summary: string | null;
  privacy_tier: PrivacyTier;
  tier_reason: string | null;
  user_overridden: boolean;
  updated_at: string;
}


export interface TraitDetail {
  trait: Trait;
  facts: { id: string; statement: string; privacy_tier: PrivacyTier | null }[];
  evidence: {
    id: string;
    provider: string;
    item_type: string;
    occurred_at: string | null;
    payload: Record<string, unknown>;
  }[];
}


export type ProfileByCategory = Record<string, Trait[]>;


export interface HouseholdMember {
  name: string;
  relationship: string;
  likes: string;
  dislikes: string;
  dietary_needs: string;
}


export interface MyLife {
  home_city: string;
  home_area: string;
  office_city: string;
  office_area: string;
  work_arrangement: string;
  household_members: HouseholdMember[];
}


export interface QATopic {
  topic: string;
  confidence: number;
}


export interface QAStartResponse {
  session_id: string;
  topic: string;
  question: string;
  progress: { answered: number; total: number };
}


export interface QAAnswerResponse {
  session_id: string;
  done: boolean;
  question?: string;
  progress: { answered: number; total: number };
}


export interface PrivacyOverview {
  category_defaults: Record<string, PrivacyTier>;
  traits: { id: string; category: string; key: string; value: string; privacy_tier: PrivacyTier }[];
}


export interface MCPLogEntry {
  id: string;
  client_name: string | null;
  tool: string;
  traits_returned: number;
  traits_blocked: number;
  created_at: string;
}


export interface MCPApproval {
  id: string;
  client_name: string | null;
  category: string;
  status: "pending" | "approved" | "denied" | "expired" | "invalidated" | "consumed";
  expires_at: string;
  created_at: string;
  traits: { id: string; category: string; value: string; summary: string | null }[];
}


export const getMcpApprovals = () => apiFetch<MCPApproval[]>("/api/mcp/approvals");
export const decideMcpApproval = (id: string, decision: "allow" | "deny") =>
  apiFetch(`/api/mcp/approvals/${id}/decision`, { method: "POST", body: JSON.stringify({ decision }) });


export interface UsageBreakdownRow {
  model: string;
  task: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  avg_latency_ms: number;
}


export interface UsageSummary {
  breakdown: UsageBreakdownRow[];
  total_calls: number;
  total_cost_usd: number;
  hypothetical_all_ultra_cost_usd: number;
  savings_usd: number;
}


export interface Nudge {
  type: "suggestion" | "question";
  message: string;
  cached: boolean;
}


export interface CompareResult {
  baseline: string;
  personalized: string;
  used_trait_ids: string[];
  web_search_used: boolean;
  sources: WebSource[];
}


export interface WebSource {
  title: string;
  url: string;
}


function isWebSource(value: unknown): value is WebSource {
  if (typeof value !== "object" || value === null || !("title" in value) || typeof value.title !== "string"
      || !("url" in value) || typeof value.url !== "string") return false;
  try {
    const url = new URL(value.url);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password;
  } catch { return false; }
}


// ---- Connections ----


export const getConnections = () => apiFetch<Connection[]>("/api/connections");
export const connectProviderUrl = (provider: string) => `${API_BASE}/api/connect/${provider}`;
export const syncProvider = (provider: string) => apiFetch<{
  raw_items_inserted: number;
  messages_fetched?: number;
  events_fetched?: number;
  message?: string;
  limit_reached?: boolean;
}>(`/api/sync/${provider}`, { method: "POST" });
export const disconnectGoogle = (provider: "gmail" | "gcal") =>
  apiFetch<{ message: string }>(`/api/connections/${provider}`, { method: "DELETE" });
export const learnCalendar = () => apiFetch<{
  events_checked: number;
  results: { event_id: string; status: string; error?: string }[];
  message: string;
}>("/api/gcal/learn", { method: "POST", body: JSON.stringify({ consent: true }) });
export const processImportedEmail = () => apiFetch<{
  emails_processed: number;
  results: { email_id: string; type: string; status: string; error?: string }[];
  message: string;
}>("/api/gmail/process-batch", { method: "POST", body: JSON.stringify({ consent: true }) });
export interface ActivityData {
  rankings: Record<string, { name: string; count: number; fact_ids: string[]; children: { name: string; count: number }[] }[]>;
  review: { email_id: string; type: string; status: string }[];
  events: { fact_id: string; statement: string; source_count: number }[];
}
export const getActivity = () => apiFetch<ActivityData>("/api/profile/activity");
export const refreshPreferences = () => apiFetch<{ message: string }>("/api/profile/activity/refresh", { method: "POST" });
export const retryActivity = (id: string) => apiFetch<{ message: string }>(`/api/profile/activity/${id}/retry`, { method: "POST" });
export const removeActivity = (id: string) => apiFetch<{ message: string }>(`/api/profile/activity/${id}`, { method: "DELETE" });
export const loadDemoData = () => apiFetch("/api/demo/load", { method: "POST" });


export interface TodayItem {
  id: string;
  kind: "action" | "reminder";
  title: string;
  detail: string;
  day: string;
  at: string | null;
  source_url: string | null;
  source: "gmail" | "gcal";
  all_day: boolean;
}


export interface TodayData {
  date: string;
  timezone: string;
  items: TodayItem[];
  action_count: number;
  reminder_count: number;
  pending_emails: number;
  failed_scans: number;
}


export const getToday = (timezone: string, signal?: AbortSignal) =>
  apiFetch<TodayData>(`/api/today?timezone_name=${encodeURIComponent(timezone)}`, { signal });
export const processToday = (timezone: string) => apiFetch<{
  emails_checked: number;
  results: { email_id: string; status: string; error?: string; unmatched_updates?: number }[];
  message: string;
}>("/api/today/process", { method: "POST", body: JSON.stringify({ consent: true, timezone_name: timezone }) });
export const updateTodayItem = (id: string, action: "done" | "dismiss" | "snooze") =>
  apiFetch<{ message: string }>(`/api/today/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ action }) });


// ---- Profile ----


export const getProfile = () => apiFetch<ProfileByCategory>("/api/profile");
export const getMyLife = () => apiFetch<MyLife>("/api/profile/my-life");
export const saveMyLife = (data: MyLife) =>
  apiFetch<MyLife>("/api/profile/my-life", { method: "PUT", body: JSON.stringify(data) });
export const getTrait = (id: string) => apiFetch<TraitDetail>(`/api/traits/${id}`);
export const patchTrait = (id: string, body: { value?: string; privacy_tier?: PrivacyTier }) =>
  apiFetch<Trait>(`/api/traits/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteTrait = (id: string) => apiFetch(`/api/traits/${id}`, { method: "DELETE" });
export const rebuildProfile = () => apiFetch("/api/profile/rebuild", { method: "POST" });


// ---- Chat ----


export async function sendChatMessage(
  message: string,
  mode: "personalized" | "baseline",
  onChunk: (text: string) => void,
  webQuery?: string,
): Promise<{ usedTraitIds: string[]; webSearchUsed: boolean; sources: WebSource[] }> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, mode, web_query: webQuery }),
  });
  if (!res.ok || !res.body) {
    throw new Error(`Chat request failed (${res.status}): ${await res.text()}`);
  }
  const usedTraitIds = (res.headers.get("X-Used-Trait-Ids") ?? "").split(",").filter(Boolean);
  const sources: unknown = JSON.parse(res.headers.get("X-Web-Sources") ?? "[]");
  if (!Array.isArray(sources) || sources.length > 3 || !sources.every(isWebSource))
    throw new Error("Chat returned invalid web-source metadata.");
  const webSearchUsed = res.headers.get("X-Web-Search-Used") === "true";
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk(decoder.decode(value, { stream: true }));
  }
  return { usedTraitIds, webSearchUsed, sources };
}


export const compareChat = (message: string, webQuery?: string) =>
  apiFetch<CompareResult>("/api/chat/compare", { method: "POST", body: JSON.stringify({ message, web_query: webQuery }) });


// ---- Know Me Better (qa) ----


export const getQaTopics = () => apiFetch<QATopic[]>("/api/qa/topics");
export const startQaSession = (topic: string) =>
  apiFetch<QAStartResponse>("/api/qa/start", { method: "POST", body: JSON.stringify({ topic }) });
export const answerQaQuestion = (sessionId: string, answer: string) =>
  apiFetch<QAAnswerResponse>("/api/qa/answer", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, answer }),
  });


// ---- Privacy and MCP ----


export const getPrivacy = () => apiFetch<PrivacyOverview>("/api/privacy");
export const setCategoryTier = (category: string, tier: PrivacyTier) =>
  apiFetch(`/api/privacy/category/${category}`, { method: "PUT", body: JSON.stringify({ tier }) });
export const generateMcpToken = () => apiFetch<{ token: string; note: string }>("/api/mcp/token", { method: "POST" });
export const getMcpLog = () => apiFetch<MCPLogEntry[]>("/api/mcp/log");
export const mcpServerUrl = () => (import.meta.env.VITE_MCP_SERVER_URL as string | undefined) ?? "http://localhost:8800/mcp";


// ---- Usage / nudge ----


export const getUsage = () => apiFetch<UsageSummary>("/api/usage");
export const getNudge = () => apiFetch<Nudge>("/api/nudge");


// ---- Account ----


export const deleteAllData = () => apiFetch("/api/account/data", { method: "DELETE" });




