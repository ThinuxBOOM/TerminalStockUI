export function StaleBanner({ detail }: { detail?: string }) {
  return (
    <div className="mb-3 rounded border border-term-amber bg-term-panel p-3 text-xs text-term-amber" role="alert">
      ⚠ Partial failure — showing cached/stale data{detail ? `: ${detail}` : '.'} Provider
      outage does not block the page (Milestone 1 acceptance).
    </div>
  );
}

export default function ErrorState({
  title = 'Something failed',
  detail,
  onRetry,
}: {
  title?: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="term-panel p-6" role="alert">
      <p className="text-sm font-bold text-term-red">✕ {title}</p>
      {detail && <p className="mt-1 text-xs text-term-muted">{detail}</p>}
      {onRetry && (
        <button className="term-btn-ghost mt-3" type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
