import { useEffect, useState } from 'react';
import { compareRuns, restartRun } from '../api/client';
import type { CompareOut, RunCost } from '../api/types';
import { Notice } from '../components/Notice';
import { StatusMark } from '../components/StatusMark';
import { formatSeconds, formatTokens } from '../lib/format';
import { friendlyError, runErrorMessage } from '../lib/messages';
import type { RunStream } from '../lib/useRunStream';

interface Props {
  originalId: string;
  repairedId: string;
  restart: RunStream;
  onRestarted: (runId: string) => void;
  onNext: () => void;
}

const PARTS = [
  { key: 'localization', label: 'Localization' },
  { key: 'repair', label: 'Repair' },
  { key: 'replay', label: 'Replay' },
] as const;

/** Which recovery was cheaper, from the returned token totals only. */
export function verdict(repaired: RunCost, restart: RunCost): string {
  const difference = Math.abs(repaired.tokens.total - restart.tokens.total);
  const totals = `${formatTokens(repaired.tokens.total)} against ${formatTokens(restart.tokens.total)}`;
  let cost: string;
  if (repaired.tokens.total < restart.tokens.total) {
    cost = `Resuming was cheaper: ${formatTokens(difference)} fewer tokens than a full restart (${totals}).`;
  } else if (repaired.tokens.total > restart.tokens.total) {
    cost = `The full restart was cheaper: resuming used ${formatTokens(difference)} more tokens (${totals}).`;
  } else {
    cost = `Both cost the same: ${formatTokens(repaired.tokens.total)} tokens each.`;
  }
  if (repaired.passed && restart.passed) return `${cost} Both ended in a passing schedule.`;
  if (repaired.passed) return `${cost} Only the resumed run passed.`;
  if (restart.passed) return `${cost} Only the full restart passed.`;
  return `${cost} Neither passed.`;
}

function CostBar({ cost, scale }: { cost: RunCost; scale: number }): JSX.Element {
  // Original runs carry no split, so their whole total is one plain segment.
  const split = cost.tokens.localization + cost.tokens.repair + cost.tokens.replay;
  const parts =
    split > 0
      ? PARTS.map((p) => ({ key: p.key as string, value: cost.tokens[p.key] }))
      : [{ key: 'plain', value: cost.tokens.total }];
  let x = 0;
  return (
    <svg className="cost-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">
      <rect x="0" y="0" width="100" height="10" className="cost-bar__track" />
      {parts.map((part) => {
        const width = scale > 0 ? (part.value / scale) * 100 : 0;
        const rect = <rect key={part.key} x={x} y="0" width={Math.max(width - 0.6, 0)} height="10" className={`fill-${part.key}`} />;
        x += width;
        return rect;
      })}
    </svg>
  );
}

function Outcome({ cost }: { cost: RunCost }): JSX.Element {
  return cost.passed ? (
    <span className="tag tag--clear">
      <StatusMark kind="clear" size={16} /> Passed
    </span>
  ) : (
    <span className="tag tag--predicted">
      <StatusMark kind="predicted" size={16} /> {cost.status === 'error' ? 'Error' : 'Failed'}
    </span>
  );
}

export function CompareScreen({ originalId, repairedId, restart, onRestarted, onNext }: Props): JSX.Element {
  const [compare, setCompare] = useState<CompareOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);

  // Refetch once the restart has finished so its numbers come from the stored run.
  const restartStatus = restart.run?.status ?? null;
  useEffect(() => {
    let cancelled = false;
    compareRuns(originalId, repairedId).then(
      (data) => {
        if (!cancelled) setCompare(data);
      },
      (caught: unknown) => {
        if (!cancelled) setError(friendlyError(caught));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [originalId, repairedId, restartStatus]);

  const startRestart = async (): Promise<void> => {
    setStarting(true);
    setRestartError(null);
    try {
      const started = await restartRun(originalId);
      onRestarted(started.run_id);
    } catch (caught) {
      setRestartError(friendlyError(caught));
    } finally {
      setStarting(false);
    }
  };

  if (error) return <Notice kind="error">{error}</Notice>;
  if (!compare) return <Notice kind="loading">Loading the comparison</Notice>;

  const columns: { title: string; note: string; cost: RunCost }[] = [
    { title: 'Original run', note: 'The run that failed', cost: compare.original },
    { title: 'Repaired run', note: 'Resumed from the repaired step', cost: compare.other },
  ];
  if (compare.restart) {
    columns.push({ title: 'Full restart', note: 'Started again from step 1', cost: compare.restart });
  }
  const scale = Math.max(...columns.map((c) => c.cost.tokens.total));
  const restartRunning = starting || restart.streaming;
  const restartFailure = restart.error ?? (restart.run ? runErrorMessage(restart.run) : null);

  return (
    <div className="compare">
      <header className="screen-head">
        <h1>What did the repair cost?</h1>
        <p className="lead" role="status">
          {compare.restart
            ? verdict(compare.other, compare.restart)
            : 'Run a full restart of the same task to see which recovery is cheaper.'}
        </p>
      </header>

      <div className="compare__body">
        <table className="table compare__table">
          <caption className="visually-hidden">Cost of each run</caption>
          <thead>
            <tr>
              <td />
              {columns.map((column) => (
                <th key={column.title} scope="col">
                  {column.title}
                  <span className="table__sub">{column.note}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Result</th>
              {columns.map((c) => (
                <td key={c.title}>
                  <Outcome cost={c.cost} />
                </td>
              ))}
            </tr>
            <tr className="compare__total">
              <th scope="row">Total tokens</th>
              {columns.map((c) => (
                <td key={c.title}>
                  <span className="big-number">{formatTokens(c.cost.tokens.total)}</span>
                  <CostBar cost={c.cost} scale={scale} />
                </td>
              ))}
            </tr>
            {PARTS.map((part) => (
              <tr key={part.key}>
                <th scope="row">
                  <span className={`swatch fill-${part.key}`} aria-hidden="true" /> {part.label}
                </th>
                {columns.map((c) => (
                  <td key={c.title}>
                    {/* The API reports an original run as a total only, with no split. */}
                    {c.cost.kind === 'original' ? (
                      <span className="muted">not split</span>
                    ) : (
                      formatTokens(c.cost.tokens[part.key])
                    )}
                  </td>
                ))}
              </tr>
            ))}
            <tr>
              <th scope="row">Time</th>
              {columns.map((c) => (
                <td key={c.title}>{formatSeconds(c.cost.seconds)}</td>
              ))}
            </tr>
            <tr>
              <th scope="row">Steps reused</th>
              {columns.map((c) => (
                <td key={c.title}>{c.cost.steps_reused}</td>
              ))}
            </tr>
            <tr>
              <th scope="row">Steps run new</th>
              {columns.map((c) => (
                <td key={c.title}>{c.cost.steps_new}</td>
              ))}
            </tr>
          </tbody>
        </table>

        <aside className="compare__side" aria-label="Next steps">
          {!compare.restart && (
            <div className="panel">
              <h2>Compare with a full restart</h2>
              <p>The usual recovery is to throw the run away and start over. Run it to get its real cost.</p>
              <button
                type="button"
                className="button button--primary button--large"
                disabled={restartRunning}
                onClick={() => void startRestart()}
              >
                Also run a full restart
              </button>
              {restartRunning && (
                <Notice kind="loading">Full restart is running, {restart.steps.length} steps so far</Notice>
              )}
              {restartError && <Notice kind="error">{restartError}</Notice>}
              {restartFailure && <Notice kind="error">{restartFailure}</Notice>}
            </div>
          )}
          <div className="panel">
            <h2>How the tokens are counted</h2>
            <p className="muted">
              Localization is every diagnosis made on the failed run. Repair is the model call that redoes
              the repaired step, which is zero when you supply the fix yourself. Replay is every step that ran
              after it. Reused steps cost nothing. A full restart is all replay.
            </p>
          </div>
          <div className="panel panel--next">
            <p>This was one run. The results screen shows the same comparison over the whole experiment.</p>
            <button type="button" className="button button--large" onClick={onNext}>
              See the experiment results
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}
