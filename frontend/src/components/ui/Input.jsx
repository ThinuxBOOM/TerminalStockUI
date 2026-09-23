import React from "react";

// Labeled input bound to the Input surface token. Keeps :focus-visible outline.
function Input({
  id,
  label,
  hint,
  error,
  className = "",
  inputClassName = "",
  ...rest
}) {
  const describedBy = hint || error ? `${id}-desc` : undefined;
  return (
    <div className={className}>
      {label ? (
        <label htmlFor={id} className="mb-1 block text-xs text-term-muted">
          {label}
        </label>
      ) : null}
      <input
        id={id}
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        className={`term-input w-full ${inputClassName}`.trim()}
        {...rest}
      />
      {error ? (
        <p id={`${id}-desc`} role="alert" className="mt-1 text-xs text-term-red">{error}</p>
      ) : hint ? (
        <p id={`${id}-desc`} className="mt-1 text-2xs text-term-muted">{hint}</p>
      ) : null}
    </div>
  );
}

export { Input, Input as default };
