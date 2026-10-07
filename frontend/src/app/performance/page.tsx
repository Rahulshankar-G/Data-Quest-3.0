"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createColumnHelper, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table";
import { api } from "@/lib/api";
import { CompareChart, TrendChart } from "@/components/charts";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";

type Daily = { date: string; spend: number; revenue: number; profit: number; clicks: number; impressions: number; conversions: number };
type PerformanceRow = { platform: string; campaign_id: string; campaign: string; sku: string | null; spend: number; revenue: number; attributed_revenue: number; profit: number; roas: number; ctr: number; cvr: number; aov: number; cpc: number; inventory_cover_days: number | null; days: Daily[] };
const helper = createColumnHelper<PerformanceRow>();
const columns = [
  helper.accessor("platform", { header: "Platform" }),
  helper.accessor("campaign", { header: "Campaign" }),
  helper.accessor("sku", { header: "SKU" }),
  helper.accessor("spend", { header: "Spend", cell: (info) => `$${info.getValue().toFixed(0)}` }),
  helper.accessor("revenue", { header: "Net sales", cell: (info) => `$${info.getValue().toFixed(0)}` }),
  helper.accessor("profit", { header: "Contribution profit", cell: (info) => <span className={info.getValue() < 0 ? "text-rose-300" : "text-emerald-300"}>${info.getValue().toFixed(0)}</span> }),
  helper.accessor("roas", { header: "ROAS", cell: (info) => `${info.getValue().toFixed(2)}×` }),
  helper.accessor("inventory_cover_days", { header: "Stock cover", cell: (info) => { const days = info.getValue(); return days == null ? "—" : `${days.toFixed(1)} d`; } }),
];

export default function PerformancePage() {
  const [platform, setPlatform] = useState("all");
  const dataQuery = useQuery({ queryKey: ["performance"], queryFn: () => api<{ rows: PerformanceRow[] }>("v1/performance") });
  const allRows = dataQuery.data?.rows ?? [];
  const platforms = useMemo(() => [...new Set(allRows.map((row) => row.platform))], [allRows]);
  const rows = useMemo(() => platform === "all" ? allRows : allRows.filter((row) => row.platform === platform), [allRows, platform]);
  const table = useReactTable({ data: rows, columns, getCoreRowModel: getCoreRowModel() });
  const selected = rows[0];
  const platformTotals = useMemo(() => {
    const grouped = new Map<string, { platform: string; spend: number; revenue: number }>();
    rows.forEach((row) => { const value = grouped.get(row.platform) ?? { platform: row.platform, spend: 0, revenue: 0 }; value.spend += row.spend; value.revenue += row.revenue; grouped.set(row.platform, value); });
    return [...grouped.values()];
  }, [rows]);
  return <Shell><Heading eyebrow="Performance explorer" title="Campaigns to contribution profit" description="Drill across platform, campaign, and SKU using store-reconciled sales, attributed media metrics, response efficiency, and stock-cover guardrails." />
    <div className="mb-4 flex flex-wrap items-center gap-3"><label className="text-xs text-slate-400" htmlFor="platform-filter">Platform</label><select id="platform-filter" value={platform} onChange={(event) => setPlatform(event.target.value)} className="rounded-lg border border-line bg-surface px-3 py-2 text-sm">{["all", ...platforms].map((value) => <option key={value} value={value}>{value === "all" ? "All platforms" : value}</option>)}</select><span className="text-xs text-slate-500">{table.getRowModel().rows.length} campaign × SKU groups</span></div>
    {dataQuery.isLoading || dataQuery.error ? <LoadingError error={dataQuery.error} /> : <>
      <div className="grid gap-4 xl:grid-cols-[1.5fr_1fr]"><Panel title={`Daily trend · ${selected?.campaign ?? "portfolio"}`} subtitle="Reconciled sales and contribution profit by day">{selected && <TrendChart data={selected.days} />}</Panel><Panel title="Channel spend vs sales" subtitle="Use net store revenue for portfolio reporting; ad attributed revenue remains visible in the grid"><CompareChart data={platformTotals.map((item) => ({ ...item, revenue: Math.round(item.revenue), spend: Math.round(item.spend) }))} x="platform" bars={["spend", "revenue"]} /></Panel></div>
      <Panel title="Performance grid" subtitle="Platform → campaign → SKU with both platform attribution and reconciled outcomes" className="mt-4"><div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-[10px] uppercase tracking-wide text-slate-500"><tr>{table.getHeaderGroups().flatMap((group) => group.headers).map((header) => <th key={header.id} className="whitespace-nowrap border-b border-line px-3 py-3">{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr></thead><tbody>{table.getRowModel().rows.map((row) => <tr key={row.id} className="border-b border-line/70 hover:bg-white/[.02]">{row.getVisibleCells().map((cell) => <td key={cell.id} className="whitespace-nowrap px-3 py-3 text-slate-300">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table></div></Panel>
    </>}</Shell>;
}
