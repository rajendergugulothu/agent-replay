import { render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Ratio, ResultsOut } from '../api/types';
// A real response saved from GET /api/results on the development server. It is
// fake-model test data produced by backend/tests/e2e_server.py, not experiment results.
import fixture from '../test/fixtures/results.fake-model.json';
import { ResultsScreen, ResultsView, offsetLabel, strategyLabel } from './ResultsScreen';

const results = fixture as ResultsOut;

function ratioText(ratio: Ratio): string {
  return `${Math.round(ratio.value * 100)}% (${ratio.numerator} of ${ratio.denominator})`;
}

function tokens(n: number | null): string {
  if (n === null) return 'n/a';
  return Math.round(n).toLocaleString('en-US');
}

function row(container: HTMLElement, selector: string): HTMLElement {
  const found = container.querySelector<HTMLElement>(selector);
  if (!found) throw new Error(`no row for ${selector}`);
  return found;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ResultsView', () => {
  it('shows where the numbers came from', () => {
    render(<ResultsView results={results} />);
    const provenance = screen.getByLabelText('Where these numbers come from');
    expect(provenance).toHaveTextContent(results.frozen.version);
    expect(provenance).toHaveTextContent(results.frozen.frozen_on);
    expect(provenance).toHaveTextContent(`${results.run_count} (${results.runs_errored} errored)`);
    for (const model of [...results.agent_models, ...results.judge_models]) {
      expect(provenance).toHaveTextContent(model);
    }
  });

  it('shows every localizer number with its counts', () => {
    const { container } = render(<ResultsView results={results} />);
    const localizers = Object.values(results.by_localizer);
    expect(localizers.length).toBeGreaterThan(0);
    for (const localizer of localizers) {
      const cells = within(row(container, `tr[data-localizer="${localizer.method}"]`));
      for (const ratio of [localizer.exact_step_accuracy, localizer.no_finding]) {
        expect(cells.getAllByText(ratioText(ratio)).length).toBeGreaterThan(0);
      }
      expect(cells.getByText(tokens(localizer.mean_tokens))).toBeInTheDocument();
    }
  });

  it('shows every strategy number with its counts', () => {
    const { container } = render(<ResultsView results={results} />);
    for (const [key, strategy] of Object.entries(results.by_strategy)) {
      const line = row(container, `tr[data-strategy="${key}"]`);
      expect(line).toHaveTextContent(strategyLabel(strategy));
      expect(line).toHaveTextContent(ratioText(strategy.recovery_success));
      expect(line).toHaveTextContent(ratioText(strategy.recovered_and_cheaper));
      expect(line).toHaveTextContent(ratioText(strategy.attempted));
      const cells = [...line.querySelectorAll('td.num')].map((cell) => cell.textContent);
      expect(cells).toEqual([
        tokens(strategy.mean_localization_tokens),
        tokens(strategy.mean_repair_tokens),
        tokens(strategy.mean_replay_tokens),
        tokens(strategy.mean_total_tokens),
        tokens(strategy.mean_restart_tokens),
      ]);
    }
  });

  it('draws one accuracy bar per localizer and one recovery bar per strategy', () => {
    render(<ResultsView results={results} />);
    const chart = screen.getByRole('img', { name: /Exact-step accuracy per localizer/ });
    const titles = [...chart.querySelectorAll('title')].map((t) => t.textContent ?? '');
    const localizers = Object.values(results.by_localizer);
    const strategies = Object.values(results.by_strategy);
    expect(titles).toHaveLength(localizers.length + strategies.length);
    for (const localizer of localizers) {
      const { numerator, denominator } = localizer.exact_step_accuracy;
      expect(titles.some((t) => t.includes('Exact-step accuracy') && t.includes(`${numerator} of ${denominator}`))).toBe(true);
    }
    for (const strategy of strategies) {
      const { numerator, denominator } = strategy.recovery_success;
      expect(titles.some((t) => t.includes('Recovery success') && t.includes(`${numerator} of ${denominator}`))).toBe(true);
    }
  });

  it('shows recovery by distance from the true step', () => {
    render(<ResultsView results={results} />);
    const chart = screen.getByRole('img', { name: /Recovery success by distance/ });
    expect(results.recovery_by_offset.length).toBeGreaterThan(0);
    for (const bucket of results.recovery_by_offset) {
      expect(chart).toHaveTextContent(offsetLabel(bucket.offset));
      expect(chart).toHaveTextContent(`${bucket.successes} of ${bucket.n}`);
    }
  });

  it('lists every scenario, including ones that were not confirmed', () => {
    const { container } = render(<ResultsView results={results} />);
    expect(container.querySelectorAll('tr[data-scenario]')).toHaveLength(results.scenario_count);
    expect(results.scenarios.some((s) => s.label_status !== 'confirmed')).toBe(true);
    for (const scenario of results.scenarios) {
      const line = row(container, `tr[data-scenario="${scenario.id}"]`);
      expect(line).toHaveTextContent(scenario.title);
      expect(line).toHaveTextContent(`${scenario.counterfactual_passes} of ${scenario.counterfactual_trials}`);
      const cells = [...line.querySelectorAll('td.num')].map((cell) => cell.textContent);
      expect(cells).toEqual(
        [scenario.original_runs, scenario.failed, scenario.no_failure, scenario.fault_not_delivered].map(String),
      );
    }
  });

  it('lists every missed case with its reason', () => {
    render(<ResultsView results={results} />);
    const section = screen.getByRole('region', { name: `Missed cases (${results.missed_cases.length})` });
    expect(within(section).getAllByRole('row')).toHaveLength(results.missed_cases.length + 1);
    for (const reason of new Set(results.missed_cases.map((m) => m.localizer_reason))) {
      expect(within(section).getAllByText(reason).length).toBeGreaterThan(0);
    }
  });
});

describe('ResultsScreen', () => {
  it('shows an empty state when the experiment has not been run', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: 'no_results', message: 'not frozen' } }), { status: 404 }),
      ),
    );
    render(<ResultsScreen />);
    expect(await screen.findByText(/The experiment has not been run yet/)).toBeInTheDocument();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });

  it('renders the response from the API', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(results), { status: 200 })));
    render(<ResultsScreen />);
    expect(await screen.findByRole('heading', { name: 'Experiment results' })).toBeInTheDocument();
  });

  it('shows an error when the server cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('failed'))));
    render(<ResultsScreen />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the server');
  });
});
