"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { CompareChart } from "@/components/charts";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";

type Learning = { outcome_count: number; wins: number; losses: number; ties: number; win_rate: number; mean_absolute_percentage_error: number | null; forecast_error_count: number; cumulative_profit_uplift: number; models: Array<{ name: string; version: string; trained_at: string; metrics: Record<string, number>; parameters: Record<string, unknown> }> };
type ClockResult = { simulated_date: string; advanced_days: number; actual_simulated_profit: number; counterfactual_profit: number; profit_uplift: number; cumulative_profit_uplift: number; mape_pct: number | null; model_recalibrated: boolean; comparison_recorded: boolean; measured_outcomes: Array<{ recommendation_id: string; title: string; actual_profit: number; counterfactual_profit: number; profit_uplift: number }> };

export default function LearningPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["learning"], queryFn: () => api<Learning>("v1/learning") });
  const [result, setResult] = useState<ClockResult | null>(null);
  const advance = useMutation({ mutationFn: (days: number) => api<ClockResult>("v1/simulation/clock/advance", { method: "POST", body: JSON.stringify({ days }) }), onSuccess: (value) => {
    setResult(value);
    void client.invalidateQueries({ queryKey: ["learning"] });
    void client.invalidateQueries({ queryKey: ["dashboard"] });
  } });
  const data = query.data;
  const hasMeasuredOutcomes = (data?.outcome_count ?? 0) > 0;
  return <Shell><Heading eyebrow="Learn & calibrate" title="Learning and calibration hub" description="Advance simulated time to compare simulated campaign profit with its baseline. Recommendation outcomes are campaign-scoped; this workflow records calibration metadata but does not retrain a forecasting model." />
    {query.isLoading || query.error ? <LoadingError error={query.error} /> : <div className="space-y-4">
      {!hasMeasuredOutcomes && <div className="rounded-xl border border-amber-500/20 bg-amber-400/[.06] p-4"><strong className="text-sm text-amber-100">Requires at least one simulated decision</strong><p className="mb-0 mt-1 text-xs leading-relaxed text-slate-400">Approve and simulate a recommendation in <a href="/decisions" className="text-accent underline underline-offset-2">Decision Center</a>, then advance the simulation clock to measure its result. Win rates and recommendation error rates remain unavailable until at least one executed recommendation is measured.</p></div>}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[
        ["Measured outcomes", data?.outcome_count ?? 0],
        ["Wins", hasMeasuredOutcomes ? data?.wins ?? 0 : "—"],
        ["Win rate", hasMeasuredOutcomes ? `${((data?.win_rate ?? 0) * 100).toFixed(2)}%` : "—"],
        ["Ties", hasMeasuredOutcomes ? data?.ties ?? 0 : "—"],
        ["Forecast error", (data?.forecast_error_count ?? 0) > 0 && data?.mean_absolute_percentage_error != null ? `${data.mean_absolute_percentage_error.toFixed(2)}%` : "—"],
      ].map(([label, value]) => <div key={String(label)} className="rounded-xl border border-line bg-surface p-4"><span className="text-xs text-slate-500">{String(label)}</span><strong className="mt-2 block text-2xl">{String(value)}</strong></div>)}</div>
      <div className="grid gap-4 xl:grid-cols-[1fr_1.25fr]">
        <Panel title="Simulation clock" subtitle="Advance by 1, 7, or 30 days. Clock advances affect simulator data only.">
          <div className="grid grid-cols-3 gap-2">{[1, 7, 30].map((days) => <button key={days} onClick={() => advance.mutate(days)} disabled={advance.isPending} className="rounded-lg border border-line bg-[#0d1420] px-3 py-3 text-sm hover:border-accent/40 disabled:opacity-50">{advance.isPending ? "Advancing…" : `Advance +${days}d`}</button>)}</div>
          {advance.error && <p className="mt-3 text-xs text-rose-300">{advance.error.message}</p>}
          {result && <div className="mt-4 rounded-xl border border-accent/20 bg-accent/[.05] p-4"><span className="text-xs text-accent">Clock advanced to {result.simulated_date}</span><div className="mt-3 grid grid-cols-2 gap-3"><div><span className="text-[10px] text-slate-500">Simulated portfolio profit</span><strong className="mt-1 block">${result.actual_simulated_profit.toFixed(2)}</strong></div><div><span className="text-[10px] text-slate-500">Portfolio baseline profit</span><strong className="mt-1 block">${result.counterfactual_profit.toFixed(2)}</strong></div><div><span className="text-[10px] text-slate-500">Portfolio simulated difference</span><strong className="mt-1 block text-accent">${result.profit_uplift.toFixed(2)}</strong></div><div><span className="text-[10px] text-slate-500">Forecast error</span><strong className="mt-1 block">{result.mape_pct == null ? "Not measured" : `${result.mape_pct.toFixed(2)}%`}</strong></div></div><p className="mb-0 mt-2 text-[10px] text-slate-500">Portfolio comparison is not a recommendation outcome. A forecast error is unavailable because no independent profit forecast is evaluated.</p>{result.measured_outcomes.length > 0 && <div className="mt-3 border-t border-line pt-3"><strong className="text-xs text-slate-200">Campaign-specific recommendation outcomes measured</strong><div className="mt-2 space-y-2">{result.measured_outcomes.map((outcome) => <div key={outcome.recommendation_id} className="flex items-center justify-between gap-3 text-xs"><span className="text-slate-300">{outcome.title}</span><span className={outcome.profit_uplift >= 0 ? "text-accent" : "text-rose-300"}>{outcome.profit_uplift >= 0 ? "+" : ""}${outcome.profit_uplift.toFixed(2)}</span></div>)}</div></div>}<p className="mb-0 mt-2 text-[10px] text-slate-500">Simulator comparison metadata recorded; forecasting models were not retrained.</p></div>}
        </Panel>
        <Panel title="Win / loss performance" subtitle="Current totals for measured executed recommendations; new results appear after a decision is measured">
          {hasMeasuredOutcomes ? <CompareChart data={[{ outcome: "Decision outcomes", wins: data?.wins ?? 0, losses: data?.losses ?? 0, ties: data?.ties ?? 0 }]} x="outcome" bars={["wins", "losses", "ties"]} /> : <p className="rounded-lg border border-line bg-[#0d1420] p-4 text-xs text-slate-400">No measured recommendation outcomes are available to chart yet.</p>}
          <div className="mt-4 space-y-2">{data?.models.slice(0, 6).map((model) => <div key={model.name + model.version} className="flex items-center justify-between rounded-lg border border-line bg-[#0d1420] px-3 py-2"><span><strong className="block text-xs">{model.name}</strong><span className="text-[10px] text-slate-500">{model.version} · {new Date(model.trained_at).toLocaleString()}</span></span><span className="text-[10px] text-accent">{model.metrics.forecast_mape_pct != null ? `Forecast MAPE ${model.metrics.forecast_mape_pct.toFixed(2)}%` : `${model.metrics.observations ?? "—"} observations · not retrained`}</span></div>)}</div>
        </Panel>
      </div>
    </div>}</Shell>;
}
