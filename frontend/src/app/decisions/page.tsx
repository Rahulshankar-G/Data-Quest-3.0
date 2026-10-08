"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { RotateCcw, ShieldCheck, ShieldX } from "lucide-react";
import { api } from "@/lib/api";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";

type Decision = { id: string; title: string; rationale: string; scenario_key: string; proposed_change_pct: number; priority_score: number; status: string; constraints: Record<string, unknown>; payload: Record<string, unknown>; execution?: { id: string; status: string; mode: string; before_state: Record<string, unknown>; after_state: Record<string, unknown> } | null };
const col = createColumnHelper<Decision>();

export default function DecisionsPage() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["decisions"], queryFn: () => api<Decision[]>("v1/decisions") });
  const approve = useMutation({ mutationFn: (id: string) => api(`v1/recommendations/${id}/approve`, { method: "POST", body: JSON.stringify({ approved_by: "Demo Operator" }) }), onSuccess: () => client.invalidateQueries() });
  const revokeApproval = useMutation({ mutationFn: (id: string) => api(`v1/recommendations/${id}/revoke-approval`, { method: "POST", body: JSON.stringify({ revoked_by: "Demo Operator" }) }), onSuccess: () => client.invalidateQueries() });
  const execute = useMutation({ mutationFn: (id: string) => api(`v1/recommendations/${id}/execute`, { method: "POST", body: "{}" }), onSuccess: () => client.invalidateQueries() });
  const rollback = useMutation({ mutationFn: (id: string) => api(`v1/executions/${id}/rollback`, { method: "POST", body: "{}" }), onSuccess: () => client.invalidateQueries() });
  const columns = [
    col.accessor("title", { header: "Recommendation", cell: (info) => <span><strong className="block text-slate-200">{info.getValue()}</strong><span className="mt-1 block text-[10px] text-slate-500">{info.row.original.scenario_key}</span></span> }),
    col.accessor("proposed_change_pct", { header: "Proposed change", cell: (info) => `${info.getValue() > 0 ? "+" : ""}${info.getValue()}%` }),
    col.accessor("priority_score", { header: "Priority", cell: (info) => `${Math.round(info.getValue())}/100` }),
    col.accessor("status", { header: "Stage", cell: (info) => <span className="rounded-full bg-slate-800 px-2 py-1 text-[10px] uppercase">{info.getValue()}</span> }),
    col.display({ id: "actions", header: "Operator actions", cell: (info) => {
      const item = info.row.original;
      return <div className="flex flex-wrap gap-2">{item.status === "proposed" && <button onClick={() => approve.mutate(item.id)} className="rounded-md border border-line px-2.5 py-1.5 text-[10px] hover:border-accent/50">Approve</button>}{item.status === "approved" && <><button onClick={() => execute.mutate(item.id)} className="rounded-md bg-accent px-2.5 py-1.5 text-[10px] font-semibold text-[#092019]">Execute simulator</button><button onClick={() => revokeApproval.mutate(item.id)} className="flex items-center gap-1 rounded-md border border-rose-900/70 px-2.5 py-1.5 text-[10px] text-rose-200 hover:border-rose-700"><ShieldX size={11} />Revoke approval</button></>}{item.execution?.status === "executed" && <button onClick={() => rollback.mutate(item.execution!.id)} className="flex items-center gap-1 rounded-md border border-amber-800/70 px-2.5 py-1.5 text-[10px] text-amber-200"><RotateCcw size={11} />Rollback</button>}</div>;
    } }),
  ];
  const table = useReactTable({ data: query.data ?? [], columns, getCoreRowModel: getCoreRowModel() });
  const errors = [approve.error, revokeApproval.error, execute.error, rollback.error].filter(Boolean).map((error) => error?.message).join(" · ");
  return <Shell><Heading eyebrow="Operator decision workflow" title="Decision Center" description="Review recommendations, approve or revoke approval before action, simulate provider execution, and roll back the exact prior budget state." />
    {query.isLoading || query.error ? <LoadingError error={query.error} /> : <div className="space-y-4"><div className="grid gap-3 sm:grid-cols-4">{["proposed", "approved", "executed", "measured"].map((stage) => <div key={stage} className="rounded-xl border border-line bg-surface p-4"><span className="text-xs capitalize text-slate-500">{stage}</span><strong className="mt-2 block text-2xl">{query.data?.filter((item) => item.status === stage || (stage === "measured" && item.status === "measured")).length ?? 0}</strong></div>)}</div>
      <Panel title="Approval and execution queue" subtitle="Simulator writes are reversible; external account changes are not enabled"><div className="overflow-x-auto"><table className="w-full min-w-[800px] text-left text-xs"><thead><tr>{table.getHeaderGroups()[0].headers.map((header) => <th key={header.id} className="border-b border-line px-3 py-3 text-[10px] uppercase tracking-wider text-slate-500">{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr></thead><tbody>{table.getRowModel().rows.map((row) => <tr key={row.id} className="border-b border-line/70">{row.getVisibleCells().map((cell) => <td key={cell.id} className="px-3 py-3 align-top text-slate-300">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table></div>{errors && <p className="mt-3 text-xs text-rose-300">{errors}</p>}</Panel>
      <Panel title="Execution guardrails"><div className="space-y-2 text-xs text-slate-400"><p className="flex gap-2"><ShieldCheck className="text-accent" size={15} />Operator approval required for each proposed change.</p><p className="flex gap-2"><ShieldCheck className="text-accent" size={15} />Approved recommendations can be returned to proposed status before execution.</p><p className="flex gap-2"><ShieldCheck className="text-accent" size={15} />Positive budget shifts blocked below 5 days of inventory cover.</p><p className="flex gap-2"><ShieldCheck className="text-accent" size={15} />All current executions run through platform simulator connectors only.</p></div></Panel>
    </div>}</Shell>;
}
