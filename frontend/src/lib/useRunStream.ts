import { useCallback, useEffect, useState } from 'react';
import { getRun, runStreamUrl } from '../api/client';
import type { RunOut, StepOut } from '../api/types';
import { friendlyError } from './messages';

export interface RunStream {
  runId: string | null;
  /** Steps received so far. Once the run has finished these are the final steps. */
  steps: StepOut[];
  /** The full run, available after the stream has ended. */
  run: RunOut | null;
  streaming: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

interface State {
  runId: string | null;
  steps: StepOut[];
  run: RunOut | null;
  streaming: boolean;
  error: string | null;
}

const IDLE: State = { runId: null, steps: [], run: null, streaming: false, error: null };

/** Follow one run: steps arrive over SSE, then the final state is fetched once. */
export function useRunStream(runId: string | null): RunStream {
  const [state, setState] = useState<State>(IDLE);

  useEffect(() => {
    if (runId === null) {
      setState(IDLE);
      return;
    }
    let cancelled = false;
    setState({ ...IDLE, runId, streaming: true });
    const source = new EventSource(runStreamUrl(runId));

    const finish = (): void => {
      source.close();
      getRun(runId).then(
        (run) => {
          if (cancelled) return;
          // Still running here means the stream ended early, not the run.
          const error =
            run.status === 'running' ? 'The live connection to this run was lost before it finished.' : null;
          setState({ runId, steps: run.steps, run, streaming: false, error });
        },
        (error: unknown) => {
          if (!cancelled) setState((s) => ({ ...s, streaming: false, error: friendlyError(error) }));
        },
      );
    };

    source.addEventListener('step', (event) => {
      const step = JSON.parse((event as MessageEvent<string>).data) as StepOut;
      setState((s) =>
        // A reconnect replays the stream from step 1, so skip steps already shown.
        s.steps.some((known) => known.step_number === step.step_number)
          ? s
          : { ...s, steps: [...s.steps, step] },
      );
    });
    source.addEventListener('done', finish);
    // Without this the browser would reconnect forever if the server went
    // away. One fetch tells us where the run stands instead.
    source.onerror = finish;

    return () => {
      cancelled = true;
      source.close();
    };
  }, [runId]);

  const refetch = useCallback(async (): Promise<void> => {
    if (runId === null) return;
    const run = await getRun(runId);
    setState((s) => (s.runId === runId ? { ...s, steps: run.steps, run } : s));
  }, [runId]);

  // Between a change of runId and the effect, state still belongs to the old run.
  const current = state.runId === runId ? state : { ...IDLE, runId, streaming: runId !== null };
  return { ...current, refetch };
}
