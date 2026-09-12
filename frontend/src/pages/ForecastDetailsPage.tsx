import { useParams } from 'react-router-dom';
import ForecastDetails from '../features/forecast/ForecastDetails';

export default function ForecastDetailsPage() {
  const { symbol = 'AAPL' } = useParams();
  const decoded = decodeURIComponent(symbol);
  return (
    <div>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">FORECAST DETAILS · {decoded}</h1>
      <ForecastDetails symbol={decoded} />
    </div>
  );
}
