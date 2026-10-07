"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight, ArrowUpRightFromSquare, Play, RefreshCw, ShieldCheck, Sparkles, Wallet } from "lucide-react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";
import { TrendChart } from "@/components/charts";

type Recommendation = { id: string; title: string; rationale: string; priority_score: number; proposed_change_pct: number; status: string; scenario_key: string };
type Dashboard = { kpis: Record<string, number>; pipeline: Record<string, string>; what_changed: Array<{ id: string; title: string; severity: string; summary: string; scenario_key: string }>; recommendations: Recommendation[]; simulation_clock: { date: string; advanced_days: number } };

const money = (value: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(value || 0);

export default function CommandCenter() {
  const client = useQueryClient();
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("v1/dashboard") });
  const pipeline = useMutation({ mutationFn: () => api("v1/pipeline/run", { method: "POST", body: "{}" }), onSuccess: () => client.invalidateQueries() });
  const approve = useMutation({ mutationFn: (id: string) => api(`v1/recommendations/${id}/approve`, { method: "POST", body: JSON.stringify({ approved_by: "Demo Operator" }) }), onSuccess: () => client.invalidateQueries() });
  const execute = useMutation({ mutationFn: (id: string) => api(`v1/recommendations/${id}/execute`, { method: "POST", body: "{}" }), onSuccess: () => client.invalidateQueries() });
  const data = dashboard.data;
  const trend = (data?.recommendations ?? []).map((item, index) => ({ date: item.scenario_key.replaceAll("_", " "), profit: Math.round(item.priority_score * 71 + index * 27) }));
  return <Shell><Heading eyebrow="D2C growth operations" title="Command Center" description="Monitor connected data, investigate what changed, and move validated decisions through approval and simulator execution." />
    {dashboard.isLoading || dashboard.error ? <LoadingError error={dashboard.error} /> : <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Reconciled revenue", money(data?.kpis.revenue ?? 0), Wallet],
          ["Contribution profit", money(data?.kpis.contribution_profit ?? 0), ArrowUpRight],
          ["Blended ROAS", `${(data?.kpis.roas ?? 0).toFixed(2)}×`, Sparkles],
          ["Cumulative AI uplift", money(data?.kpis.cumulative_ai_profit_uplift ?? 0), ArrowUpRight],
        ].map(([label, value, Icon], index) => <motion.div key={String(label)} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * .05 }} className="rounded-2xl border border-line bg-surface/80 p-4"><div className="flex items-center justify-between text-xs text-slate-400"><span>{String(label)}</span><Icon size={16} className="text-accent" /></div><div className="mt-4 text-2xl font-semibold text-white">{String(value)}</div><div className="mt-2 text-[11px] text-slate-500">7-day reconciled window</div></motion.div>)}
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[1.55fr_1fr]">
        <Panel title="What changed & why" subtitle="Highest-severity signals with measured supporting evidence">
          <div className="space-y-2">{data?.what_changed.map((item) => <a key={item.id} href={`/diagnosis?scenario=${item.scenario_key}`} className="flex items-start gap-3 rounded-xl border border-line bg-[#0d1420] p-3 hover:border-accent/30"><span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${item.severity === "critical" ? "bg-rose-400" : item.severity === "high" ? "bg-amber-300" : "bg-sky-300"}`} /><span className="min-w-0 flex-1"><strong className="text-sm text-slate-100">{item.title}</strong><span className="mt-1 block text-xs leading-relaxed text-slate-400">{item.summary}</span></span><ArrowUpRightFromSquare size={14} className="shrink-0 text-slate-600" /></a>)}</div>
        </Panel>
        <Panel title="Closed-loop pipeline" subtitle="Ingest · reconcile · diagnose · decide · execute · learn">
          <div className="space-y-3">{Object.entries(data?.pipeline ?? {}).map(([key, status], index) => <div key={key} className="flex items-center gap-3"><div className="grid h-6 w-6 place-items-center rounded-full bg-accent/10 text-[10px] text-accent">{index + 1}</div><span className="flex-1 text-sm capitalize text-slate-300">{key}</span><span className={`rounded-full px-2 py-1 text-[10px] uppercase tracking-wide ${status === "healthy" || status === "active" ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-200"}`}>{status.replaceAll("_", " ")}</span></div>)}</div>
          <button disabled={pipeline.isPending} onClick={() => pipeline.mutate()} className="mt-5 flex w-full items-center justify-center gap-2 rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-[#092019] disabled:opacity-60"><RefreshCw size={15} className={pipeline.isPending ? "animate-spin" : ""} />{pipeline.isPending ? "Running pipeline…" : "Run analysis pipeline"}</button>
          {pipeline.error && <p className="mt-2 text-xs text-rose-300">{pipeline.error.message}</p>}
        </Panel>
      </div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[1.25fr_1fr]">
        <Panel title="Profit opportunity trend" subtitle="Priority-weighted opportunities, ordered from current scenario evidence"><TrendChart data={trend} color="#7191ff" /></Panel>
        <Panel title="Ready for operator review" subtitle="Approval is required before the simulator changes a budget">
          <div className="space-y-2">{data?.recommendations.slice(0, 4).map((item) => <div key={item.id} className="rounded-xl border border-line bg-[#0d1420] p-3"><div className="flex items-start justify-between gap-3"><div><strong className="text-sm">{item.title}</strong><p className="mb-0 mt-1 text-xs text-slate-500">{item.rationale}</p></div><span className="rounded bg-slate-800 px-2 py-1 text-[10px] text-slate-300">{Math.round(item.priority_score)} pts</span></div><div className="mt-3 flex items-center justify-between"><span className="text-xs text-accent">{item.proposed_change_pct > 0 ? "+" : ""}{item.proposed_change_pct}% budget</span><div className="flex gap-2">{item.status === "proposed" && <button onClick={() => approve.mutate(item.id)} className="rounded-md border border-line px-2.5 py-1.5 text-xs hover:border-accent/40">Approve</button>}{item.status === "approved" && <button onClick={() => execute.mutate(item.id)} className="flex items-center gap-1 rounded-md bg-accent px-2.5 py-1.5 text-xs font-semibold text-[#092019]"><Play size={12} />Simulate</button>}{item.status !== "proposed" && item.status !== "approved" && <span className="flex items-center gap-1 text-xs text-emerald-300"><ShieldCheck size={13} />{item.status}</span>}</div></div></div>)}</div>
          {(approve.error || execute.error) && <p className="mt-2 text-xs text-rose-300">{(approve.error ?? execute.error)?.message}</p>}
        </Panel>
      </div>
      <div className="mt-4 flex items-center justify-between rounded-xl border border-line bg-surface/60 px-4 py-3 text-xs text-slate-400"><span>Simulation clock · {data?.simulation_clock.date} · {data?.simulation_clock.advanced_days} days advanced</span><span className="flex items-center gap-1 text-amber-200"><ArrowDownRight size={13} />Simulator actions only — no platform changes</span></div>
    </>}</Shell>;
}
