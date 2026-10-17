"use client";
/** Admin: publish / edit / unpublish Master Bots in the marketplace. */
import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Master } from "@/lib/types";

const EMPTY = {
  name: "", avatar: "🤖", tagline: "", description: "", persona: "", min_plan_id: "FREE",
  welcome_message: "Welcome {bot}! I'm {master}. Installing my skill package into you now, then a quick test.",
  test_prompt: "Onboarding test: reply in one sentence describing your new specialty.", test_expect: "",
  skill_payload: JSON.stringify({ skill_id: "", name: "", system_prompt: "", prompt_templates: [], tools: [], guardrails: [] }, null, 2),
  tools: "[]",
};

export default function AdminMarketplace() {
  const [rows, setRows] = useState<Master[]>([]);
  const [form, setForm] = useState(EMPTY);
  const [editing, setEditing] = useState<string | null>(null);
  const [trainingId, setTrainingId] = useState("");
  const [trainingPlan, setTrainingPlan] = useState("PRO");
  const [msg, setMsg] = useState<string | null>(null);
  const load = useCallback(() => api<Master[]>("/api/admin/marketplace").then(setRows).catch((e) => setMsg(e.message)), []);
  useEffect(() => { load(); }, [load]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    let skill_payload: unknown, tools: unknown;
    try { skill_payload = JSON.parse(form.skill_payload); tools = JSON.parse(form.tools); } catch { setMsg("Skill payload / tools must be valid JSON"); return; }
    const body = { ...form, skill_payload, tools, test_expect: form.test_expect || null };
    try {
      if (editing) await api(`/api/admin/marketplace/${editing}`, { method: "PATCH", json: body });
      else await api("/api/admin/marketplace", { method: "POST", json: { ...body, is_published: true } });
      setMsg(editing ? "Saved." : "Published ✓");
      setForm(EMPTY); setEditing(null); load();
    } catch (err) { setMsg((err as Error).message); }
  };
  const edit = (m: Master) => {
    setEditing(m.id);
    setForm({
      name: m.name, avatar: m.avatar, tagline: m.tagline, description: m.description, persona: m.persona, min_plan_id: m.min_plan_id,
      welcome_message: m.welcome_message ?? "", test_prompt: m.test_prompt, test_expect: m.test_expect ?? "",
      skill_payload: JSON.stringify(m.skill_payload ?? {}, null, 2), tools: JSON.stringify(m.tools_json ?? [], null, 2),
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const toggle = async (m: Master) => { await api(`/api/admin/marketplace/${m.id}`, { method: "PATCH", json: { is_published: !m.is_published } }).catch((e) => setMsg(e.message)); load(); };
  const del = async (m: Master) => { if (!confirm(`Delete ${m.name}? Its group and memberships are removed.`)) return; await api(`/api/admin/marketplace/${m.id}`, { method: "DELETE" }).catch((e) => setMsg(e.message)); load(); };
  const fromTraining = async () => {
    try {
      await api(`/api/admin/marketplace/from-training/${trainingId.trim()}?min_plan_id=${trainingPlan}`, { method: "POST" });
      setMsg("Published from training payload ✓"); setTrainingId(""); load();
    } catch (err) { setMsg((err as Error).message); }
  };
  const f = (k: keyof typeof EMPTY) => ({ value: form[k], onChange: (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value }) });

  return (
    <div className="space-y-4">
      {msg && <p className="rounded-xl bg-slate-800 p-3 text-sm" onClick={() => setMsg(null)}>{msg}</p>}
      <form onSubmit={submit} className="card grid gap-3 md:grid-cols-2">
        <p className="font-semibold md:col-span-2">{editing ? "✏️ Edit Master Bot" : "➕ Publish a new Master Bot"}</p>
        <div><label className="label">Name</label><input className="input" required {...f("name")} /></div>
        <div className="grid grid-cols-[80px_1fr] gap-2">
          <div><label className="label">Avatar</label><input className="input" {...f("avatar")} /></div>
          <div><label className="label">Min plan</label>
            <select className="input" {...f("min_plan_id")}><option>FREE</option><option>PRO</option><option>BUSINESS</option></select></div>
        </div>
        <div className="md:col-span-2"><label className="label">Tagline</label><input className="input" {...f("tagline")} /></div>
        <div><label className="label">Description</label><textarea className="input min-h-20" {...f("description")} /></div>
        <div><label className="label">Persona (cloned into subscriber bots)</label><textarea className="input min-h-20" {...f("persona")} /></div>
        <div><label className="label">Welcome message ({"{bot}"}, {"{master}"})</label><textarea className="input min-h-16" {...f("welcome_message")} /></div>
        <div className="space-y-2">
          <div><label className="label">Follow-up test prompt</label><input className="input" {...f("test_prompt")} /></div>
          <div><label className="label">Reply must contain (optional)</label><input className="input" {...f("test_expect")} /></div>
        </div>
        <div><label className="label">Skill payload JSON</label><textarea className="input min-h-48 font-mono text-xs" {...f("skill_payload")} /></div>
        <div><label className="label">Extra tools JSON</label><textarea className="input min-h-48 font-mono text-xs" {...f("tools")} /></div>
        <div className="flex gap-2 md:col-span-2">
          <button className="btn-primary">{editing ? "Save" : "Publish"}</button>
          {editing && <button type="button" className="btn-ghost" onClick={() => { setEditing(null); setForm(EMPTY); }}>Cancel</button>}
        </div>
      </form>
      <div className="card flex flex-wrap items-end gap-2">
        <div className="min-w-64 flex-1"><label className="label">Publish from a trained payload (training request id)</label><input className="input font-mono text-xs" value={trainingId} onChange={(e) => setTrainingId(e.target.value)} placeholder="uuid from Train My Bot" /></div>
        <select className="input w-32" value={trainingPlan} onChange={(e) => setTrainingPlan(e.target.value)}><option>FREE</option><option>PRO</option><option>BUSINESS</option></select>
        <button className="btn-ghost" disabled={!trainingId} onClick={fromTraining}>Publish</button>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {rows.map((m) => (
          <div key={m.id} className={`card space-y-1 ${m.is_published ? "" : "opacity-60"}`}>
            <p className="font-semibold">{m.avatar} {m.name} <span className="text-xs text-slate-500">{m.min_plan_id}+ · {m.is_published ? "published" : "draft"}</span></p>
            <p className="text-xs text-slate-400">👥 {m.members} · 🎓 {m.graduated} · <code>{m.skill_id}</code></p>
            <div className="flex gap-2 pt-1 text-xs">
              <button className="btn-ghost px-2! py-1!" onClick={() => edit(m)}>Edit</button>
              <button className="btn-ghost px-2! py-1!" onClick={() => toggle(m)}>{m.is_published ? "Unpublish" : "Publish"}</button>
              <Link className="btn-ghost px-2! py-1!" href={`/marketplace/group/?id=${m.id}`}>Group</Link>
              <button className="btn-ghost px-2! py-1! text-red-300" onClick={() => del(m)}>Delete</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
