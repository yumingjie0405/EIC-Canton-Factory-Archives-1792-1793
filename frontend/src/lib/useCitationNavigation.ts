"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { evidenceAnchorId, isCitationInRange } from "@/lib/citations";

const HIGHLIGHT_DURATION_MS = 4000;

/**
 * Highlight + scroll coordination for one research result.
 *
 * Each result passes its own `scope`, so anchors never collide when several
 * answers (current RAG answer, current Agent run, opened history records) are
 * on screen at once. Navigation refuses out-of-range numbers outright instead
 * of pointing at a neighbouring card.
 */
export function useCitationNavigation(scope: string, sourceCount: number) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    clearTimer();
    setActiveIndex(null);
  }, [clearTimer]);

  useEffect(() => clearTimer, [clearTimer]);

  const activate = useCallback(
    (index: number): boolean => {
      if (!isCitationInRange(index, sourceCount)) {
        return false;
      }
      setActiveIndex(index);
      clearTimer();
      timerRef.current = setTimeout(() => setActiveIndex(null), HIGHLIGHT_DURATION_MS);

      if (typeof document !== "undefined") {
        const element = document.getElementById(evidenceAnchorId(scope, index));
        if (element) {
          element.scrollIntoView({ behavior: "smooth", block: "center" });
          // Move keyboard focus so screen readers announce the evidence itself.
          (element as HTMLElement).focus({ preventScroll: true });
        }
      }
      return true;
    },
    [clearTimer, scope, sourceCount]
  );

  return { activeIndex, activate, reset };
}
