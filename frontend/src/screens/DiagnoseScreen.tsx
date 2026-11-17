import type { DiagnosisOut, EvidenceOut, LocalizerMethod, RunOut, StepOut } from '../api/types';
import { InjectionDetail } from '../components/InjectionDetail';
import { Notice } from '../components/Notice';
import { StatusMark } from '../components/StatusMark';
import { Timeline } from '../components/Timeline';
import { formatJson, formatSeconds, formatTokens, plainLabel } from '../lib/format';
import { LOCALIZERS, localizerName } from '../lib/messages';
import { markSteps } from '../lib/timeline';

interface Props {
  run: RunOut;
  diagnoses: Partial<Record<LocalizerMethod, DiagnosisOut>>;
  activeMethod: LocalizerMethod | null;
  busyMethod: LocalizerMethod | null;
  error: string | null;
  onDiagnose: (method: LocalizerMethod) => void;
  onSelect: (method: LocalizerMethod) => void;
  onNext: () => void;
}

/** The value an evidence item points at. Read from the step when the localizer sent none. */
function evidenceValue(evidence: EvidenceOut, steps: StepOut[]): unknown {
  if (evidence.value !== null && evidence.value !== undefined) return evidence.value;
  const step = steps.find((s) => s.step_number === evidence.step_number);
  let value: unknown = step;
  for (const key of evidence.field.split('.')) {
    if (typeof value !== 'object' || value === null) return undefined;
    value = (value as Record<string, unknown>)[key];
  }
  return value;
}

function summaryFor(diagnosis: DiagnosisOut): string {
  return diagnosis.predicted_step === null ? 'No step found' : `Blames step ${diagnosis.predicted_step}`;
}

function TruthPanel({ run, diagnosis }: { run: RunOut; diagnosis: DiagnosisOut }): JSX.Element {
  const truth = run.injected_step ?? diagnosis.injected_step;
  if (truth === null) {
    return <p className="muted">No fault was planted in this run.</p>;
  }
  const planted = run.steps.find((s) => s.injection !== null);
  const predicted = diagnosis.predicted_step;
  return (
    <>
      <p className="truth">
        <StatusMark kind="planted" /> The fault was planted at <strong>step {truth}</strong>.
      </p>
      {predicted === null && <p className="truth truth--miss">This method did not find it.</p>}
      {predicted !== null && predicted === truth && (
        <p className="truth truth--hit">
          <StatusMark kind="clear" /> The prediction is correct.
        </p>
      )}
      {predicted !== null && predicted !== truth && (
        <p className="truth truth--miss">
          <StatusMark kind="affected" /> The prediction (step {predicted}) is {Math.abs(predicted - truth)}{' '}
          {Math.abs(predicted - truth) === 1 ? 'step' : 'steps'} {predicted > truth ? 'too late' : 'too early'}.
        </p>
      )}
      {planted?.injection && <InjectionDetail injection={planted.injection} />}
    </>
  );
}

export function DiagnoseScreen(props: Props): JSX.Element {
  const { run, diagnoses, activeMethod, busyMethod, error, onDiagnose, onSelect, onNext } = props;
  const diagnosis = activeMethod ? (diagnoses[activeMethod] ?? null) : null;

  return (
    <div className="split">
      <section className="split__main" aria-labelledby="diagnose-heading">
        <header className="screen-head">
          <h1 id="diagnose-heading">Which step went wrong?</h1>
          <p className="lead">
            Ask a localizer to point at the faulty step. Try more than one and compare their answers.
          </p>
        </header>
        <Timeline
          label="Steps of the run, marked by the diagnosis"
          steps={run.steps}
          marks={markSteps(run.steps, diagnosis)}
          validation={run.validation}
          live={false}
        />
      </section>

      <aside className="split__side" aria-label="Diagnosis">
        <div className="panel">
          <h2>Choose a localizer</h2>
          <ul className="localizers">
            {LOCALIZERS.map((localizer) => {
              const done = diagnoses[localizer.method];
              const busy = busyMethod === localizer.method;
              return (
                <li key={localizer.method}>
                  <button
                    type="button"
                    className={`localizer${activeMethod === localizer.method ? ' localizer--active' : ''}`}
                    aria-pressed={activeMethod === localizer.method}
                    disabled={busyMethod !== null}
                    onClick={() => (done ? onSelect(localizer.method) : onDiagnose(localizer.method))}
                  >
                    <span className="localizer__name">{localizer.name}</span>
                    <span className="localizer__state">
                      {busy ? 'Diagnosing' : done ? summaryFor(done) : 'Run'}
                    </span>
                    <span className="localizer__explanation">{localizer.explanation}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {busyMethod && <Notice kind="loading">{localizerName(busyMethod)} is reading the trace</Notice>}
          {error && <Notice kind="error">{error}</Notice>}
        </div>

        {!diagnosis && !busyMethod && (
          <Notice kind="info">No diagnosis yet. Pick a localizer above to mark the timeline.</Notice>
        )}

        {diagnosis && (
          <div className="panel" aria-live="polite">
            <h2>{localizerName(diagnosis.method)} says</h2>
            {diagnosis.predicted_step === null ? (
              <p className="finding finding--none">No step found by this method</p>
            ) : (
              <p className="finding">
                <StatusMark kind="predicted" size={22} /> Step {diagnosis.predicted_step} is the fault
              </p>
            )}
            <p>{diagnosis.reason}</p>

            <dl className="facts">
              <div>
                <dt>Failure type</dt>
                <dd>{diagnosis.failure_type ? plainLabel(diagnosis.failure_type) : 'None given'}</dd>
              </div>
              <div>
                <dt>Tokens spent</dt>
                <dd>{formatTokens(diagnosis.tokens)}</dd>
              </div>
              <div>
                <dt>Time</dt>
                <dd>{formatSeconds(diagnosis.seconds)}</dd>
              </div>
              <div>
                <dt>Used an LLM</dt>
                <dd>{diagnosis.used_llm ? 'Yes' : 'No'}</dd>
              </div>
            </dl>

            <h3>Evidence</h3>
            {diagnosis.evidence.length === 0 ? (
              <p className="muted">This method gave no evidence.</p>
            ) : (
              <ul className="evidence">
                {diagnosis.evidence.map((item, i) => {
                  const value = evidenceValue(item, run.steps);
                  return (
                    <li key={i}>
                      <span className="evidence__where">
                        Step {item.step_number}, <code>{item.field}</code>
                      </span>
                      {value !== undefined && <mark className="evidence__value">{formatJson(value)}</mark>}
                      {item.note && <span className="evidence__note">{item.note}</span>}
                    </li>
                  );
                })}
              </ul>
            )}

            <h3>What was actually planted</h3>
            <TruthPanel run={run} diagnosis={diagnosis} />
          </div>
        )}

        {diagnosis && (
          <div className="panel panel--next">
            <button type="button" className="button button--primary button--large" onClick={onNext}>
              {diagnosis.predicted_step === null ? 'Repair a step' : `Repair step ${diagnosis.predicted_step}`}
            </button>
          </div>
        )}
      </aside>
    </div>
  );
}
