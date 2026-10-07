"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Background, Controls, MiniMap, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "@/lib/api";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";
import { Button } from "@/components/ui/button";

type Source = { key: string; name: string; mode: string; status: string; health_score: number; record_counts: Record<string, number> };
type Lineage = { nodes: Array<{ id: string; label: string; kind: string }>; edges: Array<{ id: string; source: string; target: string }> };
const positions: Record<string, { x: number; y: number }> = {
  meta: { x: 20, y: 25 }, google: { x: 20, y: 115 }, amazon: { x: 20, y: 205 },
  tiktok: { x: 20, y: 295 }, programmatic: { x: 20, y: 385 }, shopify: { x: 20, y: 475 },
  ga4: { x: 20, y: 565 }, reconciliation: { x: 300, y: 240 }, "unified-facts": { x: 590, y: 240 },
  "anomaly-engine": { x: 880, y: 150 }, "decision-engine": { x: 1150, y: 250 },
  execution: { x: 1420, y: 220 }, outcomes: { x: 1690, y: 250 },
};

export default function DataHub() {
  const [uploadResult, setUploadResult] = useState<Record<string, unknown> | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [mappingPrompt, setMappingPrompt] = useState<{ columns: string[]; required: string[]; fields: string[] } | null>(null);
  const sources = useQuery({ queryKey: ["sources"], queryFn: () => api<Source[]>("v1/sources") });
  const lineage = useQuery({ queryKey: ["lineage"], queryFn: () => api<Lineage>("v1/lineage") });
  async function upload(file?: File, selectedMapping?: Record<string, string | null>) {
    if (!file) return;
    setUploadError("");
    setUploadResult(null);
    setSelectedFile(file);
    const body = new FormData();
    body.set("file", file);
    if (selectedMapping) body.set("column_mapping", JSON.stringify(selectedMapping));
    try {
      const result = await api<Record<string, unknown>>("v1/ingest/upload", { method: "POST", body });
      setUploadResult(result);
      if (result.status === "mapping_required") {
        const suggestions = result.suggestions as Record<string, string | null>;
        setMapping(suggestions);
        setMappingPrompt({
          columns: result.columns as string[],
          required: result.required as string[],
          fields: result.fields as string[],
        });
      } else {
        setSelectedFile(null);
        setMappingPrompt(null);
        void lineage.refetch();
        void sources.refetch();
      }
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Upload failed");
    }
  }
  const requiredFields = new Set(mappingPrompt?.required ?? ["date", "spend", "revenue"]);
  const nodes: Node[] = (lineage.data?.nodes ?? []).map((node, index) => ({
    id: node.id,
    position: positions[node.id.replace("source-", "")] ?? { x: 760 + (index % 3) * 210, y: 400 + Math.floor(index / 3) * 90 },
    data: { label: <div className="rounded-lg border border-slate-700 bg-[#111827] px-3 py-2 text-xs text-slate-200"><span className="mb-1 block text-[9px] uppercase tracking-widest text-emerald-300">{node.kind}</span>{node.label}</div> },
    style: { width: 185, background: "transparent", border: 0 },
  }));
  const edges: Edge[] = (lineage.data?.edges ?? []).map((edge) => ({ ...edge, animated: edge.id.includes("loop") }));
  return <Shell><Heading eyebrow="Ingest & reconcile" title="Unified Data Hub" description="Inspect simulator and import-source health, map tabular uploads, and trace normalized data through decisions and measured outcomes." />
    {sources.error || lineage.error ? <LoadingError error={sources.error ?? lineage.error} /> : <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{sources.data?.slice(0, 7).map((source) => <div key={source.key} className="rounded-xl border border-line bg-surface p-4"><div className="flex items-center justify-between"><strong className="text-sm">{source.name}</strong><span className="h-2 w-2 rounded-full bg-emerald-300" /></div><div className="mt-2 flex items-center justify-between text-xs text-slate-500"><span>{source.mode} · health {source.health_score}%</span><span>{source.record_counts.campaigns} campaigns</span></div></div>)}</div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <Panel title="Data lineage" subtitle="Source records flow through proportional reconciliation, analysis, simulator execution, and learning">
          <div className="h-[570px] overflow-hidden rounded-xl border border-line"><ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: .12 }} nodesDraggable nodesConnectable={false}><Background color="#253244" gap={18} /><Controls /><MiniMap nodeColor="#75e0c0" maskColor="rgba(9,13,22,.8)" /></ReactFlow></div>
        </Panel>
        <div className="space-y-4">
          <Panel title="Import campaign data" subtitle="Map CSV or JSON columns, validate schema, then persist campaign-day facts">
            <label
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => { event.preventDefault(); void upload(event.dataTransfer.files[0]); }}
              className="flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-slate-600 bg-[#0c121e] px-4 text-center hover:border-accent/60"
            ><span className="text-sm font-medium">Drop a CSV / JSON or browse</span><span className="mt-2 text-xs text-slate-500">Up to 10 MB and 100,000 rows · date, spend, revenue required</span><input className="sr-only" type="file" accept=".csv,.json,application/json,text/csv" onChange={(event) => void upload(event.target.files?.[0])} /></label>
            {mappingPrompt && <div className="mt-4 rounded-xl border border-line bg-[#0c121e] p-3"><div className="mb-3"><strong className="text-xs">Map source columns</strong><p className="mb-0 mt-1 text-[10px] text-slate-500">Required fields are marked. Unmapped optional fields are safely set to zero.</p></div><div className="space-y-2">{mappingPrompt.fields.map((field) => <label key={field} className="grid grid-cols-[1fr_1.2fr] items-center gap-2 text-xs"><span className="capitalize text-slate-300">{field.replaceAll("_", " ")} {requiredFields.has(field) && <span className="text-rose-300">*</span>}</span><select value={mapping[field] ?? ""} onChange={(event) => setMapping((current) => ({ ...current, [field]: event.target.value || null }))} className="min-w-0 rounded-lg border border-line bg-[#111827] px-2 py-2 text-xs text-slate-200"><option value="">Not mapped</option>{mappingPrompt.columns.map((column) => <option key={column} value={column}>{column}</option>)}</select></label>)}</div><Button size="sm" className="mt-4 w-full" disabled={!selectedFile || mappingPrompt.required.some((field) => !mapping[field])} onClick={() => void upload(selectedFile ?? undefined, mapping)}>Validate mapping & import</Button></div>}
            {uploadError && <p className="mt-3 text-xs text-rose-300">{uploadError}</p>}
            {uploadResult && <pre className="mt-3 max-h-48 overflow-auto rounded-lg bg-[#090d16] p-3 text-xs text-accent">{JSON.stringify(uploadResult, null, 2)}</pre>}
          </Panel>
          <Panel title="Reconciliation policy" subtitle="Revenue credits are capped at observed store sales and distributed by platform-reported contribution">
            <div className="space-y-3 text-xs leading-relaxed text-slate-400"><div className="flex justify-between border-b border-line pb-2"><span>Attribution method</span><span className="text-slate-200">Proportional, SKU × day</span></div><div className="flex justify-between border-b border-line pb-2"><span>Profit equation</span><span className="text-slate-200">Net revenue − COGS − spend − discounts</span></div><div className="flex justify-between border-b border-line pb-2"><span>Simulator tables</span><span className="text-slate-200">Orders · ads · GA events</span></div><div className="flex justify-between"><span>Source records</span><span className="text-slate-200">{sources.data?.[0]?.record_counts.facts ?? 0} unified facts</span></div></div>
          </Panel>
        </div>
      </div>
    </>}</Shell>;
}
