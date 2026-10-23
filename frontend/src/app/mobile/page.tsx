"use client";
/** Live Mobilelty: control your Android phone from a laptop browser (reverse of Remote Desktop). */
import { useCallback, useEffect, useRef, useState } from "react";
import Shell from "@/components/Shell";
import { api, getToken, wsBase } from "@/lib/api";
import { useApiBase } from "@/lib/useBase";
import type { Connector } from "@/lib/types";

const QUALITY: Record<string, { quality: number; width: number; fps: number }> = {
  Fast: { quality: 35, width: 540, fps: 8 },
  Balanced: { quality: 55, width: 720, fps: 5 },
  Sharp: { quality: 75, width: 1080, fps: 3 },
};

export default function MobilePage() {
  const base = useApiBase();
  const [phones, setPhones] = useState<Connector[]>([]);
  const [cid, setCid] = useState("");
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("Not connected");
  const [error, setError] = useState<string | null>(null);
  const [preset, setPreset] = useState("Balanced");
  const [fps, setFps] = useState(0);
  const [text, setText] = useState("");
  const [pairCode, setPairCode] = useState<string | null>(null);
  const [pairName, setPairName] = useState("My Android phone");
  const [note, setNote] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const frames = useRef(0);
  const cidRef = useRef("");
  const drag = useRef<{ x: number; y: number; t: number; moved: boolean } | null>(null);
  cidRef.current = cid;

  const reload = useCallback(() => {
    api<Connector[]>("/api/connectors").then((m) => {
      const android = m.filter((x) => (x.features || []).includes("android") || /android|phone|mobile/i.test(x.machine_name));
      const list = android.length ? android : m;
      setPhones(list);
      setCid((c) => c || list.find((x) => x.status === "ONLINE")?.id || list[0]?.id || "");
    }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { const t = setInterval(() => { setFps(frames.current); frames.current = 0; }, 1000); return () => clearInterval(t); }, []);

  const send = useCallback((m: Record<string, unknown>) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ connector_id: cidRef.current, ...m }));
  }, []);
  const input = useCallback((m: Record<string, unknown>) => send({ type: "RD_INPUT", ...m }), [send]);

  const stop = useCallback(() => {
    send({ type: "RD_STOP" });
    wsRef.current?.close();
    wsRef.current = null;
    setRunning(false);
    setStatus("Stopped");
  }, [send]);

  const start = useCallback(() => {
    setError(null);
    wsRef.current?.close();
    const ws = new WebSocket(`${wsBase()}/ws/dashboard?token=${encodeURIComponent(getToken() || "")}`);
    wsRef.current = ws;
    setStatus("Connecting…");
    let ka: ReturnType<typeof setInterval> | undefined;
    ws.onopen = () => {
      setRunning(true);
      setStatus("Waiting for first frame…");
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
      else if (m.type === "CONNECTOR_STATUS" && m.connector_id === cidRef.current && m.status === "ONLINE") {
        ws.send(JSON.stringify({ connector_id: cidRef.current, type: "RD_START", ...QUALITY[preset] }));
      }
    };
    ws.onclose = () => { clearInterval(ka); setRunning(false); setStatus("Disconnected"); };
  }, [preset]);

  useEffect(() => () => wsRef.current?.close(), []);
  useEffect(() => { if (running) send({ type: "RD_START", ...QUALITY[preset] }); }, [preset]); // eslint-disable-line

  const pos = (e: { clientX: number; clientY: number }) => {
    const r = imgRef.current!.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    };
  };

  const onDown = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    drag.current = { ...pos(e), t: Date.now(), moved: false, x: pos(e).x, y: pos(e).y };
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current; if (!d) return;
    const p = pos(e);
    if (Math.hypot(p.x - d.x, p.y - d.y) > 0.02) d.moved = true;
  };
  const onUp = (e: React.PointerEvent) => {
    const d = drag.current; drag.current = null; if (!d) return;
    const p = pos(e);
    if (d.moved) input({ action: "swipe", x1: d.x, y1: d.y, x2: p.x, y2: p.y, duration_ms: Math.min(800, Date.now() - d.t) });
    else input({ action: "tap", x: p.x, y: p.y });
  };

  const makeCode = async () => {
    setError(null);
    try {
      const r = await api<{ code: string; expires_in: number }>("/api/mobile/pair-code", {
        method: "POST",
        json: { device_name: pairName.trim() || "My Android phone" },
      });
      setPairCode(r.code);
      setNote(`Code valid for ${Math.round(r.expires_in / 60)} minutes. Enter it in the phone app.`);
      reload();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const online = phones.find((m) => m.id === cid)?.status === "ONLINE";

  return (
    <Shell>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="mr-auto text-xl font-semibold">📱 Live Mobilelty</h1>
          <select className="input w-auto!" value={cid} onChange={(e) => { stop(); setCid(e.target.value); }}>
            {phones.length === 0 && <option value="">No phones yet</option>}
            {phones.map((m) => (
              <option key={m.id} value={m.id}>
                {m.machine_name} {(m.features || []).includes("android") ? "🤖" : ""} {m.status === "ONLINE" ? "🟢" : "⚫"}
              </option>
            ))}
          </select>
          <select className="input w-auto!" value={preset} onChange={(e) => setPreset(e.target.value)}>
            {Object.keys(QUALITY).map((q) => <option key={q}>{q}</option>)}
          </select>
          {running
            ? <button className="btn-ghost" onClick={stop}>■ Stop</button>
            : <button className="btn-primary" disabled={!cid} onClick={start}>▶ Connect</button>}
        </div>
        <p className="text-xs text-slate-400">
          {status} · {fps} fps {online ? "" : "· phone offline"} — click = tap · drag = swipe. Only your account can open this phone.
        </p>
        {error && <div className="rounded-lg border border-rose-700/50 bg-rose-900/30 p-2 text-sm text-rose-200">{error}</div>}
        {note && <div className="rounded-lg border border-emerald-700/50 bg-emerald-900/20 p-2 text-xs text-emerald-200" onClick={() => setNote(null)}>{note}</div>}

        <div className="overflow-auto rounded-xl border border-slate-800 bg-black" style={{ maxHeight: "70vh" }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            ref={imgRef}
            alt={running ? "phone screen" : ""}
            draggable={false}
            onContextMenu={(e) => e.preventDefault()}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            style={{ width: "100%", maxWidth: 420, margin: "0 auto", display: "block", touchAction: "none", userSelect: "none", minHeight: running ? 0 : 200 }}
          />
        </div>

        <div className="flex flex-wrap gap-1">
          <button className="btn-ghost px-3!" onClick={() => input({ action: "back" })}>◀ Back</button>
          <button className="btn-ghost px-3!" onClick={() => input({ action: "home" })}>⬤ Home</button>
          <button className="btn-ghost px-3!" onClick={() => input({ action: "recents" })}>▣ Recents</button>
        </div>
        <div className="flex gap-2">
          <input
            className="input"
            placeholder="Type into the focused field on the phone"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && text) { input({ action: "type", text }); setText(""); } }}
          />
          <button className="btn-primary" onClick={() => { if (text) { input({ action: "type", text }); setText(""); } }}>Send</button>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 space-y-3">
          <h2 className="font-semibold text-slate-200">Pair a new Android phone</h2>
          <ol className="list-decimal pl-5 text-sm text-slate-300 space-y-1">
            <li>Install the APK: <a className="underline text-emerald-300" href="/downloads/live-mobilelty.apk">download live-mobilelty.apk</a> (allow unknown sources).</li>
            <li>Get a 6-digit code below and type it in the app → <b>Pair</b>.</li>
            <li>In the app: allow <b>screen capture</b>, enable the <b>Accessibility</b> service, disable battery optimisation.</li>
            <li>Come back here, pick the phone, press <b>Connect</b>.</li>
          </ol>
          <div className="flex flex-wrap gap-2 items-center">
            <input className="input w-auto!" value={pairName} onChange={(e) => setPairName(e.target.value)} placeholder="Phone name" />
            <button className="btn-primary" onClick={makeCode}>Get pair code</button>
            {pairCode && (
              <span className="rounded-lg bg-emerald-900/40 border border-emerald-700 px-4 py-2 font-mono text-2xl tracking-widest text-emerald-200">
                {pairCode}
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500">
            Full guide: <a className="underline" href="/docs/mobile/">Live Mobilelty docs</a> · Hub: <code>{base}</code>
          </p>
        </div>
      </div>
    </Shell>
  );
}
