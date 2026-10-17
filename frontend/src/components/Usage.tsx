/** Usage meter row. */
export default function UsageBar({ label, used, max, unit = "" }: { label: string; used: number; max: number; unit?: string }) {
  const unlimited = max === -1;
  const pct = unlimited ? 5 : max <= 0 ? 100 : Math.min(100, (used / max) * 100);
  const color = pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-400" : "bg-emerald-500";
  return (
    <div>
      <div className="flex justify-between text-xs text-slate-400">
        <span>{label}</span>
        <span className="text-slate-200">{used.toLocaleString()}{unit} / {unlimited ? "∞" : max === 0 ? "not included" : max.toLocaleString() + unit}</span>
      </div>
      <div className="mt-1 h-2 rounded-full bg-slate-800"><div className={`h-2 rounded-full ${color}`} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}
