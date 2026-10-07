"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, BarChart, Bar } from "recharts";

export function TrendChart({ data, dataKey = "profit", color = "#75e0c0" }: { data: Array<Record<string, string | number>>; dataKey?: string; color?: string }) {
  return <div className="h-[230px] w-full"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data}><defs><linearGradient id={`fill-${dataKey}`} x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={color} stopOpacity={0.25} /><stop offset="95%" stopColor={color} stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke="#202b3b" strokeDasharray="4 4" vertical={false} /><XAxis dataKey="date" tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={26} /><YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} width={52} /><Tooltip contentStyle={{ background: "#0d1420", border: "1px solid #263244", borderRadius: 10, color: "#e5edf7" }} /><Area type="monotone" dataKey={dataKey} stroke={color} fill={`url(#fill-${dataKey})`} strokeWidth={2} /></AreaChart></ResponsiveContainer></div>;
}

export function CompareChart({ data, x, bars, valueFormatter }: { data: Array<Record<string, string | number>>; x: string; bars: string[]; valueFormatter?: (value: number) => string }) {
  return <div className="h-[280px] w-full"><ResponsiveContainer width="100%" height="100%"><BarChart data={data}><CartesianGrid stroke="#202b3b" strokeDasharray="4 4" vertical={false} /><XAxis dataKey={x} tick={{ fill: "#94a3b8", fontSize: 10 }} tickLine={false} axisLine={false} /><YAxis tick={{ fill: "#64748b", fontSize: 10 }} tickLine={false} axisLine={false} /><Tooltip cursor={{ fill: "#75e0c0", fillOpacity: 0.08, stroke: "#75e0c0", strokeOpacity: 0.16, strokeWidth: 1 }} contentStyle={{ background: "#0d1420", border: "1px solid #263244", borderRadius: 10 }} formatter={(value) => typeof value === "number" && valueFormatter ? valueFormatter(value) : value} /><Bar dataKey={bars[0]} fill="#75e0c0" radius={[4, 4, 0, 0]} /><Bar dataKey={bars[1]} fill="#7191ff" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div>;
}
