"use client";

import { useQuery } from "@tanstack/react-query";
import { ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, CartesianGrid } from "recharts";
import { api } from "@/lib/api";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";

type Opportunity = { id: string; scenario_key: string; title: string; kind: string; estimated_profit_uplift: number; confidence: number; features: Record<string, number | null>; status: string };

export default function OpportunityPage() {
  const query = useQuery({ queryKey: ["opportunities"], queryFn: () => api<Opportunity[]>("v1/opportunities") });
  const rows = query.data ?? [];
  const points = rows.map((row) => ({ ...row, x: row.confidence * 100, y: Math.max(0, row.estimated_profit_uplift), z: Math.max(14, Math.abs(row.estimated_profit_uplift) / 12) }));
  return <Shell><Heading eyebrow="Opportunity radar" title="Where can profitable growth come from?" description="Rank evidence-backed opportunities by modeled profit impact and confidence. Feature indicators are measured attributes, not causal explanations." />
    {query.isLoading || query.error ? <LoadingError error={query.error} /> : <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]"><Panel title="Impact × confidence matrix" subtitle="Bubble size scales with estimated contribution-profit opportunity"><div className="h-[390px]"><ResponsiveContainer width="100%" height="100%"><ScatterChart margin={{ top: 15, right: 15, bottom: 25, left: 15 }}><CartesianGrid stroke="#202b3b" strokeDasharray="4 4" /><XAxis type="number" dataKey="x" name="confidence" unit="%" domain={[0, 100]} tick={{ fill: "#94a3b8", fontSize: 11 }} label={{ value: "Model confidence", position: "insideBottom", fill: "#64748b" }} /><YAxis type="number" dataKey="y" name="profit opportunity" tick={{ fill: "#94a3b8", fontSize: 11 }} /><ZAxis type="number" dataKey="z" range={[70, 400]} /><Tooltip cursor={{ strokeDasharray: "3 3" }} contentStyle={{ background: "#0d1420", border: "1px solid #263244", borderRadius: 10 }} formatter={(value, name) => [name === "profit opportunity" ? `$${Number(value).toFixed(0)}` : `${value}%`, name]} /><Scatter data={points} fill="#75e0c0" fillOpacity={0.78} /></ScatterChart></ResponsiveContainer></div></Panel>
      <Panel title="Ranked opportunities" subtitle="Impact and relevant leading indicators">{rows.sort((a, b) => b.estimated_profit_uplift - a.estimated_profit_uplift).map((item) => <a key={item.id} href={`/diagnosis?scenario=${item.scenario_key}`} className="mb-2 block rounded-xl border border-line bg-[#0d1420] p-3 hover:border-accent/40"><div className="flex items-start justify-between gap-3"><div><span className="text-[9px] uppercase tracking-widest text-slate-500">{item.kind}</span><strong className="mt-1 block text-sm">{item.title}</strong></div><span className="text-sm text-accent">${item.estimated_profit_uplift.toFixed(0)}</span></div><div className="mt-3 flex gap-2 text-[10px] text-slate-500"><span>{Math.round(item.confidence * 100)}% confidence</span>{Object.entries(item.features).filter(([, value]) => value != null).slice(0, 3).map(([key, value]) => <span key={key} className="rounded bg-slate-800 px-2 py-1">{key.replaceAll("_", " ")} {Number(value).toFixed(2)}</span>)}</div></a>)}</Panel>
    </div>}</Shell>;
}
