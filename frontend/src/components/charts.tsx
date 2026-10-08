"use client";

import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export function TrendChart({ data, dataKey = "profit", color = "#75e0c0" }: { data: Array<Record<string, string | number>>; dataKey?: string; color?: string }) {
  return <div className="h-[230px] w-full"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data}><defs><linearGradient id={`fill-${dataKey}`} x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={color} stopOpacity={0.25} /><stop offset="95%" stopColor={color} stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke="#202b3b" strokeDasharray="4 4" vertical={false} /><XAxis dataKey="date" tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={26} /><YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} width={52} /><Tooltip contentStyle={{ background: "#0d1420", border: "1px solid #263244", borderRadius: 10, color: "#e5edf7" }} /><Area type="monotone" dataKey={dataKey} stroke={color} fill={`url(#fill-${dataKey})`} strokeWidth={2} /></AreaChart></ResponsiveContainer></div>;
}

export function CompareChart({ data, x, bars, valueFormatter }: { data: Array<Record<string, string | number>>; x: string; bars: string[]; valueFormatter?: (value: number) => string }) {
  const colors: Record<string, string> = {
    wins: "#75e0c0",
    losses: "#fb7185",
    ties: "#fbbf24",
    spend: "#75e0c0",
    revenue: "#7191ff",
    current: "#75e0c0",
    optimized: "#7191ff",
  };
  return <div className="h-[280px] w-full"><ResponsiveContainer width="100%" height="100%"><BarChart data={data}><CartesianGrid stroke="#202b3b" strokeDasharray="4 4" vertical={false} /><XAxis dataKey={x} tick={{ fill: "#94a3b8", fontSize: 10 }} tickLine={false} axisLine={false} /><YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} /><Tooltip cursor={{ fill: "#75e0c0", fillOpacity: 0.08, stroke: "#75e0c0", strokeOpacity: 0.16, strokeWidth: 1 }} contentStyle={{ background: "#0d1420", border: "1px solid #263244", borderRadius: 10 }} formatter={(value) => typeof value === "number" && valueFormatter ? valueFormatter(value) : value} /><Legend wrapperStyle={{ color: "#94a3b8", fontSize: 10 }} />{bars.map((bar, index) => <Bar key={bar} dataKey={bar} fill={colors[bar] ?? ["#75e0c0", "#7191ff", "#fbbf24", "#fb7185"][index % 4]} radius={[4, 4, 0, 0]} />)}</BarChart></ResponsiveContainer></div>;
}

export function DecompositionChart({ data }: { data: Array<{ factor: string; contribution: number }> }) {
  return <div className="h-[220px] w-full min-w-0"><ResponsiveContainer width="100%" height="100%"><BarChart data={data} layout="vertical" margin={{ top: 4, right: 20, bottom: 4, left: 0 }}><CartesianGrid stroke="#202b3b" strokeDasharray="4 4" horizontal={false} /><XAxis type="number" tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={(value) => `${Number(value).toFixed(1)}%`} /><YAxis dataKey="factor" type="category" width={48} tick={{ fill: "#94a3b8", fontSize: 10 }} tickLine={false} axisLine={false} /><ReferenceLine x={0} stroke="#64748b" /><Tooltip cursor={{ fill: "#75e0c0", fillOpacity: 0.08 }} contentStyle={{ background: "#0d1420", border: "1px solid #263244", borderRadius: 10 }} formatter={(value) => [`${Number(value).toFixed(2)}%`, "ROAS contribution"]} /><Bar dataKey="contribution" fill="#75e0c0" radius={[0, 4, 4, 0]} minPointSize={2} /></BarChart></ResponsiveContainer></div>;
}
