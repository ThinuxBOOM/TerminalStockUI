import React from "react";

const VARIANTS = {
  primary: "bg-term-green text-black hover:opacity-90",
  secondary: "border border-term-border2 bg-term-panel2 text-term-text hover:border-term-green",
  ghost: "border border-term-border text-term-text hover:border-term-green",
  danger: "border border-term-red/60 text-term-red hover:bg-term-redDim",
};

const SIZES = {
  xs: "px-2 py-1 text-2xs",
  sm: "px-2.5 py-1.5 text-xs",
  md: "px-3 py-2 text-sm",
  lg: "px-4 py-2.5 text-sm",
};

function Button({
  variant = "primary",
  size = "md",
  className = "",
  type = "button",
  disabled = false,
  children,
  ...rest
}) {
  const v = VARIANTS[variant] ?? VARIANTS.primary;
  const s = SIZES[size] ?? SIZES.md;
  return (
    <button
      type={type}
      disabled={disabled}
      className={`rounded font-semibold transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40 ${v} ${s} ${className}`.trim()}
      {...rest}
    >
      {children}
    </button>
  );
}

export { Button, Button as default };
