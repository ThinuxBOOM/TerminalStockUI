const LOCALE_FOR: Record<string, string> = {
  USD: 'en-US',
  CNY: 'zh-CN',
  EUR: 'de-DE',
  GBP: 'en-GB',
  JPY: 'ja-JP',
};

/** Locale hint per currency (overridable via prop). Defaults to en-US. */
export function localeForCurrency(currency?: string): string {
  const code = (currency ?? 'USD').trim().toUpperCase();
  return LOCALE_FOR[code] ?? 'en-US';
}

/** Normalize to a 3-letter ISO code; fall back to USD for display only. */
export function currencyCodeFor(currency?: string): string {
  const code = (currency ?? 'USD').trim().toUpperCase();
  return /^[A-Z]{3}$/.test(code) ? code : 'USD';
}

export default function CurrencyValue({
  value,
  currency = 'USD',
  locale,
  className = '',
}: {
  value: number | null | undefined;
  currency?: string;
  locale?: string;
  className?: string;
}) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return (
      <span className={`text-term-muted ${className}`} title={`currency=${currencyCodeFor(currency)} value=unavailable`}>
        unavailable
      </span>
    );
  }
  const code = currencyCodeFor(currency);
  const loc = locale ?? localeForCurrency(code);
  let text: string;
  try {
    text = new Intl.NumberFormat(loc, {
      style: 'currency',
      currency: code,
    }).format(value);
  } catch {
    text = `${code} ${value.toFixed(2)}`;
  }
  return (
    <span className={className} title={`${code} · ${loc}`}>
      {text}
    </span>
  );
}
