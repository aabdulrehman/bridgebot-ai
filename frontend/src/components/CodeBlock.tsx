"use client";
/** Monospace code block with a copy button. */
import { useState } from "react";

export default function CodeBlock({ code, lang = "bash" }: { code: string; lang?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="group relative">
      <pre className="overflow-x-auto rounded-xl border border-slate-800 bg-black/60 p-3 text-[12px] leading-relaxed text-slate-200" data-lang={lang}><code>{code}</code></pre>
      <button onClick={() => { navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1200); }}
        className="absolute top-2 right-2 rounded-md bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300 opacity-80 hover:opacity-100">
        {copied ? "copied" : "copy"}
      </button>
    </div>
  );
}
