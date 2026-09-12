import { Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import HomePage from './pages/HomePage';
import SearchPage from './pages/SearchPage';
import SecurityBriefPage from './pages/SecurityBriefPage';
import ForecastDetailsPage from './pages/ForecastDetailsPage';
import ProviderSettingsPage from './pages/ProviderSettingsPage';
import BacktestLabPage from './pages/BacktestLabPage';
import WatchlistPage from './pages/WatchlistPage';

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/security/:symbol" element={<SecurityBriefPage />} />
        <Route path="/forecast/:symbol" element={<ForecastDetailsPage />} />
        <Route path="/providers" element={<ProviderSettingsPage />} />
        <Route path="/backtest" element={<BacktestLabPage />} />
        <Route path="/watchlist" element={<WatchlistPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
