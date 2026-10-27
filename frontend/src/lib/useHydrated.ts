"use client";

import { useEffect, useState } from "react";

/**
 * Whether React has hydrated this component in the browser.
 *
 * Server-rendered markup shows up before React attaches its handlers, so a form
 * that looks ready can still swallow clicks (the handler never runs and no
 * request is sent). Gating the interactive controls on this flag turns that
 * silent dead state into an explicit "preparing" state.
 */
export function useHydrated(): boolean {
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    setHydrated(true);
  }, []);
  return hydrated;
}
