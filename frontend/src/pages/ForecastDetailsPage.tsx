import { useParams } from 'react-router-dom';
import ForecastDetails from '../features/forecast/ForecastDetails';

/** Malformed `%` sequences throw in decodeURIComponent — never crash the page. */
function safeDecode(v: string): string {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}

export default function ForecastDetailsPage() {
  const { symbol = 'AAPL' } = useParams();
  const decoded = safeDecode(symbol);
  return (
    <div>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">FORECAST DETAILS · {decoded}</h1>
      <ForecastDetails symbol={decoded} />
    </div>
  );
}
