"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, ArrowLeftRight, BadgeDollarSign, BarChart3, BrainCircuit, Boxes, CircleHelp, GitBranch, Gauge, LayoutDashboard, Lightbulb, ListChecks, Radio, ShieldAlert } from "lucide-react";
import { motion } from "framer-motion";
import { useConsoleStore } from "@/lib/store";

const navigation = [
  { href: "/", label: "Command Center", icon: LayoutDashboard },
  { href: "/data", label: "Unified Data Hub", icon: GitBranch },
  { href: "/performance", label: "Performance Explorer", icon: BarChart3 },
  { href: "/diagnosis", label: "Anomaly & Diagnosis", icon: ShieldAlert },
  { href: "/opportunities", label: "Opportunity Radar", icon: Lightbulb },
  { href: "/decisions", label: "Decision Center", icon: ListChecks },
  { href: "/optimizer", label: "Budget Optimizer", icon: Gauge },
  { href: "/learning", label: "Learning & Calibration", icon: BrainCircuit },
];

export function Shell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();
  const setScenarioGuideOpen = useConsoleStore((state) => state.setScenarioGuideOpen);
  return (
    <div className="min-h-screen bg-background text-slate-100">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[250px] border-r border-line bg-[#0c111b] px-4 py-5 lg:block">
        <Link href="/" className="mb-8 flex items-center gap-3 px-2">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent/15 text-accent"><Activity size={21} /></span>
          <span><strong className="block text-lg tracking-tight">AdPilot</strong><small className="text-xs text-slate-500">Growth operations</small></span>
        </Link>
        <div className="mb-3 px-3 text-[10px] font-bold uppercase tracking-[.18em] text-slate-500">Workspace</div>
        <nav className="space-y-1">
          {navigation.map(({ href, label, icon: Icon }) => {
            const active = pathname === href;
            return (
              <Link key={href} href={href} className={`relative flex items-center gap-3 rounded-lg px-3 py-2.5 text-[13px] transition ${active ? "text-white" : "text-slate-400 hover:bg-white/[.04] hover:text-slate-100"}`}>
                {active && <motion.span layoutId="nav-active" className="absolute inset-0 rounded-lg border border-accent/20 bg-accent/[.09]" />}
                <Icon className={`relative z-10 ${active ? "text-accent" : ""}`} size={16} />
                <span className="relative z-10">{label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="absolute inset-x-4 bottom-5 space-y-2">
          <button onClick={() => setScenarioGuideOpen(true)} className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-slate-400 hover:bg-white/[.04]"><CircleHelp size={16} />Scenario guide</button>
          <div className="rounded-xl border border-line bg-surface p-3">
            <div className="flex items-center gap-2 text-xs text-slate-300"><span className="h-2 w-2 rounded-full bg-accent" />Simulator mode</div>
            <p className="mb-0 mt-1 text-[11px] leading-relaxed text-slate-500">Actions are simulated. No ad account is changed.</p>
          </div>
        </div>
      </aside>
      <div className="lg:pl-[250px]">
        <header className="sticky top-0 z-20 flex h-[68px] items-center justify-between border-b border-line bg-[#090d16]/90 px-5 backdrop-blur-xl md:px-8">
          <div className="flex items-center gap-2 text-xs text-slate-500"><Boxes size={15} /> AdPilot Demo Brand <span className="text-slate-700">/</span> <span className="text-slate-300">All channels</span></div>
          <div className="flex items-center gap-4"><div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex"><Radio size={14} className="text-accent" /> Pipeline healthy</div><button onClick={() => setScenarioGuideOpen(true)} className="rounded-full border border-line p-2 text-slate-400 hover:text-white"><CircleHelp size={16} /></button><div className="grid h-8 w-8 place-items-center rounded-full bg-slate-700 text-xs font-semibold">DO</div></div>
        </header>
        <main className="mx-auto max-w-[1500px] px-5 py-7 md:px-8">{children}</main>
      </div>
      <ScenarioDrawer />
    </div>
  );
}

function ScenarioDrawer() {
  const open = useConsoleStore((state) => state.scenarioGuideOpen);
  const setOpen = useConsoleStore((state) => state.setScenarioGuideOpen);
  const scenarios = [
    ["meta_creative_fatigue", "Meta creative fatigue", "CTR decay and rising CPA"],
    ["stockout_risk", "Advertised SKU stockout risk", "Less than 3 days of cover"],
    ["price_promo_cvr_drop", "Price hike / promo removal", "CVR decline after price change"],
    ["shopping_brand_cannibalization", "Shopping / brand overlap", "Attributed revenue moves between campaigns"],
    ["tiktok_uncapped_headroom", "TikTok marginal ROAS", "Room to scale while marginal returns rise"],
    ["ga4_tracking_outage", "GA4 tracking outage", "Analytics events decline while orders hold"],
    ["attribution_overstatement", "Attribution overstatement", "Platform credit exceeds store orders"],
    ["high_margin_underpromoted", "High-margin under-promotion", "Healthy stock and margin, low budget"],
  ];
  if (!open) return null;
  return <div className="fixed inset-0 z-50 flex justify-end bg-black/60" onClick={() => setOpen(false)}><section className="h-full w-full max-w-md overflow-y-auto border-l border-line bg-[#0d1420] p-6" onClick={(event) => event.stopPropagation()}><div className="mb-6 flex items-center justify-between"><div><p className="text-xs uppercase tracking-widest text-accent">Scenario guide</p><h2 className="mt-1 text-xl font-semibold">Explore the seeded cases</h2></div><button className="text-slate-400" onClick={() => setOpen(false)}>Close</button></div><div className="space-y-3">{scenarios.map(([key, title, detail], index) => <Link key={key} href={`/diagnosis?scenario=${key}`} onClick={() => setOpen(false)} className="block rounded-xl border border-line bg-surface p-4 hover:border-accent/40"><div className="flex gap-3"><span className="text-xs font-bold text-accent">0{index + 1}</span><div><strong className="text-sm">{title}</strong><p className="mb-0 mt-1 text-xs text-slate-500">{detail}</p></div></div></Link>)}</div></section></div>;
}

export function Heading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return <div className="mb-7"><p className="mb-2 text-[10px] font-bold uppercase tracking-[.2em] text-accent">{eyebrow}</p><h1 className="text-2xl font-semibold tracking-tight text-white md:text-[30px]">{title}</h1><p className="mb-0 mt-2 max-w-3xl text-sm leading-relaxed text-slate-400">{description}</p></div>;
}

export function Panel({ title, subtitle, children, className = "" }: { title: string; subtitle?: string; children: React.ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-line bg-surface/80 p-5 ${className}`}><div className="mb-4"><h2 className="text-sm font-semibold text-slate-100">{title}</h2>{subtitle && <p className="mb-0 mt-1 text-xs text-slate-500">{subtitle}</p>}</div>{children}</section>;
}

export function LoadingError({ error }: { error: Error | null }) {
  if (!error) return <div className="rounded-xl border border-line bg-surface p-4 text-sm text-slate-400">Loading current data…</div>;
  return <div className="rounded-xl border border-rose-800/60 bg-rose-950/30 p-4 text-sm text-rose-200">Could not load service data: {error.message}</div>;
}
