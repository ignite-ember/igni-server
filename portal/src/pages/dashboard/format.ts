/** `1_500_000` → `"1.5M"`, `2_400` → `"2.4K"`, below that the number itself. */
export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return n.toLocaleString()
}

/** A share of a whole as a whole-number percentage: `0.437` → `"44%"`. */
export function formatPercent(share: number): string {
  return `${Math.round(share * 100)}%`
}

/** Mebibytes as people read them: `512` → `"512 MiB"`, `4096` → `"4 GiB"`. */
export function formatMemory(mib: number): string {
  return mib >= 1024 && mib % 1024 === 0 ? `${mib / 1024} GiB` : `${mib.toLocaleString()} MiB`
}

/** Whole days from now until `iso` (negative once it has passed). */
export function daysUntil(iso: string, now: Date = new Date()): number {
  return Math.floor((new Date(iso).getTime() - now.getTime()) / 86_400_000)
}
