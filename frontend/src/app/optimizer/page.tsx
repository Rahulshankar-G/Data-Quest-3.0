"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { CompareChart } from "@/components/charts";
import { Heading, LoadingError, Panel, Shell } from "@/components/shell";

type Campaign = {
  campaign_id: string;
  campaign: string;
  daily_budget: number;
  spend: number;
  profit: number;
  roas: number;
  inventory_cover_days: number | null;
};

type Optimized = {
  converged: boolean;
  status: string;
  budget_requested: number;
  budget_optimized: number;
  budget_adjusted: boolean;
  target_roas_met: boolean;
  budget_period: string;
  data_window: { start: string | null; end: string | null; days: number };
  predicted_revenue: number;
  predicted_profit: number;
  predicted_roas: number;
  allocations: Array<{
    name: string;
    current_spend: number;
    optimized_spend: number;
    change_pct: number;
    predicted_profit: number;
    inventory_cover_days: number;
    inventory_guardrail_applied: boolean;
  }>;
  response_curves: Record<string, {
    scale: number;
    half_saturation: number;
    r_squared: number;
    fit_method: string;
  }>;
};

export default function OptimizerPage() {
  const perf = useQuery({
    queryKey: ["performance", "all"],
    queryFn: () => api<{ rows: Campaign[] }>("v1/performance"),
  });
  const [budget, setBudget] = useState(0);
  const [shift, setShift] = useState(20);
  const [roas, setRoas] = useState(1);
  const rows = perf.data?.rows ?? [];
  const dailyBudget = [
    ...new Map(
      rows
        .filter((row) => row.campaign_id)
        .map((row) => [row.campaign_id, row.daily_budget]),
    ).values(),
  ].reduce((sum, value) => sum + value, 0);

  useEffect(() => {
    if (!budget && dailyBudget > 0) setBudget(Math.round(dailyBudget));
  }, [budget, dailyBudget]);

  const optimizer = useMutation({
    mutationFn: () => api<Optimized>("v1/optimizer", {
      method: "POST",
      body: JSON.stringify({
        total_budget: budget,
        max_shift_pct: shift,
        target_roas: roas,
      }),
    }),
  });

  return (
    <Shell>
      <Heading
        eyebrow="Budget optimizer"
        title="What-if allocation lab"
        description="Use observed daily revenue response to compare a daily budget allocation after product costs and inventory guardrails."
      />
      {perf.isLoading || perf.error ? <LoadingError error={perf.error} /> : (
        <div className="grid gap-4 xl:grid-cols-[330px_1fr]">
          <Panel
            title="Optimization objective"
            subtitle="Set a portfolio-wide daily budget and solve a constrained allocation"
          >
            <label className="mb-2 block text-xs text-slate-400">
              Daily portfolio budget · ${budget.toLocaleString()}
            </label>
            <input
              type="range"
              min={Math.max(100, Math.round(dailyBudget * 0.65))}
              max={Math.max(200, Math.round(dailyBudget * 1.35))}
              step={100}
              value={budget}
              onChange={(event) => setBudget(Number(event.target.value))}
              className="w-full accent-[#75e0c0]"
            />
            <label className="mb-2 mt-5 block text-xs text-slate-400">
              Maximum shift · {shift}%
            </label>
            <input
              type="range"
              min={0}
              max={50}
              step={1}
              value={shift}
              onChange={(event) => setShift(Number(event.target.value))}
              className="w-full accent-[#75e0c0]"
            />
            <label className="mb-2 mt-5 block text-xs text-slate-400">
              Minimum target ROAS · {roas.toFixed(2)}×
            </label>
            <input
              type="range"
              min={0}
              max={8}
              step={0.1}
              value={roas}
              onChange={(event) => setRoas(Number(event.target.value))}
              className="w-full accent-[#75e0c0]"
            />
            <button
              onClick={() => optimizer.mutate()}
              disabled={optimizer.isPending || !budget}
              className="mt-6 w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-[#092019] disabled:opacity-50"
            >
              {optimizer.isPending ? "Solving allocation…" : "Optimize budget mix"}
            </button>
            {optimizer.error && (
              <p className="mt-3 text-xs text-rose-300">{optimizer.error.message}</p>
            )}
            <p className="mt-4 text-[10px] leading-relaxed text-slate-500">
              Curves use up to 30 days of observed campaign revenue and spend.
              Predicted contribution profit subtracts estimated product cost and ad spend.
              Budgets are daily; low inventory caps daily spend. Optimization does not execute changes.
            </p>
          </Panel>

          <div className="space-y-4">
            <Panel
              title="Strategy comparison"
              subtitle={optimizer.data
                ? `${optimizer.data.status} · ${optimizer.data.converged ? "converged" : "feasible fallback; review constraints"}`
                : "Run the solver to compare current and optimized daily budgets"}
            >
              {optimizer.data && (
                <>
                  {optimizer.data.data_window.start && optimizer.data.data_window.end && (
                    <p className="mb-3 text-[11px] text-slate-500">
                      Historical window: {optimizer.data.data_window.start} to{" "}
                      {optimizer.data.data_window.end} ({optimizer.data.data_window.days} days)
                    </p>
                  )}
                  {(optimizer.data.budget_adjusted || !optimizer.data.target_roas_met) && (
                    <p className="mb-3 rounded-lg border border-amber-500/30 bg-amber-400/[.06] p-3 text-xs text-amber-100">
                      {optimizer.data.budget_adjusted && (
                        <>The requested daily budget was adjusted to ${optimizer.data.budget_optimized.toFixed(2)} to respect campaign shift and inventory limits. </>
                      )}
                      {!optimizer.data.target_roas_met && (
                        <>The target ROAS is infeasible within the current budget and guardrails; the displayed allocation does not meet it.</>
                      )}
                    </p>
                  )}
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {[
                      ["Daily budget", `$${optimizer.data.budget_optimized.toLocaleString()}`],
                      ["Predicted revenue", `$${optimizer.data.predicted_revenue.toLocaleString()}`],
                      ["Contribution profit", `$${optimizer.data.predicted_profit.toLocaleString()}`],
                      ["Predicted ROAS", `${optimizer.data.predicted_roas.toFixed(2)}×`],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-lg border border-line bg-[#0d1420] p-3">
                        <span className="text-[10px] text-slate-500">{label}</span>
                        <strong className="mt-1 block text-base">{value}</strong>
                      </div>
                    ))}
                  </div>
                  <div className="mt-4">
                    <CompareChart
                      data={optimizer.data.allocations.map((row) => ({
                        campaign: row.name.slice(0, 18),
                        current: row.current_spend,
                        optimized: row.optimized_spend,
                      }))}
                      x="campaign"
                      bars={["current", "optimized"]}
                    />
                  </div>
                </>
              )}
            </Panel>

            {optimizer.data && (
              <Panel
                title="Campaign allocation & stock burn-down"
                subtitle="Daily amounts; inventory guardrails are shown explicitly"
              >
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr>
                        {["Campaign", "Current daily", "Optimized daily", "Shift", "Cover days", "Guardrail"].map((label) => (
                          <th key={label} className="border-b border-line px-3 py-2 text-[10px] uppercase text-slate-500">
                            {label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {optimizer.data.allocations.map((row) => (
                        <tr key={row.name} className="border-b border-line/60">
                          <td className="px-3 py-2">{row.name}</td>
                          <td className="px-3 py-2">${row.current_spend.toFixed(2)}</td>
                          <td className="px-3 py-2">${row.optimized_spend.toFixed(2)}</td>
                          <td className="px-3 py-2">
                            {row.change_pct > 0 ? "+" : ""}{row.change_pct.toFixed(2)}%
                          </td>
                          <td className="px-3 py-2">{row.inventory_cover_days.toFixed(2)}</td>
                          <td className="px-3 py-2">
                            {row.inventory_guardrail_applied
                              ? <span className="text-amber-200">Throttled</span>
                              : "Clear"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            )}
          </div>
        </div>
      )}
    </Shell>
  );
}
