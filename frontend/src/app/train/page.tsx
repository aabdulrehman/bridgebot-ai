"use client";
/**
 * "Train My Bot" — multi-step niche submission + skill approval preview.
 * Flow: Business → Requirements → Goals & tone → Target → submit → (TRAINING) → READY preview → Approve & inject.
 */
import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import StatusDot from "@/components/StatusDot";
import { api } from "@/lib/api";
import { useHubSocket } from "@/lib/useHubSocket";
import type { Connector, HubEvent, TrainingRequest } from "@/lib/types";

const STEPS = ["Business", "Requirements", "Goals & tone", "Target agent", "Review"] as const;
const TONES = ["professional and friendly", "warm and empathetic", "concise and technical", "playful", "formal"];

interface FormState {
  business_name: string;
  niche_requirements_text: string;
  goals: string[];
  tone: string;
  target_connector_id: string;
  target_agent_name: string;
}

const EMPTY: FormState = { business_name: "", niche_requirements_text: "", goals: [], tone: TONES[0], target_connector_id: "", target_agent_name: "default" };

export default function TrainPage() {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [goalDraft, setGoalDraft] = useState("");
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [requests, setRequests] = useState<TrainingRequest[]>([]);
  const [active, setActive] = useState<TrainingRequest | null>(null);
  const [tab, setTab] = useState<"overview" | "tools" | "json">("overview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [balanceKey, setBalanceKey] = useState(0);

  const loadAll = useCallback(async () => {
    try {
      const [c, r] = await Promise.all([api<Connector[]>("/api/connectors"), api<TrainingRequest[]>("/api/training")]);
      setConnectors(c);
      setRequests(r);
      setForm((f) => (f.target_connector_id || !c[0] ? f : { ...f, target_connector_id: c[0].id }));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const refreshActive = useCallback(async (id: string) => {
    const tr = await api<TrainingRequest>(`/api/training/${id}`);
    setActive(tr);
    setRequests((prev) => [tr, ...prev.filter((p) => p.id !== tr.id)]);
    if (tr.status === "READY") setBalanceKey((k) => k + 1);
    return tr;
  }, []);

  // Live updates (fallback polling below in case the socket is down)
  useHubSocket((ev: HubEvent) => {
    if (ev.type === "TRAINING_STATUS" && active && ev.training_request_id === active.id) refreshActive(active.id);
    if (ev.type === "CONNECTOR_STATUS") loadAll();
    if (ev.type === "SKILL_ACK" && active && ev.request_id === active.id) {
      setNotice(ev.ok ? `✅ Skill installed on your machine at ${String(ev.path)}` : `❌ Agent rejected skill: ${String(ev.error)}`);
    }
  });

  useEffect(() => {
    if (!active || !["PENDING", "TRAINING"].includes(active.status)) return;
    const t = setInterval(() => refreshActive(active.id).catch(() => undefined), 2500);
    return () => clearInterval(t);
  }, [active, refreshActive]);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));
  const addGoal = () => {
    const g = goalDraft.trim();
    if (g && form.goals.length < 20) set("goals", [...form.goals, g]);
    setGoalDraft("");
  };

  const canNext = [
    form.business_name.trim().length > 0,
    form.niche_requirements_text.trim().length >= 10,
    true,
    true,
    true,
  ][step];

  const submit = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const tr = await api<TrainingRequest>("/api/training/submit", {
        method: "POST",
        json: { ...form, target_connector_id: form.target_connector_id || null },
      });
      setActive(tr);
      setStep(0);
      setForm({ ...EMPTY, target_connector_id: form.target_connector_id });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!active) return;
    setBusy(true);
    setError(null);
    // Set the "waiting" notice BEFORE the request: the SKILL_ACK can arrive over
    // the WebSocket before the HTTP response, and must not be overwritten.
    setNotice("📦 Payload sent over the bridge — waiting for the agent to confirm…");
    try {
      const tr = await api<TrainingRequest>("/api/training/approve", {
        method: "POST",
        json: { training_request_id: active.id, target_connector_id: active.target_connector_id || form.target_connector_id || null, target_agent_name: active.target_agent_name },
      });
      setActive(tr);
      loadAll();
    } catch (e) {
      setNotice(null);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const payload = active?.generated_payload_json;
  const targetOnline = connectors.find((c) => c.id === (active?.target_connector_id || form.target_connector_id))?.status === "ONLINE";

  return (
    <Shell balanceKey={balanceKey}>
      <div className="grid gap-4 lg:grid-cols-2">
        {/* ------------------------------------------------ multi-step form */}
        <section className="card space-y-4">
          <div>
            <h1 className="text-lg font-bold">🧠 Train My Bot</h1>
            <p className="text-sm text-slate-400">Describe your niche. Our Master Bot compiles prompts, tools & memory using <b>platform</b> tokens — not yours.</p>
          </div>
          <ol className="flex gap-1">
            {STEPS.map((s, i) => (
              <li key={s} className={`h-1.5 flex-1 rounded-full ${i <= step ? "bg-emerald-500" : "bg-slate-800"}`} title={s} />
            ))}
          </ol>
          <p className="text-xs font-semibold text-emerald-300">Step {step + 1}/{STEPS.length}: {STEPS[step]}</p>

          {step === 0 && (
            <div>
              <label className="label" htmlFor="biz">Business / project name</label>
              <input id="biz" className="input" placeholder="Riyadh Homes Real Estate" value={form.business_name} onChange={(e) => set("business_name", e.target.value)} />
            </div>
          )}
          {step === 1 && (
            <div>
              <label className="label" htmlFor="niche">Niche & requirements</label>
              <textarea id="niche" className="input min-h-40" placeholder="What does your business do, who are your customers, what should the agent handle, which tools/data it can use…"
                value={form.niche_requirements_text} onChange={(e) => set("niche_requirements_text", e.target.value)} />
              <p className="mt-1 text-xs text-slate-500">{form.niche_requirements_text.length}/8000 · min 10 characters</p>
            </div>
          )}
          {step === 2 && (
            <div className="space-y-3">
              <div>
                <label className="label" htmlFor="goal">Goals</label>
                <div className="flex gap-2">
                  <input id="goal" className="input" placeholder="Book property viewings" value={goalDraft} onChange={(e) => setGoalDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addGoal(); } }} />
                  <button type="button" className="btn-ghost" onClick={addGoal}>Add</button>
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {form.goals.map((g, i) => (
                    <span key={i} className="rounded-md bg-slate-800 px-2 py-1 text-xs">
                      {g} <button className="ml-1 text-slate-500" onClick={() => set("goals", form.goals.filter((_, j) => j !== i))}>×</button>
                    </span>
                  ))}
                </div>
              </div>
              <div>
                <label className="label" htmlFor="tone">Tone</label>
                <select id="tone" className="input" value={form.tone} onChange={(e) => set("tone", e.target.value)}>
                  {TONES.map((t) => <option key={t}>{t}</option>)}
                </select>
              </div>
            </div>
          )}
          {step === 3 && (
            <div className="space-y-3">
              <div>
                <label className="label" htmlFor="target">Target machine</label>
                <select id="target" className="input" value={form.target_connector_id} onChange={(e) => set("target_connector_id", e.target.value)}>
                  <option value="">Choose later</option>
                  {connectors.map((c) => <option key={c.id} value={c.id}>{c.machine_name} ({c.status})</option>)}
                </select>
              </div>
              <div>
                <label className="label" htmlFor="agent">Agent name on that machine</label>
                <input id="agent" className="input" list="agent-names" value={form.target_agent_name} onChange={(e) => set("target_agent_name", e.target.value)} />
                <datalist id="agent-names">
                  {connectors.find((c) => c.id === form.target_connector_id)?.agents.map((a) => <option key={a.id} value={a.agent_name} />)}
                </datalist>
              </div>
            </div>
          )}
          {step === 4 && (
            <dl className="space-y-2 text-sm">
              <div><dt className="label">Business</dt><dd>{form.business_name}</dd></div>
              <div><dt className="label">Requirements</dt><dd className="whitespace-pre-wrap text-slate-300">{form.niche_requirements_text}</dd></div>
              <div><dt className="label">Goals</dt><dd>{form.goals.join(", ") || "—"}</dd></div>
              <div><dt className="label">Tone</dt><dd>{form.tone}</dd></div>
              <div><dt className="label">Target</dt><dd>{connectors.find((c) => c.id === form.target_connector_id)?.machine_name ?? "choose later"} / {form.target_agent_name}</dd></div>
            </dl>
          )}

          {error && <p className="rounded-lg bg-red-950/50 p-2 text-sm text-red-300">{error}</p>}
          <div className="flex justify-between">
            <button className="btn-ghost" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>← Back</button>
            {step < STEPS.length - 1 ? (
              <button className="btn-primary" disabled={!canNext} onClick={() => setStep((s) => s + 1)}>Next →</button>
            ) : (
              <button className="btn-primary" disabled={busy} onClick={submit}>{busy ? "Submitting…" : "🚀 Compile skill"}</button>
            )}
          </div>
        </section>

        {/* ------------------------------------------------ approval preview */}
        <section className="card space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-bold">Skill preview & approval</h2>
            {active && <StatusDot online={active.status === "READY" || active.status === "APPROVED"} label={active.status} />}
          </div>
          {!active && <p className="text-sm text-slate-400">Submit a niche or pick a previous request below.</p>}
          {active && ["PENDING", "TRAINING"].includes(active.status) && (
            <div className="flex items-center gap-3 rounded-xl bg-slate-950 p-4 text-sm text-slate-300">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-emerald-400 border-t-transparent" /> Master Bot is compiling your skill…
            </div>
          )}
          {active?.status === "FAILED" && <p className="rounded-lg bg-red-950/50 p-2 text-sm text-red-300">Failed: {active.error_message}</p>}
          {payload && (
            <>
              <div className="text-sm">
                <p className="font-semibold text-emerald-300">{payload.name}</p>
                <p className="text-xs text-slate-500">{payload.skill_id} · by {active?.generator} · cost {active?.token_cost} platform tokens</p>
              </div>
              <div className="flex gap-1 text-xs">
                {(["overview", "tools", "json"] as const).map((t) => (
                  <button key={t} onClick={() => setTab(t)} className={`rounded-lg px-3 py-1 capitalize ${tab === t ? "bg-slate-800 text-emerald-300" : "text-slate-400"}`}>{t}</button>
                ))}
              </div>
              <div className="max-h-[45dvh] overflow-y-auto rounded-xl bg-slate-950 p-3 text-xs">
                {tab === "overview" && (
                  <div className="space-y-3">
                    <div><p className="label">System prompt</p><p className="whitespace-pre-wrap text-slate-300">{payload.system_prompt}</p></div>
                    <div><p className="label">Memory ({payload.memory?.type}, window {payload.memory?.window})</p>
                      <ul className="list-disc pl-4 text-slate-300">{payload.memory?.seed_facts?.map((f, i) => <li key={i}>{f}</li>)}</ul></div>
                    <div><p className="label">Guardrails</p><ul className="list-disc pl-4 text-slate-300">{payload.guardrails?.map((g, i) => <li key={i}>{g}</li>)}</ul></div>
                    <div><p className="label">Prompt templates</p>{payload.prompt_templates?.map((t) => (
                      <p key={t.name} className="mb-1"><b className="text-sky-300">{t.name}</b>: <span className="text-slate-400">{t.template}</span></p>))}</div>
                  </div>
                )}
                {tab === "tools" && payload.tools?.map((t) => (
                  <div key={t.name} className="mb-3">
                    <p className="font-mono text-sky-300">{t.name}()</p>
                    <p className="text-slate-400">{t.description}</p>
                    <pre className="mt-1 overflow-x-auto text-[11px] text-slate-500">{JSON.stringify(t.parameters, null, 2)}</pre>
                  </div>
                ))}
                {tab === "json" && <pre className="overflow-x-auto text-[11px] text-slate-300">{JSON.stringify(payload, null, 2)}</pre>}
              </div>
              {active?.status === "READY" && (
                <div className="space-y-2">
                  {!targetOnline && <p className="text-xs text-amber-300">Target machine is offline — start local_connector.py to inject.</p>}
                  <button className="btn-primary w-full" disabled={busy || !targetOnline} onClick={approve}>✅ Approve & inject into {active.target_agent_name}</button>
                </div>
              )}
            </>
          )}
          {notice && <p className="rounded-lg bg-emerald-950/50 p-2 text-sm text-emerald-300">{notice}</p>}

          {requests.length > 0 && (
            <div className="border-t border-slate-800 pt-3">
              <p className="label">History</p>
              <ul className="space-y-1">
                {requests.map((r) => (
                  <li key={r.id}>
                    <button onClick={() => { setActive(r); setNotice(null); }} className={`flex w-full items-center justify-between rounded-lg px-2 py-1 text-left text-xs hover:bg-slate-800 ${active?.id === r.id ? "bg-slate-800" : ""}`}>
                      <span className="truncate">{r.generated_payload_json?.name ?? r.niche_requirements_text.slice(0, 40)}</span>
                      <span className="ml-2 shrink-0 text-slate-500">{r.status}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>
    </Shell>
  );
}
