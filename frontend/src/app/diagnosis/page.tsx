"use client";

import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense, useState } from "react";
import { Activity, BrainCircuit, Play, Send, ShieldAlert } from "lucide-react";
import { api } from "@/lib/api";
import { CompareChart } from "@/components/charts";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";

type Scenario = { key: string; title: string; category: string; anomaly: { id: string; severity: string; score: number; evidence: Record<string, unknown> }; recommendation: { id: string; status: string } };
type Diagnostics = { anomalies: Array<{ id: string; scenario_key: string; severity: string; metric: string; score: number; evidence: Record<string, unknown>; diagnosis?: { summary: string; drivers: string[]; decomposition: Record<string, unknown>; confidence: number } }>; agent_steps: Array<{ id: string; run_id: string; step_index: number; tool_name: string; input: Record<string, unknown>; output: Record<string, unknown> }> };
type MethodResult = { observations: number; campaigns: Array<{ campaign_id: string; campaign: string; dates: string[]; robust_z_mad: number[]; stl_residual: number[]; pelt_changepoints: number[] }> };

function formatValue(value: unknown, useFourDecimals: boolean): string {
  return typeof value === "number" && useFourDecimals ? value.toFixed(4) : String(value);
}

function DiagnosisContent() {
  const searchParams = useSearchParams();
  const [selected, setSelected] = useState(searchParams.get("scenario") ?? "stockout_risk");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [chatError, setChatError] = useState("");
  const [latestRunId, setLatestRunId] = useState("");
  const client = useQueryClient();
  const scenarios = useQuery({ queryKey: ["scenarios"], queryFn: () => api<Scenario[]>("v1/scenarios") });
  const diagnostics = useQuery({ queryKey: ["diagnostics"], queryFn: () => api<Diagnostics>("v1/diagnostics") });
  const methods = useQuery({ queryKey: ["anomaly-methods"], queryFn: () => api<MethodResult>("v1/anomaly-methods") });
  const run = useMutation({ mutationFn: () => api<{ run_id: string }>(`v1/diagnostics/${selected}/run`, { method: "POST", body: "{}" }), onSuccess: (result) => { setLatestRunId(result.run_id); void client.invalidateQueries({ queryKey: ["diagnostics"] }); } });
  const selectedScenario = scenarios.data?.find((item) => item.key === selected);
  const useFourDecimals = selected === "price_promo_cvr_drop";
  const selectedDiagnosis = diagnostics.data?.anomalies.find((item) => item.scenario_key === selected);
  const selectedMethod = methods.data?.campaigns.find((item) => item.campaign_id === selectedScenario?.anomaly?.evidence.campaign_id);
  const traceRunId = latestRunId || selectedScenario?.anomaly?.id;
  const trace = diagnostics.data?.agent_steps.filter((item) => item.run_id === traceRunId) ?? [];
  const decomposition = (selectedDiagnosis?.diagnosis?.decomposition as { components?: Record<string, { shapley_contribution_pct?: number }> } | undefined)?.components;
  const waterfall = Object.entries(decomposition ?? {}).map(([factor, value]) => ({ factor: factor.toUpperCase(), contribution: value.shapley_contribution_pct ?? 0, zero: 0 }));
  const traceJson = (value: Record<string, unknown>) => JSON.stringify(
    value,
    (_key, item: unknown) => typeof item === "number" && useFourDecimals
      ? item.toFixed(4)
      : item,
  );
  async function ask() {
    if (!question.trim()) return;
    setChatError("");
    try {
      const result = await api<{ answer: string }>("v1/chat", { method: "POST", body: JSON.stringify({ question, scenario_key: selected }) });
      setAnswer(result.answer);
    } catch (error) {
      setChatError(error instanceof Error ? error.message : "The explanation request failed.");
    }
  }
  return <Shell><Heading eyebrow="Diagnosis & reasoning" title="Anomaly & Diagnosis Studio" description="Review ranked evidence, factor-level performance decomposition, statistical detection signals, and a persisted tool-by-tool diagnostic trace." />
    {scenarios.error || diagnostics.error || methods.error ? <LoadingError error={scenarios.error ?? diagnostics.error ?? methods.error} /> : <>
      <div className="grid gap-4 xl:grid-cols-[300px_1fr]"><Panel title="Scenario signals" subtitle="Select an evidence-backed anomaly"><div className="space-y-2">{scenarios.data?.map((scenario) => <button key={scenario.key} onClick={() => { setSelected(scenario.key); setLatestRunId(""); }} className={`w-full rounded-lg border p-3 text-left ${selected === scenario.key ? "border-accent/40 bg-accent/[.06]" : "border-line bg-[#0d1420]"}`}><span className="flex items-center justify-between gap-2"><strong className="text-xs">{scenario.title}</strong><span className="text-[10px] uppercase text-slate-500">{scenario.anomaly.severity}</span></span><span className="mt-1 block text-[10px] text-slate-500">{scenario.category} · risk {formatValue(scenario.anomaly.score, useFourDecimals && scenario.key === selected)}</span></button>)}</div></Panel>
        <div className="space-y-4"><Panel title={selectedScenario?.title ?? "Selected diagnosis"} subtitle="Diagnosis uses measured facts and describes drivers as hypotheses">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{["ctr", "cvr", "cpa", "roas", "inventory_cover_days", "reconciliation_factor"].map((metric) => <div key={metric} className="rounded-lg border border-line bg-[#0d1420] p-3"><span className="text-[10px] uppercase tracking-wide text-slate-500">{metric.replaceAll("_", " ")}</span><strong className="mt-1 block text-sm text-white">{formatValue(selectedScenario?.anomaly.evidence[metric] ?? "—", useFourDecimals)}</strong></div>)}</div>
          <div className="mt-4 rounded-lg border border-line bg-[#0d1420] p-3"><div className="mb-2 flex items-center gap-2 text-xs font-medium"><ShieldAlert size={14} className="text-amber-200" />Evidence & likely drivers</div><p className="text-xs leading-relaxed text-slate-400">{String(selectedScenario?.anomaly.evidence.summary ?? "")}</p><ul className="mb-0 mt-2 list-disc space-y-1 pl-4 text-xs text-slate-300">{((selectedScenario?.anomaly.evidence.drivers ?? []) as string[]).map((driver) => <li key={driver}>{driver}</li>)}</ul></div>
          <button onClick={() => run.mutate()} disabled={run.isPending} className="mt-4 flex items-center gap-2 rounded-lg bg-accent px-3 py-2 text-xs font-semibold text-[#092019]"><Play size={13} />{run.isPending ? "Running reasoning tools…" : "Run diagnosis trace"}</button>{run.error && <p className="mt-2 text-xs text-rose-300">{run.error.message}</p>}
        </Panel>
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="ROAS factor decomposition" subtitle="Contribution of measured CTR, CVR, AOV, and CPC movement against the recent baseline">
            {waterfall.length ? <CompareChart data={waterfall} x="factor" bars={["contribution", "zero"]} valueFormatter={useFourDecimals ? (value) => value.toFixed(4) : undefined} /> : <p className="text-xs text-slate-500">Run diagnosis to compare the latest period with its preceding seven-day baseline.</p>}
            {selectedMethod && <div className="mt-3 grid grid-cols-3 gap-2 text-[10px]"><div className="rounded-lg bg-[#0d1420] p-2 text-slate-400">MAD latest <strong className="mt-1 block text-slate-100">{formatValue(selectedMethod.robust_z_mad.at(-1) ?? 0, useFourDecimals)}</strong></div><div className="rounded-lg bg-[#0d1420] p-2 text-slate-400">STL latest <strong className="mt-1 block text-slate-100">{formatValue(selectedMethod.stl_residual.at(-1) ?? 0, useFourDecimals)}</strong></div><div className="rounded-lg bg-[#0d1420] p-2 text-slate-400">PELT breaks <strong className="mt-1 block text-slate-100">{selectedMethod.pelt_changepoints.length}</strong></div></div>}
          </Panel>
          <Panel title="Tool-using reasoning trace" subtitle="Query facts → decompose → check inventory → fatigue → tracking"><div className="space-y-2">{trace.map((step) => <div key={step.id} className="flex gap-3 rounded-lg bg-[#0d1420] p-3"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent/10 text-[10px] text-accent">{step.step_index}</span><span><strong className="text-xs">{step.tool_name}</strong><code className="mt-1 block whitespace-pre-wrap break-all text-[10px] text-slate-500">{traceJson(step.output)}</code></span></div>)}{!trace.length && <p className="text-xs text-slate-500">Run a diagnosis to write a new evidence trace.</p>}</div></Panel>
          <Panel title="Ask about this evidence" subtitle="Provider-backed explanation if configured; otherwise deterministic, evidence-bound response"><div className="flex gap-2"><input value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void ask()} placeholder="Why did performance change?" className="min-w-0 flex-1 rounded-lg border border-line bg-[#0d1420] px-3 py-2 text-xs outline-none focus:border-accent/40" /><button onClick={() => void ask()} className="rounded-lg bg-slate-700 px-3 text-white"><Send size={14} /></button></div>{chatError && <p className="mt-2 text-xs text-rose-300">{chatError}</p>}{answer && <p className="mt-3 rounded-lg bg-[#0d1420] p-3 text-xs leading-relaxed text-slate-300">{answer}</p>}<div className="mt-5 flex items-center gap-2 text-[10px] text-slate-500"><BrainCircuit size={13} />Statistical methods are computed per campaign on persisted facts ({methods.data?.observations ?? 0} observations).</div></Panel>
        </div></div>
      </div>
    </>}</Shell>;
}

export default function DiagnosisPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-background" />}>
      <DiagnosisContent />
    </Suspense>
  );
}
