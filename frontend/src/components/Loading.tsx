export default function Loading({ label = 'loading…' }: { label?: string }) {
  return (
    <div className="term-panel p-6 text-sm text-term-muted" role="status">
      <span className="animate-pulse text-term-green">▊</span> {label}
    </div>
  );
}
