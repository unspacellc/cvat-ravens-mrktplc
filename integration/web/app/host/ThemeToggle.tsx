import { useEffect, useState } from "react";

type Mode = "light" | "dark" | "system";

/** Runs before first paint (inlined in <head>) so the page never flashes the wrong theme. */
export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem('theme')||'system';var d=t==='dark'||(t==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d)}catch(e){}})();`;

function read(): Mode {
  try {
    const v = localStorage.getItem("theme");
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(mode: Mode) {
  const dark = mode === "dark" || (mode === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function ThemeToggle() {
  const [mode, setMode] = useState<Mode>("system");
  useEffect(() => setMode(read()), []);
  useEffect(() => {
    if (mode !== "system") return;
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const on = () => applyTheme("system");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [mode]);
  const choose = (m: Mode) => {
    setMode(m);
    try {
      localStorage.setItem("theme", m);
    } catch {
      /* storage blocked: the choice still applies for this page view */
    }
    applyTheme(m);
  };
  return (
    <div role="group" aria-label="Theme" className="flex overflow-hidden rounded-md border border-border text-xs">
      {(["light", "dark", "system"] as const).map((m) => (
        <button key={m} type="button" aria-pressed={mode === m} onClick={() => choose(m)} className={`px-2 py-1 capitalize ${mode === m ? "bg-primary text-primary-foreground" : ""}`}>
          {m}
        </button>
      ))}
    </div>
  );
}
