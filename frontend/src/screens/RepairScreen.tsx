import { useState } from 'react';
import { ApiError, resumeRun } from '../api/client';
import type { DiagnosisOut, JsonObject, ResumeIn, RunOut, ToolOut } from '../api/types';
import { EditForm } from '../components/EditForm';
import { InjectionDetail } from '../components/InjectionDetail';
import { Notice } from '../components/Notice';
import { RunOutcome } from '../components/RunOutcome';
import { Timeline } from '../components/Timeline';
import { friendlyError, localizerName } from '../lib/messages';
import { markSteps } from '../lib/timeline';
import type { RunStream } from '../lib/useRunStream';

interface Props {
  original: RunOut;
  diagnosis: DiagnosisOut | null;
  tools: ToolOut[];
  repaired: RunStream;
  onResumed: (runId: string) => void;
  onNext: () => void;
}

export function RepairScreen({ original, diagnosis, tools, repaired, onResumed, onNext }: Props): JSX.Element {
  // Only steps that called a tool have arguments to edit.
  const editable = original.steps.filter((s) => s.tool_name !== null);
  const fallback = editable[editable.length - 1]?.step_number ?? 1;
  const [stepNumber, setStepNumber] = useState<number>(diagnosis?.predicted_step ?? fallback);
  const step = editable.find((s) => s.step_number === stepNumber) ?? editable[0];
  const [toolOverride, setToolOverride] = useState<string | null>(null);
  const toolName = toolOverride ?? step?.tool_name ?? null;
  const tool = tools.find((t) => t.name === toolName);

  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resume = async (body: ResumeIn): Promise<void> => {
    setBusy(true);
    setServerError(null);
    setError(null);
    try {
      const started = await resumeRun(original.id, body);
      onResumed(started.run_id);
    } catch (caught) {
      // The schema complaint belongs next to the form. Everything else is a banner.
      if (caught instanceof ApiError && caught.code === 'invalid_arguments') setServerError(caught.message);
      else setError(friendlyError(caught));
    } finally {
      setBusy(false);
    }
  };

  const resumeWithFix = (args: JsonObject): void => {
    if (!step || !tool) return;
    void resume({ step: step.step_number, mode: 'forced_action', tool_name: tool.name, arguments: args });
  };

  const resumeWithHint = (): void => {
    if (!step) return;
    void resume({
      step: step.step_number,
      mode: 'repair_hint',
      diagnosis_id: diagnosis?.diagnosis_id ?? null,
    });
  };

  if (!step) return <Notice kind="info">This run has no tool steps to repair.</Notice>;

  const working = busy || repaired.streaming;
  const showRepaired = repaired.runId !== null;

  return (
    <div className="split split--even">
      <section className="split__side" aria-labelledby="repair-heading">
        <header className="screen-head">
          <h1 id="repair-heading">Repair one step, keep the rest</h1>
          <p className="lead">
            Steps before the one you repair are reused from the original run. Only the steps after it run again.
          </p>
        </header>

        <div className="panel">
          <div className="edit-form__field">
            <label htmlFor="repair-step">Step to repair</label>
            <select
              id="repair-step"
              value={step.step_number}
              onChange={(e) => {
                setStepNumber(Number(e.target.value));
                setToolOverride(null);
                setServerError(null);
              }}
            >
              {editable.map((s) => (
                <option key={s.step_number} value={s.step_number}>
                  Step {s.step_number}: {s.tool_name}
                  {s.step_number === diagnosis?.predicted_step ? ' (predicted fault)' : ''}
                </option>
              ))}
            </select>
            {diagnosis && diagnosis.predicted_step === null && (
              <p className="edit-form__hint">
                {localizerName(diagnosis.method)} found no step, so pick the one you suspect.
              </p>
            )}
          </div>

          <div className="edit-form__field">
            <label htmlFor="repair-tool">Tool to call at this step</label>
            <select
              id="repair-tool"
              value={toolName ?? ''}
              onChange={(e) => {
                setToolOverride(e.target.value);
                setServerError(null);
              }}
            >
              {tools.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>

          {step.injection && (
            <details className="reveal">
              <summary>Show what was planted at this step</summary>
              <InjectionDetail injection={step.injection} />
            </details>
          )}

          {tool ? (
            <EditForm
              key={`${step.step_number}-${tool.name}`}
              tool={tool}
              initialArgs={step.tool_args}
              submitLabel={`Resume from step ${step.step_number} with this fix`}
              busy={working}
              serverError={serverError}
              onSubmit={resumeWithFix}
            />
          ) : (
            <Notice kind="error">The tool list could not be loaded, so arguments cannot be edited.</Notice>
          )}

          <div className="or-divider" role="presentation">
            or
          </div>
          <button type="button" className="button" disabled={working} onClick={resumeWithHint}>
            Resume with the diagnosis as a hint
          </button>
          <p className="edit-form__hint">
            The agent is told what went wrong at step {step.step_number} and chooses the fix itself.
          </p>
          {error && <Notice kind="error">{error}</Notice>}
        </div>
      </section>

      <section className="split__main" aria-label={showRepaired ? 'Repaired run' : 'Original run'}>
        {showRepaired ? (
          <>
            <h2 className="section-title">Repaired run</h2>
            {repaired.error && <Notice kind="error">{repaired.error}</Notice>}
            {repaired.run && repaired.run.status !== 'running' && <RunOutcome run={repaired.run} />}
            {repaired.run && repaired.run.status !== 'running' && repaired.run.status !== 'error' && (
              <div className="panel panel--next">
                <button type="button" className="button button--primary button--large" onClick={onNext}>
                  Compare the cost
                </button>
              </div>
            )}
            <Timeline
              label="Steps of the repaired run"
              steps={repaired.steps}
              marks={markSteps(repaired.steps, null)}
              validation={repaired.run?.validation ?? null}
              live={repaired.streaming}
            />
          </>
        ) : (
          <>
            <h2 className="section-title">Original run</h2>
            <Timeline
              label="Steps of the original run"
              steps={original.steps}
              marks={markSteps(original.steps, diagnosis)}
              validation={original.validation}
              live={false}
            />
          </>
        )}
      </section>
    </div>
  );
}
