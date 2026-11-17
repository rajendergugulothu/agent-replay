// Mirrors backend/agent_replay/api/models.py. Keep the two in step.

export type JsonObject = Record<string, unknown>;

export interface ErrorBody {
  code: string;
  message: string;
}

export interface HealthOut {
  status: 'ok';
  mode: 'live' | 'offline';
  demo_mode: boolean;
  version: string;
  results_available: boolean;
}

export interface ScenarioOut {
  id: string;
  title: string;
  description: string;
  failure_type: string;
  student_id: string;
  user_request: string;
  label_status: string;
}

export interface ToolOut {
  name: string;
  description: string;
  parameters: JsonObject;
}

export interface RunStarted {
  run_id: string;
}

export type StepOrigin = 'model' | 'forced' | 'reused';

export interface StepOut {
  step_number: number;
  tool_name: string | null;
  tool_args: JsonObject | null;
  tool_output: JsonObject | null;
  text: string | null;
  summary: string;
  tokens: number;
  seconds: number;
  depends_on: number[];
  origin: StepOrigin;
  // Null until the run has been diagnosed, so the UI cannot give the answer away.
  injection: JsonObject[] | null;
}

export interface ReasonOut {
  code: string;
  message: string;
}

export interface ValidationOut {
  passed: boolean;
  reasons: ReasonOut[];
}

export interface ScheduleEntry {
  section_id: string;
  course_code: string;
  title: string;
  days: string[];
  start: string;
  end: string;
  credits: number;
}

export type RunKind = 'original' | 'full_restart' | 'continuation';
export type RunStatus = 'running' | 'passed' | 'failed' | 'error';

export interface RunOut {
  id: string;
  scenario_id: string | null;
  kind: RunKind;
  status: RunStatus;
  stop_reason: string | null;
  parent_run_id: string | null;
  branch_step: number | null;
  continuation_mode: string | null;
  student_id: string;
  user_request: string;
  model: string;
  steps: StepOut[];
  validation: ValidationOut | null;
  final_schedule: ScheduleEntry[] | null;
  injected_step: number | null;
  observed_failure_step: number | null;
  tokens: number;
  seconds: number;
}

export type LocalizerMethod = 'rule' | 'llm' | 'hybrid';

export interface LocalizeIn {
  method: LocalizerMethod;
}

export interface EvidenceOut {
  step_number: number;
  field: string;
  value: unknown;
  note: string;
}

export interface DiagnosisOut {
  diagnosis_id: string;
  method: LocalizerMethod;
  predicted_step: number | null;
  failure_type: string | null;
  reason: string;
  evidence: EvidenceOut[];
  tokens: number;
  seconds: number;
  used_llm: boolean;
  affected_steps: number[];
  injected_step: number | null;
}

export type ResumeMode = 'forced_action' | 'repair_hint';

export interface ResumeIn {
  step: number;
  mode: ResumeMode;
  diagnosis_id?: string | null;
  tool_name?: string | null;
  arguments?: JsonObject | null;
}

export interface TokenSplit {
  localization: number;
  repair: number;
  replay: number;
  total: number;
}

export interface RunCost {
  run_id: string;
  kind: string;
  status: string;
  passed: boolean;
  tokens: TokenSplit;
  seconds: number;
  steps_reused: number;
  steps_new: number;
}

export interface CompareOut {
  original: RunCost;
  other: RunCost;
  restart: RunCost | null;
}

export interface ResetOut {
  runs_cleared: number;
}

export interface StreamDone {
  status: RunStatus | 'timeout';
}

// GET /api/results returns the frozen summary file as stored. These types
// follow that file, which has no model in models.py.

export interface Ratio {
  value: number;
  numerator: number;
  denominator: number;
}

export interface ResultsScenario {
  id: string;
  title: string;
  failure_type: string;
  label_status: string;
  counterfactual_passes: number;
  counterfactual_trials: number;
  original_runs: number;
  failed: number;
  no_failure: number;
  fault_not_delivered: number;
}

export interface LocalizerSummary {
  method: LocalizerMethod;
  n: number;
  exact_step_accuracy: Ratio;
  within_one_accuracy: Ratio;
  failure_type_accuracy: Ratio;
  no_finding: Ratio;
  mean_tokens: number;
  mean_seconds: number;
  run_ids: string[];
  consistency: Ratio;
}

export interface StrategySummary {
  strategy: string;
  method: LocalizerMethod | null;
  n: number;
  // How many cases got a recovery run at all. A localizer with no finding has none.
  attempted: Ratio;
  recovery_success: Ratio;
  recovered_and_cheaper: Ratio;
  // Means cover attempted recoveries only, so they are null when there were none.
  mean_localization_tokens: number | null;
  mean_repair_tokens: number | null;
  mean_replay_tokens: number | null;
  mean_total_tokens: number | null;
  mean_seconds: number | null;
  mean_restart_tokens: number | null;
  run_ids: string[];
}

export interface OffsetBucket {
  offset: number;
  n: number;
  successes: number;
  run_ids: string[];
}

export interface MissedCase {
  scenario_id: string;
  trial: number;
  method: LocalizerMethod;
  why: string;
  true_step: number;
  predicted_step: number | null;
  true_type: string;
  predicted_type: string | null;
  localizer_reason: string;
  recovered: boolean;
  original_run_id: string;
  recovery_run_id: string | null;
}

export interface ResultsOut {
  experiment_id: string;
  computed_at: string;
  agent_models: string[];
  judge_models: string[];
  run_count: number;
  runs_errored: number;
  scenario_count: number;
  scenarios: ResultsScenario[];
  by_localizer: Partial<Record<LocalizerMethod, LocalizerSummary>>;
  by_strategy: Record<string, StrategySummary>;
  recovery_by_offset: OffsetBucket[];
  missed_cases: MissedCase[];
  frozen: { version: string; frozen_on: string };
}
