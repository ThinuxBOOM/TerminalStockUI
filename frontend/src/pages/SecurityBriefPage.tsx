import { useParams } from 'react-router-dom';
import SecurityBrief from '../features/security/SecurityBrief';

export default function SecurityBriefPage() {
  const { symbol = 'AAPL' } = useParams();
  const decoded = decodeURIComponent(symbol);
  return (
    <div>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">SECURITY BRIEF · {decoded}</h1>
      <SecurityBrief symbol={decoded} />
    </div>
  );
}
