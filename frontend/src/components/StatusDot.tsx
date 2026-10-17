/** Small ONLINE/OFFLINE pill. */
export default function StatusDot({ online, label }: { online: boolean; label?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ${online ? "bg-emerald-900/40 text-emerald-300" : "bg-slate-800 text-slate-400"}`}>
      <span className={`h-2 w-2 rounded-full ${online ? "animate-pulse bg-emerald-400" : "bg-slate-500"}`} />
      {label ?? (online ? "ONLINE" : "OFFLINE")}
    </span>
  );
}
