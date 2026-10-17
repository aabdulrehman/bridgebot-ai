"use client";
/** Login / Register screen. */
import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { api, apiBase, setToken } from "@/lib/api";

interface TokenOut {
  access_token: string;
}

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await api<TokenOut>(`/api/auth/${mode}`, { method: "POST", json: { email, password } });
      setToken(res.access_token);
      router.replace("/dashboard/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="grid min-h-dvh place-items-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon-192.png" alt="BridgeBot" className="mx-auto mb-3 h-16 w-16 rounded-2xl" />
          <h1 className="text-2xl font-bold">BridgeBot <span className="text-emerald-400">AI</span></h1>
          <p className="mt-1 text-sm text-slate-400">Control your local AI agents from anywhere.</p>
        </div>
        <form onSubmit={submit} className="card space-y-4">
          <div className="grid grid-cols-2 gap-1 rounded-xl bg-slate-950 p-1 text-sm">
            {(["login", "register"] as const).map((m) => (
              <button key={m} type="button" onClick={() => setMode(m)}
                className={`rounded-lg py-1.5 font-medium capitalize ${mode === m ? "bg-slate-800 text-emerald-300" : "text-slate-400"}`}>
                {m}
              </button>
            ))}
          </div>
          <div>
            <label className="label" htmlFor="email">Email</label>
            <input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <input id="password" className="input" type="password" minLength={8} required
              autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(e) => setPassword(e.target.value)} />
            {mode === "register" && <p className="mt-1 text-xs text-slate-500">At least 8 characters. New accounts get free platform tokens.</p>}
          </div>
          {error && <p className="rounded-lg bg-red-950/50 p-2 text-sm text-red-300">{error}</p>}
          <button className="btn-primary w-full" disabled={busy}>{busy ? "Please wait…" : mode === "login" ? "Login" : "Create account"}</button>
        </form>
        <p className="text-center text-[11px] text-slate-600">API: {apiBase()}</p>
      </div>
    </main>
  );
}
