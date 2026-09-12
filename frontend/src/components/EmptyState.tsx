type Props = {
  title?: string;
  detail?: string;
  actionLabel?: string;
  onAction?: () => void;
  className?: string;
};

/**
 * Empty-data placeholder (M8). Use when a query succeeds with zero rows so
 * every page has an explicit empty state distinct from loading/error.
 */
export default function EmptyState({
  title = 'Nothing here yet',
  detail,
  actionLabel,
  onAction,
  className = '',
}: Props) {
  return (
    <div className={`term-panel p-6 text-sm text-term-muted ${className}`} role="status">
      <p className="font-bold text-term-text">{title}</p>
      {detail && <p className="mt-1 text-xs">{detail}</p>}
      {actionLabel && onAction && (
        <button className="term-btn-ghost mt-3 text-xs" type="button" onClick={onAction}>
          {actionLabel}
        </button>
      )}
    </div>
  );
}
