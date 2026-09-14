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
  currency = "USD",
  locale,
  className = ""
}) {
  if (value === null || value === void 0 || !Number.isFinite(value)) {
    return /* @__PURE__ */ React.createElement("span", { className: `text-term-muted ${className}`, title: `currency=${currencyCodeFor(currency)} value=unavailable` }, "unavailable");
  }
  const code = currencyCodeFor(currency);
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
