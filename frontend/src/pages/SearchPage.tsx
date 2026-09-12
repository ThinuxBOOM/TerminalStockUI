import { useSearchParams } from 'react-router-dom';
import SearchBox from '../features/search/SearchBox';

/** M7: unified exchange-aware search (XNYS/XNAS/XSHG/XPAR/XAMS/XBRU). */
const KNOWN_MARKETS = new Set(['XNYS', 'XNAS', 'XSHG', 'XPAR', 'XAMS', 'XBRU']);

function normalizeMarket(v: string | null): string {
  const mic = (v ?? '').trim().toUpperCase();
  if (!mic || mic === 'ALL') return '';
  return KNOWN_MARKETS.has(mic) ? mic : '';
}

export default function SearchPage() {
  const [params] = useSearchParams();
  const q = params.get('q') ?? '';
  const market = normalizeMarket(params.get('market'));
  return (
    <div>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">SEARCH · UNIFIED EXCHANGE-AWARE</h1>
      <SearchBox key={`${q}|${market}`} initial={q} initialMarket={market} />
    </div>
  );
}
