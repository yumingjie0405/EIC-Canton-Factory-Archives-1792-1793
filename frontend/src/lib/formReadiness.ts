/**
 * Small pure rules shared by the "submit is never silently inert" behaviour.
 *
 * A disabled submit button is fine as long as the reason is visible. These
 * helpers decide *why* a submit was blocked so pages can surface a message
 * instead of doing nothing (the failure mode that made a click on an
 * un-hydrated form look like a hung request).
 */

export const EMPTY_GROUP_NAME_MESSAGE = "请输入文档组名称。";
export const EMPTY_QUESTION_MESSAGE = "请先输入研究问题。";
export const EMPTY_PLAN_QUESTION_MESSAGE = "请先输入研究问题，再生成任务规划。";
export const BUSY_MESSAGE = "请求处理中，请稍候。";

/** Controls may only be used once React has hydrated them and no request is running. */
export function isInteractive(hydrated: boolean, busy: boolean): boolean {
  return hydrated && !busy;
}

/**
 * Returns the message to show for a blocked submit, or ``null`` when the
 * submit should proceed.
 */
export function blockedSubmitMessage(params: {
  value: string;
  emptyMessage: string;
  busy?: boolean;
  hydrated?: boolean;
}): string | null {
  const { value, emptyMessage, busy = false, hydrated = true } = params;
  if (busy) return BUSY_MESSAGE;
  if (!hydrated) return null;
  if (!value.trim()) return emptyMessage;
  return null;
}
