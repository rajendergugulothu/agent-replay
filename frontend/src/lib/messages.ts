import { ApiError } from '../api/client';
import type { LocalizerMethod, RunOut } from '../api/types';

const ERROR_MESSAGES: Record<string, string> = {
  busy: 'Two runs are already in progress. Wait a few seconds and try again.',
  rate_limited: 'Too many requests in the last minute. Wait a minute and try again.',
  daily_limit: "Today's budget for live runs is used up. Recorded results are still available.",
  not_in_demo: 'This scenario is not part of the demo set.',
  not_configured: 'The server has no model access configured, so new runs cannot start.',
  offline_unavailable: 'This step was not recorded for offline use.',
  run_not_finished: 'The run is still in progress. Wait for it to finish.',
  run_not_found: 'This run no longer exists. Reset and start again.',
  network_error: 'Could not reach the server. Check that it is running.',
};

export function friendlyError(error: unknown): string {
  if (error instanceof ApiError) return ERROR_MESSAGES[error.code] ?? error.message;
  return 'Something went wrong. Try again.';
}

const STOP_REASON_MESSAGES: Record<string, string> = {
  api_error: 'The model service returned an error, so the run stopped early.',
  offline_unavailable: 'The server is offline and this run was not recorded for offline use.',
  server_busy: 'The server was busy with other runs and could not start this one. Try again.',
};

/** A plain sentence for a run that ended with status "error", otherwise null. */
export function runErrorMessage(run: RunOut): string | null {
  if (run.status !== 'error') return null;
  const known = run.stop_reason ? STOP_REASON_MESSAGES[run.stop_reason] : undefined;
  return known ?? 'The run stopped because of an unexpected error.';
}

export const LOCALIZERS: { method: LocalizerMethod; name: string; explanation: string }[] = [
  {
    method: 'rule',
    name: 'Rule-based',
    explanation: 'Fixed checks on each step. Instant and free, but only finds what the rules cover.',
  },
  {
    method: 'llm',
    name: 'LLM judge',
    explanation: 'A second model reads the whole trace and names the step it blames.',
  },
  {
    method: 'hybrid',
    name: 'Hybrid',
    explanation: 'Rules first. The model is asked only when the rules find nothing.',
  },
];

export function localizerName(method: LocalizerMethod): string {
  return LOCALIZERS.find((l) => l.method === method)?.name ?? method;
}
