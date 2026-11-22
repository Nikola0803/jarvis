"use client";

/**
 * The command bar under the orb: type to Z.E.R.O without opening anything.
 * Enter sends, Escape closes and hands focus back to the mic (VAD keeps running).
 * Shared state is lifted into MiraWorld so the old ⌨ button just focuses this.
 */

import { forwardRef } from "react";

const CommandBar = forwardRef<
  HTMLInputElement,
  {
    value: string;
    onChange: (v: string) => void;
    onSubmit: () => void;
    disabled?: boolean;
  }
>(function CommandBar({ value, onChange, onSubmit, disabled }, ref) {
  return (
    <form
      className="hud-command"
      onSubmit={(e) => { e.preventDefault(); onSubmit(); }}
      autoComplete="off"
    >
      <span className="hud-command-caret" aria-hidden="true">›</span>
      <input
        ref={ref}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={disabled ? "listening…" : "ask, or /mail read 3, /mail reply…"}
        disabled={disabled}
        aria-label="Message Z.E.R.O"
        onKeyDown={(e) => {
          if (e.key === "Escape") { onChange(""); (e.target as HTMLInputElement).blur(); }
        }}
      />
      <button type="submit" disabled={disabled || !value.trim()} aria-label="Send">↵</button>
    </form>
  );
});

export default CommandBar;
