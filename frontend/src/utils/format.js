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
export { changeArrow, changeColor, formatDateTime, formatPct1 };
