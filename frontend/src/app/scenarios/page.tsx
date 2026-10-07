"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";

type Scenario = { key: string; title: string; category: string; connector: string; anomaly: { severity: string; score: number; evidence: { summary: string; drivers: string[] } }; recommendation: { id: string; status: string; proposed_change_pct: number } };

export default function ScenariosPage() {
  const query = useQuery({ queryKey: ["scenarios"], queryFn: () => api<Scenario[]>("v1/scenarios") });
  return <Shell><Heading eyebrow="Scenario guide" title="Eight planted operator scenarios" description="Every case is persisted into simulator accounts, ads, products, inventory, daily observations, unified facts, anomaly diagnoses, and recommendations." />
    {query.error ? <LoadingError error={query.error} /> : <div className="grid gap-3 lg:grid-cols-2">{query.data?.map((scenario, index) => <Panel key={scenario.key} title={`${String(index + 1).padStart(2, "0")} · ${scenario.title}`} subtitle={`${scenario.category} · source ${scenario.connector}`}><p className="text-xs leading-relaxed text-slate-400">{scenario.anomaly.evidence.summary}</p><div className="mb-4 flex flex-wrap gap-2">{scenario.anomaly.evidence.drivers.map((driver) => <span key={driver} className="rounded-md border border-line bg-[#0d1420] px-2 py-1 text-[10px] text-slate-300">{driver}</span>)}</div><div className="flex items-center justify-between"><span className="text-[10px] uppercase tracking-wider text-slate-500">Risk {scenario.anomaly.score} · {scenario.anomaly.severity} · {scenario.recommendation.status} · {scenario.recommendation.proposed_change_pct > 0 ? "+" : ""}{scenario.recommendation.proposed_change_pct}%</span><Link className="rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-[#092019]" href={`/diagnosis?scenario=${scenario.key}`}>Open diagnosis</Link></div></Panel>)}</div>}</Shell>;
}
