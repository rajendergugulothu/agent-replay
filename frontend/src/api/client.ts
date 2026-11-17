import type {
  CompareOut,
  DiagnosisOut,
  HealthOut,
  LocalizerMethod,
  ResetOut,
  ResultsOut,
  ResumeIn,
  RunOut,
  RunStarted,
  ScenarioOut,
  ToolOut,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, body?: unknown, method?: 'POST'): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: method ?? (body === undefined ? 'GET' : 'POST'),
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, 'network_error', 'Could not reach the server.');
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (payload as { error?: { code?: unknown; message?: unknown } } | null)?.error;
    throw new ApiError(
      response.status,
      typeof error?.code === 'string' ? error.code : 'http_error',
      typeof error?.message === 'string' ? error.message : `Request failed (${response.status}).`,
    );
  }
  return payload as T;
}

export function getHealth(): Promise<HealthOut> {
  return request('/health');
}

export function getScenarios(): Promise<ScenarioOut[]> {
  return request('/scenarios');
}

export function getTools(): Promise<ToolOut[]> {
  return request('/tools');
}

export function startRun(scenarioId: string): Promise<RunStarted> {
  return request('/runs', { scenario_id: scenarioId });
}

export function getRun(runId: string): Promise<RunOut> {
  return request(`/runs/${runId}`);
}

export function runStreamUrl(runId: string): string {
  return `/api/runs/${runId}/stream`;
}

export function localize(runId: string, method: LocalizerMethod): Promise<DiagnosisOut> {
  return request(`/runs/${runId}/localize`, { method });
}

export function resumeRun(runId: string, body: ResumeIn): Promise<RunStarted> {
  return request(`/runs/${runId}/resume`, body);
}

export function restartRun(runId: string): Promise<RunStarted> {
  return request(`/runs/${runId}/restart`, undefined, 'POST');
}

export function compareRuns(runId: string, otherId: string): Promise<CompareOut> {
  return request(`/runs/${runId}/compare/${otherId}`);
}

export function getResults(): Promise<ResultsOut> {
  return request('/results');
}

export function resetDemo(): Promise<ResetOut> {
  return request('/reset', undefined, 'POST');
}
