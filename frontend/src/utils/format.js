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
export { changeArrow, changeColor, formatDateTime, formatMetric, formatNumber, formatPct1, formatRange, formatValue };
