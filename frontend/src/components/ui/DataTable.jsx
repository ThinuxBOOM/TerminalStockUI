import React from "react";

// Minimal accessible table: columns [{key, header, align, render}],
// rows are plain objects. Numbers render with tabular-nums.
function DataTable({ columns = [], rows = [], caption, emptyText = "No rows.", className = "", testId }) {
  if (!rows || rows.length === 0) {
    return (
      <div className={`term-panel-nested p-4 text-center text-xs text-term-muted ${className}`.trim()} role="status">
        {emptyText}
      </div>
    );
  }
  return (
    <div className={`overflow-x-auto ${className}`.trim()} data-testid={testId}>
      <table className="w-full text-sm">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="text-left">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={`term-label px-2 py-1.5 font-sans ${c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left"}`.trim()}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.key ?? i} className="border-t border-term-border">
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={`px-2 py-1.5 ${c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left"} ${c.numeric ? "term-num" : ""}`.trim()}
                >
                  {typeof c.render === "function" ? c.render(r[c.key], r, i) : String(r[c.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export { DataTable, DataTable as default };
