"use client";
/** Remote Desktop: see and control your own machine's screen live (frames + input over /ws/dashboard → connector). */
import { useCallback, useEffect, useRef, useState } from "react";
import Shell from "@/components/Shell";
import { api, getToken, wsBase } from "@/lib/api";
import type { Connector } from "@/lib/types";

type FileItem = { name: string; dir: boolean; size: number; mtime: number };
const QUALITY: Record<string, { quality: number; width: number; fps: number }> = {
  Fast: { quality: 35, width: 960, fps: 8 },
  Balanced: { quality: 55, width: 1280, fps: 6 },
  Sharp: { quality: 75, width: 1920, fps: 4 },
};
const KEYS = [["Esc", "Escape"], ["Tab", "Tab"], ["⌫", "BackSpace"], ["⏎", "Return"], ["↑", "Up"], ["↓", "Down"], ["←", "Left"], ["→", "Right"],
  ["Ctrl+C", "ctrl+c"], ["Ctrl+V", "ctrl+v"], ["Ctrl+Z", "ctrl+z"], ["Ctrl+L", "ctrl+l"], ["Ctrl+T", "ctrl+t"], ["Ctrl+W", "ctrl+w"], ["Alt+Tab", "alt+Tab"], ["Super", "super"], ["Alt+F4", "alt+F4"]];
const fmt = (n: number) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

export default function RemotePage() {
  const [machines, setMachines] = useState<Connector[]>([]);
  const [cid, setCid] = useState("");
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("Not connected");
  const [error, setError] = useState<string | null>(null);
  const [preset, setPreset] = useState("Balanced");
  const [tab, setTab] = useState<"screen" | "files">("screen");
  const [zoom, setZoom] = useState(1);
  const [fps, setFps] = useState(0);
  const [text, setText] = useState("");
  const [path, setPath] = useState("");
  const [items, setItems] = useState<FileItem[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const frames = useRef(0);
  const cidRef = useRef("");
  cidRef.current = cid;

  useEffect(() => {
    api<Connector[]>("/api/connectors").then((m) => { setMachines(m); setCid((c) => c || m.find((x) => x.status === "ONLINE")?.id || m[0]?.id || ""); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { const t = setInterval(() => { setFps(frames.current); frames.current = 0; }, 1000); return () => clearInterval(t); }, []);

  const send = useCallback((m: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ connector_id: cidRef.current, ...m }));
  }, []);
  const input = useCallback((m: Record<string, unknown>) => send({ type: "RD_INPUT", ...m }), [send]);

  const stop = useCallback(() => { send({ type: "RD_STOP" }); wsRef.current?.close(); wsRef.current = null; setRunning(false); setStatus("Stopped"); }, [send]);
  const start = useCallback(() => {
    setError(null);
    wsRef.current?.close();
    const ws = new WebSocket(`${wsBase()}/ws/dashboard?token=${encodeURIComponent(getToken() || "")}`);
    wsRef.current = ws;
    setStatus("Connecting…");
    let ka: ReturnType<typeof setInterval> | undefined;
    ws.onopen = () => {
      setRunning(true); setStatus("Waiting for first frame…");
      ws.send(JSON.stringify({ connector_id: cidRef.current, type: "RD_START", ...QUALITY[preset] }));
      ka = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ connector_id: cidRef.current, type: "RD_ACK" })), 10000);
    };
    ws.onmessage = (ev) => {
      let m: Record<string, unknown>;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "RD_FRAME") {
        if (imgRef.current) imgRef.current.src = `data:image/jpeg;base64,${m.jpg}`;
        frames.current++;
        setStatus(`Live · ${m.sw}×${m.sh}`);
        ws.send(JSON.stringify({ connector_id: cidRef.current, type: "RD_ACK" }));
      } else if (m.type === "RD_ERROR") setError(String(m.error));
      else if (m.type === "RD_LS") { setPath(String(m.path || "")); setItems(m.items as FileItem[]); }
      else if (m.type === "RD_FILE") {
        const bin = atob(String(m.data)); const arr = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([arr])); a.download = String(m.name); a.click();
      } else if (m.type === "RD_CLIP") { setText(String(m.text)); setNote("Laptop clipboard copied into the text box"); }
      else if (m.type === "RD_OK") { setNote(`Done: ${m.what}${m.detail ? ` (${m.detail})` : ""}`); if (m.what === "upload") send({ type: "RD_LS", path }); }
      else if (m.type === "CONNECTOR_STATUS" && m.connector_id === cidRef.current && m.status === "ONLINE") ws.send(JSON.stringify({ connector_id: cidRef.current, type: "RD_START", ...QUALITY[preset] }));
    };
    ws.onclose = () => { clearInterval(ka); setRunning(false); setStatus("Disconnected"); };
  }, [preset, send, path]);
  useEffect(() => () => wsRef.current?.close(), []);
  useEffect(() => { if (running) send({ type: "RD_START", ...QUALITY[preset] }); }, [preset]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- pointer → normalised coords
  const pos = (e: { clientX: number; clientY: number }) => {
    const r = imgRef.current!.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  const touch = useRef<{ t: number; x: number; y: number; long?: ReturnType<typeof setTimeout>; moved: boolean; drag: boolean; lastMove: number; py?: number } | null>(null);
  const onDown = (e: React.PointerEvent) => {
    if (e.pointerType === "mouse") { input({ action: "down", button: e.button === 2 ? 3 : e.button === 1 ? 2 : 1, ...pos(e) }); return; }
    const p = { t: Date.now(), x: e.clientX, y: e.clientY, moved: false, drag: false, lastMove: 0 } as NonNullable<typeof touch.current>;
    p.long = setTimeout(() => { if (!p.moved) { input({ action: "click", button: 3, ...pos(e) }); touch.current = null; navigator.vibrate?.(30); } }, 600);
    touch.current = p;
  };
  const onMove = (e: React.PointerEvent) => {
    const now = Date.now();
    if (e.pointerType === "mouse") { if (now - (touch.current?.lastMove || 0) > 50) { touch.current = { ...(touch.current || { t: 0, x: 0, y: 0, moved: false, drag: false }), lastMove: now }; input({ action: "move", ...pos(e) }); } return; }
    const p = touch.current; if (!p) return;
    if (Math.hypot(e.clientX - p.x, e.clientY - p.y) > 12 && !p.moved) {
      p.moved = true; clearTimeout(p.long);
      if (Date.now() - p.t > 250) { p.drag = true; input({ action: "down", button: 1, x: pos({ clientX: p.x, clientY: p.y }).x, y: pos({ clientX: p.x, clientY: p.y }).y }); }
    }
    if (p.drag && now - p.lastMove > 60) { p.lastMove = now; input({ action: "move", ...pos(e) }); }
  };
  const onUp = (e: React.PointerEvent) => {
    if (e.pointerType === "mouse") { input({ action: "up", button: e.button === 2 ? 3 : e.button === 1 ? 2 : 1, ...pos(e) }); return; }
    const p = touch.current; touch.current = null; if (!p) return;
    clearTimeout(p.long);
    if (p.drag) input({ action: "up", button: 1, ...pos(e) });
    else if (!p.moved) input({ action: "click", button: 1, ...pos(e) });
  };
  const onWheel = (e: React.WheelEvent) => input({ action: "scroll", dy: Math.sign(e.deltaY) * Math.max(1, Math.round(Math.abs(e.deltaY) / 100)), ...pos(e) });
  const onKey = (e: React.KeyboardEvent) => {
    if (!running || tab !== "screen") return;
    const map: Record<string, string> = { Enter: "Return", Backspace: "BackSpace", Escape: "Escape", Tab: "Tab", ArrowUp: "Up", ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right", Delete: "Delete", Home: "Home", End: "End", PageUp: "Prior", PageDown: "Next", " ": "space" };
    const mods = [e.ctrlKey && "ctrl", e.altKey && "alt", e.metaKey && "super"].filter(Boolean).join("+");
    let k = map[e.key] || (/^F\d{1,2}$/.test(e.key) ? e.key : e.key.length === 1 && /[A-Za-z0-9]/.test(e.key) ? e.key : "");
    if (!k && e.key.length === 1 && !mods) { input({ action: "type", text: e.key }); e.preventDefault(); return; }
    if (!k) return;
    if (mods) k = `${mods}+${k}`;
    if (mods || map[e.key] || /^F\d/.test(e.key)) input({ action: "key", keys: k }); else input({ action: "type", text: e.key });
    e.preventDefault();
  };
  const upload = (f: File) => {
    if (f.size > 12 * 1048576) { setError("Max 12 MB per file"); return; }
    const r = new FileReader();
    r.onload = () => send({ type: "RD_PUT", path: `${path ? path + "/" : ""}${f.name}`, data: String(r.result).split(",")[1] });
    r.readAsDataURL(f);
  };
  const online = machines.find((m) => m.id === cid)?.status === "ONLINE";

  return (
    <Shell>
      <div className="space-y-3" tabIndex={0} onKeyDown={onKey}>
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="mr-auto text-xl font-semibold">🖥️ Remote Desktop</h1>
          <select className="input w-auto!" value={cid} onChange={(e) => { stop(); setCid(e.target.value); }}>
            {machines.map((m) => <option key={m.id} value={m.id}>{m.machine_name} {m.status === "ONLINE" ? "🟢" : "⚫"}</option>)}
          </select>
          <select className="input w-auto!" value={preset} onChange={(e) => setPreset(e.target.value)}>
            {Object.keys(QUALITY).map((q) => <option key={q}>{q}</option>)}
          </select>
          {running ? <button className="btn-ghost" onClick={stop}>■ Stop</button> : <button className="btn-primary" disabled={!cid} onClick={start}>▶ Connect</button>}
        </div>
        <p className="text-xs text-slate-400">{status} · {fps} fps {online ? "" : "· machine offline"} — tap = click · long-press = right-click · touch-and-hold then drag = drag · use the keyboard box below to type.</p>
        {error && <div className="rounded-lg border border-rose-700/50 bg-rose-900/30 p-2 text-sm text-rose-200">{error} <button className="ml-2 underline" onClick={() => setError(null)}>dismiss</button></div>}
        {note && <div className="rounded-lg border border-emerald-700/50 bg-emerald-900/20 p-2 text-xs text-emerald-200" onClick={() => setNote(null)}>{note}</div>}
        <div className="flex gap-2">
          {(["screen", "files"] as const).map((t) => <button key={t} className={tab === t ? "btn-primary" : "btn-ghost"} onClick={() => { setTab(t); if (t === "files") send({ type: "RD_LS", path }); }}>{t === "screen" ? "Screen" : "Files"}</button>)}
        </div>

        <div className={tab === "screen" ? "space-y-2" : "hidden"}>
          <div className="overflow-auto rounded-xl border border-slate-800 bg-black" style={{ maxHeight: "70vh" }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img ref={imgRef} alt={running ? "remote screen" : ""} draggable={false} onContextMenu={(e) => e.preventDefault()}
              onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onWheel={onWheel}
              style={{ width: `${zoom * 100}%`, maxWidth: "none", touchAction: zoom > 1 ? "pan-x pan-y" : "none", userSelect: "none", minHeight: running ? 0 : 200 }} />
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <button className="btn-ghost px-2!" onClick={() => setZoom((z) => Math.max(1, z - 0.5))}>－</button>
            <span className="text-xs text-slate-400">{zoom}×</span>
            <button className="btn-ghost px-2!" onClick={() => setZoom((z) => Math.min(4, z + 0.5))}>＋</button>
            <button className="btn-ghost px-2!" onClick={() => input({ action: "scroll", dy: -5 })}>Scroll ↑</button>
            <button className="btn-ghost px-2!" onClick={() => input({ action: "scroll", dy: 5 })}>Scroll ↓</button>
            {KEYS.map(([l, k]) => <button key={k} className="btn-ghost px-2! py-1! text-xs" onClick={() => input({ action: "key", keys: k })}>{l}</button>)}
          </div>
          <div className="flex gap-2">
            <input className="input" placeholder="Type here and press Send (goes to the focused window)" value={text} onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter" && text) { input({ action: "type", text }); setText(""); } }} />
            <button className="btn-primary" onClick={() => { if (text) { input({ action: "type", text }); setText(""); } }}>Send</button>
          </div>
          <div className="flex flex-wrap gap-1">
            {[["chrome", "🌐 Chrome"], ["firefox", "🦊 Firefox"], ["files", "📁 Files"], ["terminal", "⌨️ Terminal"], ["editor", "📝 Editor"]].map(([a, l]) =>
              <button key={a} className="btn-ghost px-2! py-1! text-xs" onClick={() => send({ type: "RD_LAUNCH", app: a })}>{l}</button>)}
            <button className="btn-ghost px-2! py-1! text-xs" onClick={() => send({ type: "RD_CLIP_GET" })}>📋 Get laptop clipboard</button>
            <button className="btn-ghost px-2! py-1! text-xs" onClick={() => send({ type: "RD_CLIP_SET", text })}>📋 Send box text to clipboard</button>
          </div>
        </div>

        <div className={tab === "files" ? "space-y-2" : "hidden"}>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-slate-400">~/{path}</span>
            {path && <button className="btn-ghost px-2! py-1!" onClick={() => send({ type: "RD_LS", path: path.split("/").slice(0, -1).join("/") })}>⬆ Up</button>}
            <button className="btn-ghost px-2! py-1!" onClick={() => send({ type: "RD_LS", path })}>↻</button>
            <label className="btn-primary cursor-pointer px-2! py-1!">⬆ Upload<input type="file" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
          </div>
          {!running && <p className="text-sm text-slate-400">Press Connect first.</p>}
          <ul className="divide-y divide-slate-800 rounded-xl border border-slate-800">
            {items.map((f) => {
              const p = `${path ? path + "/" : ""}${f.name}`;
              return (
                <li key={f.name} className="flex items-center gap-2 px-3 py-2 text-sm">
                  <button className="mr-auto truncate text-left hover:text-emerald-300" onClick={() => f.dir && send({ type: "RD_LS", path: p })}>{f.dir ? "📁" : "📄"} {f.name}</button>
                  {!f.dir && <span className="text-xs text-slate-500">{fmt(f.size)}</span>}
                  {!f.dir && <button className="btn-ghost px-2! py-1! text-xs" onClick={() => send({ type: "RD_GET", path: p })}>⬇</button>}
                  <button className="btn-ghost px-2! py-1! text-xs" onClick={() => send({ type: "RD_OPEN", path: p })}>Open on laptop</button>
                </li>
              );
            })}
          </ul>
        </div>
        <p className="text-xs text-slate-500">Only you (the machine owner) can open this. Machine must run the connector with <code>--remote-desktop</code> on an X11 session. Emergency off on the laptop: <code>touch ~/.bridgebot/remote_desktop_off</code>.</p>
      </div>
    </Shell>
  );
}
