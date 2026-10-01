// Central display formatting: probabilities always one decimal place,
// timestamps locale-aware with an explicit style (no bare toLocaleString()).
function formatPct1(p) {
  if (typeof p !== "number" || !Number.isFinite(p)) return "\u2014";
  return `${(p * 100).toFixed(1)}%`;
}
function changeColor(v) {
  if (typeof v !== "number" || !Number.isFinite(v) || v === 0) return "text-term-muted";
  return v > 0 ? "text-term-green" : "text-term-red";
}
function changeArrow(v) {
  if (typeof v !== "number" || !Number.isFinite(v) || v === 0) return "";
  return v > 0 ? "\u25B2" : "\u25BC";
}
function formatDateTime(iso) {
  if (iso === null || iso === void 0 || iso === "") return "\u2014";
  const d = iso instanceof Date ? iso : new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  try {
    return d.toLocaleString(void 0, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return d.toLocaleString();
  }
}
function formatNumber(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  if (abs !== 0 && (abs >= 1e6 || abs < 1e-3)) return n.toExponential(2);
  return n.toLocaleString(void 0, { maximumFractionDigits: abs >= 100 ? 2 : 4 });
}
function formatValue(v) {
  if (v === null || v === void 0) return "—";
  if (typeof v === "number") return formatNumber(v);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v !== "object") return String(v);
  if (Array.isArray(v)) return v.map(formatValue).join(", ");
  // Backend series/frame summaries: show the latest value(s).
  if (v.kind === "series") return formatValue(v.latest);
  if (v.kind === "frame") return formatValue(v.latest ?? {});
  return Object.entries(v).map(([k, x]) => `${k} ${formatValue(x)}`).join(" · ");
}
// Analytics metrics arrive as {value, formula, source_fields, quality_flag,
// reason}. Returns display text plus a tooltip with the formula/inputs, so
// raw JSON never reaches the page.
function formatMetric(m) {
  if (m && typeof m === "object" && !Array.isArray(m) && "quality_flag" in m) {
    const parts = [];
    if (m.formula) parts.push(m.formula);
    if (Array.isArray(m.source_fields) && m.source_fields.length) parts.push(`inputs: ${m.source_fields.join(", ")}`);
    const title = parts.join(" | ");
    if (m.quality_flag === "unavailable") {
      return { text: m.reason ? `unavailable (${m.reason})` : "unavailable", title, unavailable: true };
    }
    const text = formatValue(m.value);
    return { text: m.quality_flag === "degraded" ? `${text} (degraded)` : text, title, unavailable: false };
  }
  return { text: formatValue(m), title: "", unavailable: false };
}
// Forecast return band {low, high} (fractions) as "-8% to +9%"; em dash when missing.
function formatRange(iv) {
  if (!iv || typeof iv.low !== "number" || typeof iv.high !== "number" || !Number.isFinite(iv.low) || !Number.isFinite(iv.high)) return "—";
  const pct = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(0)}%`;
  return `${pct(iv.low)} to ${pct(iv.high)}`;
}
function isNum(v) {
  return typeof v === "number" && Number.isFinite(v);
}
// 0.0123 -> "+1.2%" (signed) or "1.2%" (unsigned); em dash when missing.
function fmtPct(v, digits = 1, { signed = false } = {}) {
  if (!isNum(v)) return "—";
  const x = v * 100;
  const sign = signed ? (x > 0 ? "+" : x < 0 ? "−" : "") : x < 0 ? "−" : "";
  return `${sign}${Math.abs(x).toFixed(digits)}%`;
}
function fmtMoney(v, currency = "USD", digits) {
  if (!isNum(v)) return "—";
  const d = digits ?? (Math.abs(v) >= 1000 ? 0 : 2);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: currency || "USD", currencyDisplay: "narrowSymbol", minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
  } catch {
    return v.toFixed(d);
  }
}
// 15012350139 -> "15.0B"
function fmtCompact(v, digits = 1) {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  const [div, suf] = a >= 1e12 ? [1e12, "T"] : a >= 1e9 ? [1e9, "B"] : a >= 1e6 ? [1e6, "M"] : a >= 1e3 ? [1e3, "K"] : [1, ""];
  return `${(v / div).toFixed(suf ? digits : 0)}${suf}`;
}
function fmtNum(v, digits = 2) {
  return isNum(v) ? v.toFixed(digits) : "—";
}
// Percentile rank (0..1) -> "Top 8%" / "Bottom 12%" / "Middle".
function fmtRank(rank) {
  if (!isNum(rank)) return "—";
  if (rank >= 0.5) return `Top ${Math.max(1, Math.round((1 - rank) * 100))}%`;
  return `Bottom ${Math.max(1, Math.round(rank * 100))}%`;
}
export { changeArrow, changeColor, fmtCompact, fmtMoney, fmtNum, fmtPct, fmtRank, formatDateTime, formatMetric, formatNumber, formatPct1, formatRange, formatValue, isNum };
