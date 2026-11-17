import { useEffect, useState } from 'react';
import { ApiError, getResults } from '../api/client';
import type { LocalizerMethod, MissedCase, ResultsOut, StrategySummary } from '../api/types';
import { GroupedBars } from '../charts/GroupedBars';
import type { BarGroup } from '../charts/GroupedBars';
import { StackedCost } from '../charts/StackedCost';
import { Notice } from '../components/Notice';
import { formatCount, formatPercent, formatRatio, formatTokens, plainLabel } from '../lib/format';
import { friendlyError, localizerName } from '../lib/messages';

const METHOD_ORDER: LocalizerMethod[] = ['rule', 'llm', 'hybrid'];

const WHY_LABELS: Record<string, string> = {
  no_finding: 'Found no step',
  wrong_step: 'Blamed the wrong step',
  right_step_wrong_type: 'Right step, wrong failure type',
};

export function strategyLabel(strategy: StrategySummary): string {
  if (strategy.strategy === 'full_restart') return 'Full restart';
  if (strategy.strategy === 'resume_true') return 'Resume at the true step';
  if (strategy.strategy === 'resume_predicted' && strategy.method) {
    return `Resume at step from ${localizerName(strategy.method)}`;
  }
  return plainLabel(strategy.strategy);
}

export function offsetLabel(offset: number): string {
  if (offset === 0) return 'Exact step';
  const steps = `${Math.abs(offset)} ${Math.abs(offset) === 1 ? 'step' : 'steps'}`;
  return offset < 0 ? `${steps} early` : `${steps} late`;
}

function accuracyGroups(results: ResultsOut): BarGroup[] {
  const groups: BarGroup[] = [];
  for (const method of METHOD_ORDER) {
    const localizer = results.by_localizer[method];
    if (!localizer) continue;
    const bars = [
      {
        series: 0,
        value: localizer.exact_step_accuracy.value,
        percent: formatPercent(localizer.exact_step_accuracy.value),
        count: formatCount(localizer.exact_step_accuracy.numerator, localizer.exact_step_accuracy.denominator),
      },
    ];
    const strategy = results.by_strategy[`resume_predicted:${method}`];
    if (strategy) bars.push(recoveryBar(strategy));
    groups.push({ label: localizerName(method), bars });
  }
  // The baselines have no localizer, so they show a recovery bar only.
  for (const strategy of Object.values(results.by_strategy)) {
    if (strategy.method === null) groups.push({ label: strategyLabel(strategy), bars: [recoveryBar(strategy)] });
  }
  return groups;
}

function recoveryBar(strategy: StrategySummary): BarGroup['bars'][number] {
  return {
    series: 1,
    value: strategy.recovery_success.value,
    percent: formatPercent(strategy.recovery_success.value),
    count: formatCount(strategy.recovery_success.numerator, strategy.recovery_success.denominator),
  };
}

function missedOutcome(missed: MissedCase): string {
  if (missed.recovery_run_id === null) return 'No recovery attempted';
  return missed.recovered ? 'Recovered anyway' : 'Recovery failed';
}

interface ViewProps {
  results: ResultsOut;
}

export function ResultsView({ results }: ViewProps): JSX.Element {
  const strategies = Object.values(results.by_strategy);
  const localizers = METHOD_ORDER.flatMap((m) => results.by_localizer[m] ?? []);

  return (
    <div className="results">
      <header className="screen-head">
        <h1>Experiment results</h1>
        <p className="lead">
          Does finding the faulty step lead to a successful recovery that costs less than starting over?
        </p>
      </header>

      <dl className="provenance" aria-label="Where these numbers come from">
        <div>
          <dt>Frozen version</dt>
          <dd>{results.frozen.version}</dd>
        </div>
        <div>
          <dt>Frozen on</dt>
          <dd>{results.frozen.frozen_on}</dd>
        </div>
        <div>
          <dt>Experiment</dt>
          <dd>{results.experiment_id}</dd>
        </div>
        <div>
          <dt>Runs</dt>
          <dd>
            {results.run_count} ({results.runs_errored} errored)
          </dd>
        </div>
        <div>
          <dt>Scenarios</dt>
          <dd>{results.scenario_count}</dd>
        </div>
        <div>
          <dt>Agent model</dt>
          <dd>{results.agent_models.join(', ') || 'None recorded'}</dd>
        </div>
        <div>
          <dt>Judge model</dt>
          <dd>{results.judge_models.join(', ') || 'None recorded'}</dd>
        </div>
      </dl>

      <section className="panel" aria-labelledby="accuracy-heading">
        <h2 id="accuracy-heading">Finding the step, and recovering from it</h2>
        <p className="muted">
          Exact-step accuracy is how often a localizer named the planted step. Recovery success is how often
          the resumed or restarted run passed.
        </p>
        <GroupedBars
          title="Exact-step accuracy per localizer and recovery success per strategy"
          series={['Exact-step accuracy', 'Recovery success']}
          groups={accuracyGroups(results)}
        />
        <table className="table table--compact">
          <caption>Localizers</caption>
          <thead>
            <tr>
              <th scope="col">Localizer</th>
              <th scope="col">Exact step</th>
              <th scope="col">Within one step</th>
              <th scope="col">Failure type right</th>
              <th scope="col">Found nothing</th>
              <th scope="col">Same answer across trials</th>
              <th scope="col" className="num">
                Mean tokens
              </th>
            </tr>
          </thead>
          <tbody>
            {localizers.map((l) => (
              <tr key={l.method} data-localizer={l.method}>
                <th scope="row">{localizerName(l.method)}</th>
                <td>{formatRatio(l.exact_step_accuracy)}</td>
                <td>{formatRatio(l.within_one_accuracy)}</td>
                <td>{formatRatio(l.failure_type_accuracy)}</td>
                <td>{formatRatio(l.no_finding)}</td>
                <td>{formatRatio(l.consistency)}</td>
                <td className="num">{formatTokens(l.mean_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel" aria-labelledby="cost-heading">
        <h2 id="cost-heading">Mean tokens per recovery</h2>
        <p className="muted">
          Each bar is the mean cost of one attempted recovery, split into finding the step, repairing it, and
          running the rest. The dark line marks the mean cost of a full restart. Cases where a localizer found
          no step have no recovery run and are left out of these means. They still count as not recovered.
        </p>
        <StackedCost
          title="Mean tokens per strategy, split into localization, repair and replay"
          rows={strategies.flatMap((s) =>
            // A strategy with no attempted recovery has no cost to draw.
            s.mean_total_tokens === null
              ? []
              : [
                  {
                    label: strategyLabel(s),
                    localization: s.mean_localization_tokens ?? 0,
                    repair: s.mean_repair_tokens ?? 0,
                    replay: s.mean_replay_tokens ?? 0,
                    total: s.mean_total_tokens,
                    reference: s.mean_restart_tokens ?? 0,
                  },
                ],
          )}
        />
        <table className="table table--compact">
          <caption>Strategies</caption>
          <thead>
            <tr>
              <th scope="col">Strategy</th>
              <th scope="col">Attempted</th>
              <th scope="col">Recovered</th>
              <th scope="col">Recovered and cheaper than a restart</th>
              <th scope="col" className="num">
                Localization
              </th>
              <th scope="col" className="num">
                Repair
              </th>
              <th scope="col" className="num">
                Replay
              </th>
              <th scope="col" className="num">
                Mean total
              </th>
              <th scope="col" className="num">
                Mean restart
              </th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(results.by_strategy).map(([key, s]) => (
              <tr key={key} data-strategy={key}>
                <th scope="row">{strategyLabel(s)}</th>
                <td>{formatRatio(s.attempted)}</td>
                <td>{formatRatio(s.recovery_success)}</td>
                <td>{formatRatio(s.recovered_and_cheaper)}</td>
                <td className="num">{formatTokens(s.mean_localization_tokens)}</td>
                <td className="num">{formatTokens(s.mean_repair_tokens)}</td>
                <td className="num">{formatTokens(s.mean_replay_tokens)}</td>
                <td className="num">{formatTokens(s.mean_total_tokens)}</td>
                <td className="num">{formatTokens(s.mean_restart_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel" aria-labelledby="offset-heading">
        <h2 id="offset-heading">Recovery by distance from the true step</h2>
        <p className="muted">
          How often resuming worked when the predicted step was early, exact, or late compared with the
          planted step.
        </p>
        {results.recovery_by_offset.length === 0 ? (
          <Notice kind="info">No recoveries were recorded.</Notice>
        ) : (
          <GroupedBars
            title="Recovery success by distance between the predicted and the true step"
            series={[]}
            groups={results.recovery_by_offset.map((bucket) => ({
              label: offsetLabel(bucket.offset),
              bars: [
                {
                  series: 1,
                  value: bucket.n > 0 ? bucket.successes / bucket.n : 0,
                  percent: bucket.n > 0 ? formatPercent(bucket.successes / bucket.n) : '',
                  count: formatCount(bucket.successes, bucket.n),
                },
              ],
            }))}
          />
        )}
      </section>

      <section className="panel" aria-labelledby="scenarios-heading">
        <h2 id="scenarios-heading">Scenarios</h2>
        <p className="muted">
          Every scenario is listed, including ones where the planted fault did not make the agent fail.
        </p>
        <div className="table-scroll">
          <table className="table table--compact">
            <thead>
              <tr>
                <th scope="col">Scenario</th>
                <th scope="col">Failure type</th>
                <th scope="col">Label</th>
                <th scope="col">Fixing the planted step passes</th>
                <th scope="col" className="num">
                  Runs
                </th>
                <th scope="col" className="num">
                  Failed
                </th>
                <th scope="col" className="num">
                  Did not fail
                </th>
                <th scope="col" className="num">
                  Fault not delivered
                </th>
              </tr>
            </thead>
            <tbody>
              {results.scenarios.map((s) => (
                <tr key={s.id} data-scenario={s.id}>
                  <th scope="row">
                    {s.title}
                    <span className="table__sub">{s.id}</span>
                  </th>
                  <td>{plainLabel(s.failure_type)}</td>
                  <td>
                    <span className={s.label_status === 'confirmed' ? 'pill' : 'pill pill--quiet'}>
                      {plainLabel(s.label_status)}
                    </span>
                  </td>
                  <td>{formatCount(s.counterfactual_passes, s.counterfactual_trials)}</td>
                  <td className="num">{s.original_runs}</td>
                  <td className="num">{s.failed}</td>
                  <td className="num">{s.no_failure}</td>
                  <td className="num">{s.fault_not_delivered}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel" aria-labelledby="missed-heading">
        <h2 id="missed-heading">Missed cases ({results.missed_cases.length})</h2>
        <p className="muted">Every diagnosis that did not name the planted step and failure type.</p>
        {results.missed_cases.length === 0 ? (
          <Notice kind="info">No localizer missed a case.</Notice>
        ) : (
          <div className="table-scroll table-scroll--tall">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th scope="col">Scenario</th>
                  <th scope="col">Trial</th>
                  <th scope="col">Localizer</th>
                  <th scope="col">What went wrong</th>
                  <th scope="col">True step</th>
                  <th scope="col">Predicted</th>
                  <th scope="col">Localizer&apos;s reason</th>
                  <th scope="col">Recovery</th>
                </tr>
              </thead>
              <tbody>
                {results.missed_cases.map((m) => (
                  <tr key={`${m.original_run_id}-${m.method}`}>
                    <th scope="row">{m.scenario_id}</th>
                    <td>{m.trial}</td>
                    <td>{localizerName(m.method)}</td>
                    <td>{WHY_LABELS[m.why] ?? plainLabel(m.why)}</td>
                    <td>
                      {m.true_step}
                      <span className="table__sub">{plainLabel(m.true_type)}</span>
                    </td>
                    <td>
                      {m.predicted_step ?? 'None'}
                      {m.predicted_type && <span className="table__sub">{plainLabel(m.predicted_type)}</span>}
                    </td>
                    <td>{m.localizer_reason}</td>
                    <td>{missedOutcome(m)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export function ResultsScreen(): JSX.Element {
  const [results, setResults] = useState<ResultsOut | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getResults().then(
      (data) => {
        if (!cancelled) setResults(data);
      },
      (caught: unknown) => {
        if (cancelled) return;
        if (caught instanceof ApiError && caught.code === 'no_results') setMissing(true);
        else setError(friendlyError(caught));
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <Notice kind="error">{error}</Notice>;
  if (missing) {
    return (
      <div className="empty">
        <h1>No results yet</h1>
        <p className="lead">The experiment has not been run yet. Results appear here once it has been run and frozen.</p>
      </div>
    );
  }
  if (!results) return <Notice kind="loading">Loading results</Notice>;
  return <ResultsView results={results} />;
}
