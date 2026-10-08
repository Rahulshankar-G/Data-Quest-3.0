"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { Background, Controls, Handle, MiniMap, Position, ReactFlow, type Edge, type MiniMapNodeProps, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "@/lib/api";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";
import { Button } from "@/components/ui/button";

type Source = { key: string; name: string; mode: string; status: string; health_score: number; record_counts: Record<string, number> };
type Lineage = { nodes: Array<{ id: string; label: string; kind: string }>; edges: Array<{ id: string; source: string; target: string }> };
type LineageNodeData = { label: string; kind: string };
const nodeWidth = 250;
const nodeHeight = 108;
const nodeColors: Record<string, { accent: string; background: string }> = {
  source: { accent: "#75e0c0", background: "#142c2b" },
  process: { accent: "#f6bd60", background: "#302719" },
  dataset: { accent: "#70a5ff", background: "#192943" },
  product: { accent: "#b69cff", background: "#29223b" },
  model: { accent: "#ff8f9c", background: "#33232b" },
  execution: { accent: "#ffad73", background: "#35281f" },
};
const positions: Record<string, { x: number; y: number }> = {
  reconciliation: { x: 280, y: 330 },
  "unified-facts": { x: 540, y: 330 },
  "anomaly-engine": { x: 800, y: 210 },
  "decision-engine": { x: 1060, y: 330 },
  execution: { x: 1320, y: 330 },
  outcomes: { x: 1580, y: 330 },
};

const nodeTypes = { lineage: LineageFlowNode };

function LineageFlowNode({ id, data }: NodeProps<Node<LineageNodeData>>) {
  const colors = nodeColors[data.kind] ?? nodeColors.dataset;
  const isProduct = data.kind === "product";
  const isFacts = id === "unified-facts";
  return (
    <div
      className="lineage-node"
      style={{
        borderColor: colors.accent,
        backgroundColor: colors.background,
        ["--lineage-node-accent" as string]: colors.accent,
      }}
    >
      {!isProduct && <Handle type="target" position={Position.Left} id="target-left" />}
      {isProduct && <Handle type="target" position={Position.Top} id="target-top" />}
      {!isProduct && <Handle type="source" position={Position.Right} id="source-right" />}
      {isFacts && <Handle type="source" position={Position.Bottom} id="source-bottom" />}
      <span className="lineage-node__kind" style={{ color: colors.accent }}>{data.kind}</span>
      <span className="lineage-node__label">{data.label}</span>
    </div>
  );
}

export default function DataHub() {
  const fileInput = useRef<HTMLInputElement>(null);
  const [uploadResult, setUploadResult] = useState<Record<string, unknown> | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [uploading, setUploading] = useState(false);
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
    setUploading(true);
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
    } finally {
      setUploading(false);
    }
  }
  function healthColor(score: number) {
    const amount = Math.min(100, Math.max(0, score)) / 100;
    const red = Math.round(239 + (34 - 239) * amount);
    const green = Math.round(68 + (197 - 68) * amount);
    const blue = Math.round(68 + (94 - 68) * amount);
    return `#${[red, green, blue].map((value) => value.toString(16).padStart(2, "0")).join("")}`;
  }
  const requiredFields = new Set(mappingPrompt?.required ?? ["date", "spend", "revenue"]);
  const nodes: Node[] = useMemo(
    () => {
      let productIndex = 0;
      return (lineage.data?.nodes ?? []).map((node, index) => {
        const position = node.kind === "source"
          ? { x: 20, y: 20 + index * 120 }
          : node.kind === "product"
            ? { x: 560 + (productIndex % 3) * 285, y: 590 + Math.floor(productIndex++ / 3) * 155 }
            : positions[node.id] ?? { x: 320, y: 1000 + index * 130 };
        return {
          id: node.id,
          type: "lineage",
          position,
          data: { label: node.label, kind: node.kind },
          width: nodeWidth,
          height: nodeHeight,
          style: { width: nodeWidth, height: nodeHeight, background: "transparent", border: 0 },
        };
      });
    },
    [lineage.data],
  );
  const edges: Edge[] = useMemo(
    () => {
      const nodeById = new Map((lineage.data?.nodes ?? []).map((node) => [node.id, node]));
      return (lineage.data?.edges ?? []).map((edge) => {
        const targetIsProduct = nodeById.get(edge.target)?.kind === "product";
        return {
          ...edge,
          sourceHandle: targetIsProduct ? "source-bottom" : "source-right",
          targetHandle: targetIsProduct ? "target-top" : "target-left",
          animated: edge.id.includes("loop"),
        };
      });
    },
    [lineage.data],
  );
  const minimapNodeComponent = useMemo(() => {
    const nodeById = new Map((lineage.data?.nodes ?? []).map((node) => [node.id, node]));
    const positionById = new Map(nodes.map((node) => [node.id, node.position]));
    const edgePaths = edges.flatMap((edge) => {
      const source = positionById.get(edge.source);
      const target = positionById.get(edge.target);
      if (!source || !target) return [];
      const targetIsProduct = nodeById.get(edge.target)?.kind === "product";
      if (targetIsProduct) {
        const startX = source.x + nodeWidth / 2;
        const startY = source.y + nodeHeight;
        const endX = target.x + nodeWidth / 2;
        const endY = target.y;
        const controlOffset = Math.max(35, (endY - startY) * 0.45);
        return [`M ${startX} ${startY} C ${startX} ${startY + controlOffset}, ${endX} ${endY - controlOffset}, ${endX} ${endY}`];
      }
      const startX = source.x + nodeWidth;
      const startY = source.y + nodeHeight / 2;
      const endX = target.x;
      const endY = target.y + nodeHeight / 2;
      const controlOffset = Math.max(35, (endX - startX) * 0.45);
      return [`M ${startX} ${startY} C ${startX + controlOffset} ${startY}, ${endX - controlOffset} ${endY}, ${endX} ${endY}`];
    });
    const firstNodeId = nodes[0]?.id;
    return function LineageMiniMapNode({ id, x, y, width, height, selected }: MiniMapNodeProps) {
      const kind = nodeById.get(id)?.kind ?? "dataset";
      const accent = nodeColors[kind]?.accent ?? nodeColors.dataset.accent;
      const labelLength = nodeById.get(id)?.label.length ?? 12;
      const detailWidth = Math.max(22, Math.min(width * 0.68, labelLength * 2.3));
      return (
        <g className="data-lineage-minimap-node" aria-hidden="true">
          {id === firstNodeId && edgePaths.map((path, index) => (
            <path key={index} d={path} fill="none" stroke="#718096" strokeOpacity=".72" strokeWidth="5" />
          ))}
          <rect
            x={x}
            y={y}
            width={width}
            height={height}
            rx="9"
            fill="#131c2d"
            stroke={selected ? "#f4f7fb" : accent}
            strokeWidth={selected ? "8" : "5"}
          />
          <path
            d={`M ${x + 9} ${y + 5} H ${x + width - 9}`}
            stroke={accent}
            strokeWidth="7"
            strokeLinecap="round"
          />
          <rect
            x={x + 9}
            y={y + height * 0.39}
            width={detailWidth}
            height="5"
            rx="2.5"
            fill="#dce5f2"
            fillOpacity=".9"
          />
          <rect
            x={x + 9}
            y={y + height * 0.58}
            width={Math.max(18, detailWidth * 0.62)}
            height="4"
            rx="2"
            fill="#8391a7"
            fillOpacity=".8"
          />
        </g>
      );
    };
  }, [edges, lineage.data, nodes]);
  return <Shell><Heading eyebrow="Ingest & reconcile" title="Unified Data Hub" description="Inspect simulator and import-source health, map tabular uploads, and trace normalized data through decisions and measured outcomes." />
    {sources.error || lineage.error ? <LoadingError error={sources.error ?? lineage.error} /> : <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{sources.data?.map((source) => <div key={source.key} className="rounded-xl border border-line bg-surface p-4"><div className="flex items-center justify-between"><strong className="text-sm">{source.name}</strong><span className="h-2 w-2 rounded-full" style={{ backgroundColor: healthColor(source.health_score) }} title={`Ingestion health: ${source.health_score.toFixed(1)}%`} /></div><div className="mt-2 flex items-center justify-between text-xs text-slate-500"><span>{source.mode} · health {source.health_score.toFixed(1)}%</span><span>{source.record_counts.campaigns} campaigns</span></div></div>)}</div>
      <div className="mt-4 grid gap-4 xl:grid-cols-[1.5fr_1fr]">
        <Panel title="Data lineage" subtitle="Source records flow through proportional reconciliation, analysis, simulator execution, and learning">
          <div className="h-[570px] overflow-hidden rounded-xl border border-line"><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} nodesDraggable nodesConnectable={false} defaultViewport={{ x: 12, y: 70, zoom: .42 }} minZoom={.2}><Background color="#253244" gap={18} /><Controls position="top-right" /><MiniMap nodeComponent={minimapNodeComponent} maskColor="rgba(9,13,22,.78)" maskStrokeColor="#75e0c0" maskStrokeWidth={2} pannable zoomable /></ReactFlow></div>
        </Panel>
        <div className="space-y-4">
          <Panel title="Import campaign data" subtitle="Map CSV or JSON columns, validate schema, then persist campaign-day facts">
            <div
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => { event.preventDefault(); void upload(event.dataTransfer.files[0]); }}
              className="flex min-h-36 flex-col items-center justify-center rounded-xl border border-dashed border-slate-600 bg-[#0c121e] px-4 text-center hover:border-accent/60"
            ><span className="text-sm font-medium">{selectedFile?.name ?? "Drop a CSV / JSON file here"}</span><span className="mt-2 text-xs text-slate-500">Up to 10 MB and 100,000 rows · date, spend, revenue required</span><input ref={fileInput} className="sr-only" type="file" accept=".csv,.json,application/json,text/csv" onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ""; void upload(file); }} /><Button type="button" size="sm" variant="outline" className="mt-3" disabled={uploading} onClick={() => fileInput.current?.click()}>{uploading ? "Importing…" : "Browse files"}</Button></div>
            {mappingPrompt && <div className="mt-4 rounded-xl border border-line bg-[#0c121e] p-3"><div className="mb-3"><strong className="text-xs">Map source columns</strong><p className="mb-0 mt-1 text-[10px] text-slate-500">Required fields are marked. Unmapped optional fields are safely set to zero.</p></div><div className="space-y-2">{mappingPrompt.fields.map((field) => <label key={field} className="grid grid-cols-[1fr_1.2fr] items-center gap-2 text-xs"><span className="capitalize text-slate-300">{field.replaceAll("_", " ")} {requiredFields.has(field) && <span className="text-rose-300">*</span>}</span><select value={mapping[field] ?? ""} onChange={(event) => setMapping((current) => ({ ...current, [field]: event.target.value || null }))} className="min-w-0 rounded-lg border border-line bg-[#111827] px-2 py-2 text-xs text-slate-200"><option value="">Not mapped</option>{mappingPrompt.columns.map((column) => <option key={column} value={column}>{column}</option>)}</select></label>)}</div><Button size="sm" className="mt-4 w-full" disabled={!selectedFile || uploading || mappingPrompt.required.some((field) => !mapping[field])} onClick={() => void upload(selectedFile ?? undefined, mapping)}>{uploading ? "Importing…" : "Validate mapping & import"}</Button></div>}
            {uploadError && <p className="mt-3 text-xs text-rose-300">{uploadError}</p>}
            {uploadResult && <div className="mt-3 rounded-lg border border-accent/20 bg-[#090d16] p-3 text-xs text-slate-300"><strong className="text-accent">{uploadResult.status === "imported" ? `Imported ${uploadResult.accepted_records} of ${uploadResult.total_records} records` : "Column mapping required"}</strong>{uploadResult.status === "imported" && <span className="ml-2 text-slate-500">Duplicates are kept as separate records.</span>}</div>}
          </Panel>
          <Panel title="Reconciliation policy" subtitle="Revenue credits are capped at observed store sales and distributed by platform-reported contribution">
            <div className="space-y-3 text-xs leading-relaxed text-slate-400"><div className="flex justify-between border-b border-line pb-2"><span>Attribution method</span><span className="text-slate-200">Proportional, SKU × day</span></div><div className="flex justify-between border-b border-line pb-2"><span>Profit equation</span><span className="text-slate-200">Net revenue − COGS − spend − discounts</span></div><div className="flex justify-between border-b border-line pb-2"><span>Simulator tables</span><span className="text-slate-200">Orders · ads · GA events</span></div><div className="flex justify-between"><span>Source records</span><span className="text-slate-200">{sources.data?.[0]?.record_counts.facts ?? 0} unified facts</span></div></div>
          </Panel>
        </div>
      </div>
    </>}</Shell>;
}
