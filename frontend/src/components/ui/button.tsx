import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const variants = cva("inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition disabled:pointer-events-none disabled:opacity-50", {
  variants: {
    variant: {
      primary: "bg-accent text-[#092019] hover:bg-emerald-200",
      outline: "border border-line bg-surface text-slate-200 hover:border-accent/50",
      ghost: "text-slate-400 hover:bg-white/[.05] hover:text-white",
      danger: "border border-rose-900/70 bg-rose-950/30 text-rose-200 hover:bg-rose-950/60",
    },
    size: {
      default: "px-4 py-2.5",
      sm: "px-2.5 py-1.5 text-xs",
      icon: "h-9 w-9",
    },
  },
  defaultVariants: { variant: "primary", size: "default" },
});

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof variants>;

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(variants({ variant, size }), className)} {...props} />;
}
