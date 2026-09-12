type Props = {
  /** Accessible label announced to screen readers. */
  label?: string;
  /** Number of shimmer lines to render. */
  lines?: number;
  className?: string;
};

/**
 * Loading shimmer placeholder (M8). Use while a query is in flight so every
 * page has a loading state distinct from its empty/error/stale states.
 * Pure CSS — no new deps.
 */
export default function Skeleton({ label = 'loading…', lines = 3, className = '' }: Props) {
  const n = Math.min(12, Math.max(1, lines));
  return (
    <div className={`term-panel p-4 ${className}`} role="status" aria-label={label}>
      <span className="sr-only">{label}</span>
      <div className="space-y-2" aria-hidden="true">
        {Array.from({ length: n }).map((_, i) => (
          <div
            key={i}
            className="skeleton-shimmer h-3 rounded"
            style={{ width: `${92 - (i % 3) * 18}%` }}
          />
        ))}
      </div>
    </div>
  );
}

export function SkeletonLine({ className = '' }: { className?: string }) {
  return <div className={`skeleton-shimmer h-3 rounded ${className}`} aria-hidden="true" />;
}
