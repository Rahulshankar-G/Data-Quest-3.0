"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { CompareChart } from "@/components/charts";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";

type Learning = { outcome_count: number; wins: number; losses: number; win_rate: number; mean_absolute_percentage_error: number; cumulative_profit_uplift: number; models: Array<{ name: string; version: string; trained_at: string; metrics: Record<string, number>; parameters: Record<string, unknown> }> };
type ClockResult = { simulated_date: string; advanced_days: number; actual_simulated_profit: number; counterfactual_profit: number; profit_uplift: number; cumulative_profit_uplift: number; mape_pct: number; model_recalibrated: boolean };

export default function LearningPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["learning"], queryFn: () => api<Learning>("v1/learning") });
  const [result, setResult] = useState<ClockResult | null>(null);
  const advance = useMutation({ mutationFn: (days: number) => api<ClockResult>("v1/simulation/clock/advance", { method: "POST", body: JSON.stringify({ days }) }), onSuccess: (value) => { setResult(value); client.invalidateQueries(); } });
  const data = query.data;
  return <Shell><Heading eyebrow="Learn & calibrate" title="Learning and calibration hub" description="Advance simulated time to observe outcomes, compare measured results with the counterfactual, and persist model calibration metadata." />
    {query.isLoading || query.error ? <LoadingError error={query.error} /> : <div className="space-y-4"><div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[["Measured outcomes", data?.outcome_count ?? 0], ["Wins", data?.wins ?? 0], ["Win rate", `${((data?.win_rate ?? 0) * 100).toFixed(1)}%`], ["Mean absolute percentage error", `${(data?.mean_absolute_percentage_error ?? 0).toFixed(1)}%`]].map(([label, value]) => <div key={String(label)} className="rounded-xl border border-line bg-surface p-4"><span className="text-xs text-slate-500">{String(label)}</span><strong className="mt-2 block text-2xl">{String(value)}</strong></div>)}</div>
      <div className="grid gap-4 xl:grid-cols-[1fr_1.25fr]"><Panel title="Simulation clock" subtitle="Advance the simulator by 1, 7, or 30 days. This changes simulator data only."><div className="grid grid-cols-3 gap-2">{[1, 7, 30].map((days) => <button key={days} onClick={() => advance.mutate(days)} disabled={advance.isPending} className="rounded-lg border border-line bg-[#0d1420] px-3 py-3 text-sm hover:border-accent/40 disabled:opacity-50">Advance +{days}d</button>)}</div>{advance.error && <p className="mt-3 text-xs text-rose-300">{advance.error.message}</p>}{result && <div className="mt-4 rounded-xl border border-accent/20 bg-accent/[.05] p-4"><span className="text-xs text-accent">Clock advanced to {result.simulated_date}</span><div className="mt-3 grid grid-cols-2 gap-3"><div><span className="text-[10px] text-slate-500">Measured simulator profit</span><strong className="mt-1 block">${result.actual_simulated_profit.toFixed(2)}</strong></div><div><span className="text-[10px] text-slate-500">Counterfactual profit</span><strong className="mt-1 block">${result.counterfactual_profit.toFixed(2)}</strong></div><div><span className="text-[10px] text-slate-500">Profit uplift</span><strong className="mt-1 block text-accent">${result.profit_uplift.toFixed(2)}</strong></div><div><span className="text-[10px] text-slate-500">MAPE</span><strong className="mt-1 block">{result.mape_pct.toFixed(2)}%</strong></div></div></div>}</Panel>
        <Panel title="Win / loss performance" subtitle="Model versions are persisted to the registry after each clock advance"><CompareChart data={[{ outcome: "Decision outcomes", wins: data?.wins ?? 0, losses: data?.losses ?? 0 }]} x="outcome" bars={["wins", "losses"]} /><div className="mt-4 space-y-2">{data?.models.slice(0, 6).map((model) => <div key={model.name + model.version} className="flex items-center justify-between rounded-lg border border-line bg-[#0d1420] px-3 py-2"><span><strong className="block text-xs">{model.name}</strong><span className="text-[10px] text-slate-500">{model.version} · {new Date(model.trained_at).toLocaleString()}</span></span><span className="text-[10px] text-accent">{model.metrics.mape_pct != null ? `MAPE ${model.metrics.mape_pct}%` : `${model.metrics.observations ?? "—"} observations`}</span></div>)}</div></Panel></div>
    </div>}</Shell>;
}
