"use client";
/**
 * Control Room — connector widgets, task dispatch and live log console.
 * Live data arrives over /ws/dashboard; REST is used for initial state + actions.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import Shell from "@/components/Shell";
import StatusDot from "@/components/StatusDot";
import { api, apiBase } from "@/lib/api";
import { CONNECTOR_SOURCE } from "@/lib/site";
import { useHubSocket } from "@/lib/useHubSocket";
import type { Connector, HubEvent } from "@/lib/types";

interface LogLine {
  id: number;
  ts: number;
  connector_id?: string;
  task_id?: string;
  kind: "stdout" | "stderr" | "system";
  text: string;
}

const MAX_LINES = 1500;
let lineSeq = 0;

export default function DashboardPage() {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [command, setCommand] = useState("echo Hello from BridgeBot");
  const [newMachine, setNewMachine] = useState("");
  const [newKey, setNewKey] = useState<{ machine: string; key: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const [filterSelected, setFilterSelected] = useState(false);
  const consoleRef = useRef<HTMLDivElement>(null);

  const push = useCallback((l: Omit<LogLine, "id" | "ts">) => {
    setLogs((prev) => [...prev.slice(-MAX_LINES + 1), { ...l, id: ++lineSeq, ts: Date.now() }]);
  }, []);

  const load = useCallback(async () => {
    try {
      const list = await api<Connector[]>("/api/connectors");
      setConnectors(list);
      setSelected((s) => s || list.find((c) => c.status === "ONLINE")?.id || list[0]?.id || "");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const nameOf = useCallback((id?: string) => connectors.find((c) => c.id === id)?.machine_name ?? id?.slice(0, 8) ?? "?", [connectors]);

  // ---- live hub events
  const socketState = useHubSocket((ev: HubEvent) => {
    switch (ev.type) {
      case "SNAPSHOT":
        load();
        break;
      case "CONNECTOR_STATUS":
        setConnectors((prev) => prev.map((c) => (c.id === ev.connector_id ? { ...c, status: ev.status as Connector["status"], last_heartbeat: new Date().toISOString() } : c)));
        push({ kind: "system", connector_id: ev.connector_id, text: `● ${nameOf(ev.connector_id)} is ${ev.status}` });
        if (ev.status === "ONLINE") setTimeout(load, 800);
        break;
      case "HELLO":
        push({ kind: "system", connector_id: ev.connector_id, text: `🤝 ${String(ev.machine)} connected — ${String(ev.platform)} (v${String(ev.version)})` });
        break;
      case "TASK_DISPATCHED":
        push({ kind: "system", connector_id: ev.connector_id, task_id: ev.task_id, text: `$ ${Array.isArray(ev.command) ? (ev.command as string[]).join(" ") : String(ev.command)}` });
        break;
      case "LOG":
        push({ kind: ev.stream === "stderr" ? "stderr" : "stdout", connector_id: ev.connector_id, task_id: ev.task_id, text: ev.line ?? "" });
        break;
      case "TASK_RESULT":
        push({ kind: "system", connector_id: ev.connector_id, task_id: ev.task_id, text: `⏹ exit ${ev.exit_code} · ${ev.duration_ms} ms${ev.error ? ` · ${ev.error}` : ""}` });
        break;
      case "SKILL_INJECT_SENT":
        push({ kind: "system", connector_id: ev.connector_id, text: `📦 skill ${String(ev.skill_id)} pushed to agent '${String(ev.agent_name)}'` });
        break;
      case "SKILL_ACK":
        push({ kind: ev.ok ? "system" : "stderr", connector_id: ev.connector_id, text: ev.ok ? `🧠 skill ${String(ev.skill_id)} installed → ${String(ev.path)}` : `skill inject failed: ${String(ev.error)}` });
        load();
        break;
      case "TRAINING_STATUS":
        push({ kind: "system", text: `🧪 training ${String(ev.training_request_id).slice(0, 8)} → ${ev.status}` });
        break;
    }
  });

  useEffect(() => {
    if (autoScroll && consoleRef.current) consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
  }, [logs, autoScroll]);

  // ---- actions
  const dispatch = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!selected) return setError("Select a machine first");
    try {
      await api("/api/trigger-task", { method: "POST", json: { connector_id: selected, command } });
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const createConnector = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const c = await api<Connector>("/api/connectors", { method: "POST", json: { machine_name: newMachine } });
      setNewKey({ machine: c.machine_name, key: c.api_key ?? "" });
      setNewMachine("");
      await load();
      setSelected(c.id);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const removeConnector = async (id: string) => {
    if (!confirm("Delete this connector? Its API key stops working.")) return;
    try {
      await api(`/api/connectors/${id}`, { method: "DELETE" });
      if (selected === id) setSelected("");
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const online = connectors.filter((c) => c.status === "ONLINE").length;
  const visibleLogs = useMemo(() => (filterSelected ? logs.filter((l) => !l.connector_id || l.connector_id === selected) : logs), [logs, filterSelected, selected]);
  const runCmd = newKey ? `python local_connector.py --server ${apiBase()} --api-key ${newKey.key} --machine-name ${newKey.machine}` : "";

  return (
    <Shell>
      {/* ---- summary widgets */}
      <section className="grid grid-cols-3 gap-3">
        <div className="card"><p className="label">Machines</p><p className="text-2xl font-bold">{connectors.length}</p></div>
        <div className="card"><p className="label">Online</p><p className="text-2xl font-bold text-emerald-400">{online}</p></div>
        <div className="card"><p className="label">Hub</p><StatusDot online={socketState === "open"} label={socketState.toUpperCase()} /></div>
      </section>

      {error && (
        <p className="rounded-xl bg-red-950/50 p-3 text-sm text-red-300" onClick={() => setError(null)}>
          {error} {/plan|Upgrade/i.test(error) && <a href="/billing/" className="underline">Upgrade →</a>}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
        {/* ---- connectors column */}
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-300">Connected machines</h2>
          {connectors.length === 0 && <p className="card text-sm text-slate-400">No machines yet — add one below, then run the connector on your PC.</p>}
          {connectors.map((c) => (
            <button key={c.id} onClick={() => setSelected(c.id)}
              className={`card w-full text-left transition ${selected === c.id ? "border-emerald-600" : "hover:border-slate-600"}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-semibold">🖥️ {c.machine_name}</span>
                <StatusDot online={c.status === "ONLINE"} />
              </div>
              <p className="mt-1 text-[11px] text-slate-500">Heartbeat: {c.last_heartbeat ? new Date(c.last_heartbeat).toLocaleString() : "never"}</p>
              {c.agents.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {c.agents.map((a) => (
                    <span key={a.id} className="rounded-md bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
                      {a.agent_name} · {a.agent_type} · 🧠{a.current_injected_skills_json?.length ?? 0}
                    </span>
                  ))}
                </div>
              )}
              <span onClick={(e) => { e.stopPropagation(); removeConnector(c.id); }} className="mt-2 inline-block text-[11px] text-red-400/70 hover:text-red-300">delete</span>
            </button>
          ))}

          <form onSubmit={createConnector} className="card space-y-2">
            <label className="label" htmlFor="machine">Add machine</label>
            <div className="flex gap-2">
              <input id="machine" className="input" placeholder="home-pc" required value={newMachine} onChange={(e) => setNewMachine(e.target.value)} />
              <button className="btn-primary shrink-0">Add</button>
            </div>
          </form>

          {newKey && (
            <div className="card space-y-2 border-amber-600/60">
              <p className="text-sm font-semibold text-amber-300">🔑 API key for {newKey.machine} — shown only once!</p>
              <code className="block break-all rounded-lg bg-slate-950 p-2 text-xs text-amber-200">{newKey.key}</code>
              <p className="text-xs text-slate-400">
                <a className="text-sky-300 underline" href="/downloads/local_connector.py" download>⬇ Download local_connector.py</a> ·{" "}
                <a className="text-sky-300 underline" href={CONNECTOR_SOURCE} target="_blank" rel="noopener noreferrer">View source on GitHub</a> (MIT) — then run on that PC (needs <code>pip install websockets</code>):
              </p>
              <code className="block break-all rounded-lg bg-slate-950 p-2 text-[11px] text-slate-300">{runCmd}</code>
              <div className="flex gap-2">
                <button className="btn-ghost text-xs" onClick={() => navigator.clipboard.writeText(runCmd)}>Copy command</button>
                <button className="btn-ghost text-xs" onClick={() => setNewKey(null)}>I saved it</button>
              </div>
            </div>
          )}
        </section>

        {/* ---- console column */}
        <section className="space-y-3">
          <form onSubmit={dispatch} className="card flex flex-col gap-2 sm:flex-row">
            <select className="input sm:max-w-48" value={selected} onChange={(e) => setSelected(e.target.value)}>
              <option value="">Select machine…</option>
              {connectors.map((c) => <option key={c.id} value={c.id}>{c.machine_name} {c.status === "ONLINE" ? "🟢" : "⚫"}</option>)}
            </select>
            <input className="input font-mono" value={command} onChange={(e) => setCommand(e.target.value)} placeholder="echo hello" />
            <button className="btn-primary shrink-0" disabled={!selected}>▶ Run</button>
          </form>

          <div className="card p-0">
            <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2 text-xs text-slate-400">
              <span>Live console · {visibleLogs.length} lines</span>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-1"><input type="checkbox" checked={filterSelected} onChange={(e) => setFilterSelected(e.target.checked)} /> this machine</label>
                <label className="flex items-center gap-1"><input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} /> follow</label>
                <button className="hover:text-slate-200" onClick={() => setLogs([])}>clear</button>
              </div>
            </div>
            <div ref={consoleRef} className="h-[55dvh] overflow-y-auto bg-black/60 p-3 font-mono text-[12px] leading-relaxed">
              {visibleLogs.length === 0 && <p className="text-slate-600">Waiting for events… run a command or start a connector.</p>}
              {visibleLogs.map((l) => (
                <div key={l.id} className={`whitespace-pre-wrap break-all ${l.kind === "stderr" ? "text-red-400" : l.kind === "system" ? "text-sky-400" : "text-slate-200"}`}>
                  <span className="mr-2 text-slate-600">{new Date(l.ts).toLocaleTimeString()}</span>
                  {l.connector_id && <span className="mr-2 text-emerald-600">[{nameOf(l.connector_id)}]</span>}
                  {l.text}
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </Shell>
  );
}
