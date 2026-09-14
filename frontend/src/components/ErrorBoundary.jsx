import React from "react";

// Route-level crash containment: a chart/table exception must degrade to a
// retryable panel instead of unmounting the whole terminal (no boundary
// existed before — any render throw blanked the app).
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    try {
      if (typeof console !== "undefined" && console.error) {
        console.error("OneMarket UI error:", error, info);
      }
    } catch {
      // logging must never throw
    }
  }
  render() {
    if (this.state.error) {
      const reset = () => {
        try {
          if (typeof this.props.onReset === "function") this.props.onReset();
        } catch {
          // ignore
        }
        this.setState({ error: null });
      };
      return /* @__PURE__ */ React.createElement(
        "div",
        { role: "alert", className: "rounded border border-term-border bg-term-panel p-4 text-sm text-term-text" },
        /* @__PURE__ */ React.createElement("p", { className: "font-bold text-term-green" }, "Something went wrong in this view."),
        /* @__PURE__ */ React.createElement(
          "p",
          { className: "mt-1 text-term-muted" },
          "The rest of the terminal is unaffected. Retry, or go home and come back."
        ),
        /* @__PURE__ */ React.createElement(
          "button",
          { type: "button", className: "term-btn-ghost mt-3", onClick: reset },
          "Retry this view"
        )
      );
    }
    return this.props.children;
  }
}
export { ErrorBoundary as default };
