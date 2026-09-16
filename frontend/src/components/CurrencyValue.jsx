import React from "react";
const LOCALE_FOR = {
  USD: "en-US",
  CNY: "zh-CN",
  EUR: "de-DE",
  GBP: "en-GB",
  JPY: "ja-JP"
};
function localeForCurrency(currency) {
  const code = (currency ?? "USD").trim().toUpperCase();
  return LOCALE_FOR[code] ?? "en-US";
}
function currencyCodeFor(currency) {
  const code = (currency ?? "USD").trim().toUpperCase();
  return /^[A-Z]{3}$/.test(code) ? code : "USD";
}
function CurrencyValue({
  value,
  currency,
  locale,
  className = ""
}) {
  // Honest currency: undefined (prop omitted) keeps the legacy USD default
  // for callers that never knew the currency; explicit null/"" means
  // "currency not yet known" — render the plain number with an em-dash
  // instead of briefly mis-formatting a CNY/EUR price as USD.
  const currencyUnknown = currency === null || (typeof currency === "string" && currency.trim() === "");
  if (value === null || value === void 0 || !Number.isFinite(value)) {
    return /* @__PURE__ */ React.createElement("span", { className: `text-term-muted ${className}`, title: `currency=${currencyUnknown ? "—" : currencyCodeFor(currency ?? "USD")} value=unavailable` }, "unavailable");
  }
  if (currencyUnknown) {
    let plain;
    try {
      plain = new Intl.NumberFormat(locale ?? "en-US", { maximumFractionDigits: 2 }).format(value);
    } catch {
      plain = String(value);
    }
    return /* @__PURE__ */ React.createElement("span", { className, title: "currency unknown — price shown without conversion" }, `${plain} —`);
  }
  const code = currencyCodeFor(currency ?? "USD");
  const loc = locale ?? localeForCurrency(code);
  let text;
  try {
    text = new Intl.NumberFormat(loc, {
      style: "currency",
      currency: code
    }).format(value);
  } catch {
    text = `${code} ${value.toFixed(2)}`;
  }
  return /* @__PURE__ */ React.createElement("span", { className, title: `${code} \xB7 ${loc}` }, text);
}
export { currencyCodeFor, CurrencyValue as default, localeForCurrency };
