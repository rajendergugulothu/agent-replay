import type { ScenarioOut } from '../api/types';
import { Notice } from '../components/Notice';
import { RunOutcome } from '../components/RunOutcome';
import { ScheduleTable } from '../components/ScheduleTable';
import { Timeline } from '../components/Timeline';
import { markSteps } from '../lib/timeline';
import type { RunStream } from '../lib/useRunStream';

interface Props {
  stream: RunStream;
  scenario: ScenarioOut | null;
  onNext: () => void;
}

export function RunScreen({ stream, scenario, onNext }: Props): JSX.Element {
  const { run, steps, streaming, error } = stream;
  const finished = run !== null && run.status !== 'running';

  return (
    <div className="split">
      <section className="split__main" aria-labelledby="run-heading">
        <header className="screen-head">
          <h1 id="run-heading">{streaming ? 'The agent is running' : 'The agent has finished'}</h1>
          {scenario && <p className="lead">&ldquo;{scenario.user_request}&rdquo;</p>}
        </header>
        {error && <Notice kind="error">{error}</Notice>}
        <Timeline
          label="Steps of the run"
          steps={steps}
          marks={markSteps(steps, null)}
          validation={run?.validation ?? null}
          live={streaming}
        />
        {!streaming && steps.length === 0 && !error && (
          <Notice kind="info">This run recorded no steps.</Notice>
        )}
      </section>

      <aside className="split__side" aria-label="Run result">
        {!finished && !error && <Notice kind="loading">Steps appear here as the agent takes them.</Notice>}
        {finished && run && (
          <>
            <RunOutcome run={run} />
            {run.final_schedule && (
              <div className="panel">
                <h2>Schedule the agent submitted</h2>
                <ScheduleTable schedule={run.final_schedule} />
              </div>
            )}
            {run.status !== 'error' && (
              <div className="panel panel--next">
                <p>
                  {run.status === 'failed'
                    ? 'One of these steps caused the failure. Which one?'
                    : 'This run passed. You can still ask the localizers what they see.'}
                </p>
                <button type="button" className="button button--primary button--large" onClick={onNext}>
                  Find the faulty step
                </button>
              </div>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
