import type { ScenarioOut } from '../api/types';
import { Notice } from '../components/Notice';
import { plainLabel } from '../lib/format';

interface Props {
  scenarios: ScenarioOut[] | null;
  loadError: string | null;
  selectedId: string | null;
  onSelect: (scenarioId: string) => void;
  onRun: () => void;
  starting: boolean;
  startError: string | null;
}

interface TaskGroup {
  studentId: string;
  userRequest: string;
  scenarios: ScenarioOut[];
}

/** Scenarios that share a student and request are the same task with different faults. */
function groupByTask(scenarios: ScenarioOut[]): TaskGroup[] {
  const groups = new Map<string, TaskGroup>();
  for (const scenario of scenarios) {
    const key = `${scenario.student_id}\n${scenario.user_request}`;
    const group = groups.get(key) ?? {
      studentId: scenario.student_id,
      userRequest: scenario.user_request,
      scenarios: [],
    };
    group.scenarios.push(scenario);
    groups.set(key, group);
  }
  return [...groups.values()];
}

export function StartScreen(props: Props): JSX.Element {
  const { scenarios, loadError, selectedId, onSelect, onRun, starting, startError } = props;

  if (loadError) return <Notice kind="error">{loadError}</Notice>;
  if (scenarios === null) return <Notice kind="loading">Loading scenarios</Notice>;
  if (scenarios.length === 0) return <Notice kind="info">No scenarios are available on this server.</Notice>;

  const selected = scenarios.find((s) => s.id === selectedId);

  return (
    <div className="start">
      <header className="screen-head">
        <h1>Watch an AI agent fail, then fix it</h1>
        <p className="lead">
          A course-planning agent builds a student&apos;s schedule with four tools. Each scenario plants one
          fault in its run. Pick one and run the agent.
        </p>
      </header>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          onRun();
        }}
      >
        {groupByTask(scenarios).map((group) => (
          <fieldset key={`${group.studentId}-${group.userRequest}`} className="task-group">
            <legend>
              <span className="task-group__who">Student {group.studentId} asks</span>
              <span className="task-group__request">&ldquo;{group.userRequest}&rdquo;</span>
            </legend>
            <div className="cards">
              {group.scenarios.map((scenario) => (
                <label
                  key={scenario.id}
                  className={`card${scenario.id === selectedId ? ' card--selected' : ''}`}
                  data-scenario={scenario.id}
                >
                  <input
                    type="radio"
                    name="scenario"
                    value={scenario.id}
                    checked={scenario.id === selectedId}
                    onChange={() => onSelect(scenario.id)}
                  />
                  <span className="card__body">
                    <span className="card__title">{scenario.title}</span>
                    <span className="card__description">{scenario.description}</span>
                    <span className="card__labels">
                      <span className="pill">{plainLabel(scenario.failure_type)}</span>
                      {scenario.label_status !== 'confirmed' && (
                        <span className="pill pill--quiet">Label: {plainLabel(scenario.label_status)}</span>
                      )}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        ))}

        <div className="action-bar">
          <p className="action-bar__text">
            {selected ? (
              <>
                <span className="muted">Selected: </span>
                {selected.title}
              </>
            ) : (
              'Pick a scenario above.'
            )}
          </p>
          {startError && <Notice kind="error">{startError}</Notice>}
          <button type="submit" className="button button--primary button--large" disabled={!selected || starting}>
            {starting ? 'Starting' : 'Run the agent'}
          </button>
        </div>
      </form>
    </div>
  );
}
