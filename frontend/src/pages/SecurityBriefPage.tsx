import { useParams } from 'react-router-dom';
import SecurityBrief from '../features/security/SecurityBrief';

/** Malformed `%` sequences throw in decodeURIComponent — never crash the page. */
function safeDecode(v: string): string {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}

export default function SecurityBriefPage() {
  const { symbol = 'AAPL' } = useParams();
  const decoded = safeDecode(symbol);
  return (
    <div>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">SECURITY BRIEF · {decoded}</h1>
      <SecurityBrief symbol={decoded} />
    </div>
  );
}
