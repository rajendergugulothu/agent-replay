import { useCallback, useEffect, useRef, useState } from 'react';
import { getHealth, getScenarios, getTools, localize, resetDemo, startRun } from './api/client';
import type { DiagnosisOut, HealthOut, LocalizerMethod, ScenarioOut, ToolOut } from './api/types';
import { Notice } from './components/Notice';
import { StepIndicator } from './components/StepIndicator';
import type { Screen } from './components/StepIndicator';
import { friendlyError } from './lib/messages';
import { useRunStream } from './lib/useRunStream';
import { CompareScreen } from './screens/CompareScreen';
import { DiagnoseScreen } from './screens/DiagnoseScreen';
import { RepairScreen } from './screens/RepairScreen';
import { ResultsScreen } from './screens/ResultsScreen';
import { RunScreen } from './screens/RunScreen';
import { StartScreen } from './screens/StartScreen';

type Diagnoses = Partial<Record<LocalizerMethod, DiagnosisOut>>;

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

export function App(): JSX.Element {
  const [screen, setScreen] = useState<Screen>('start');
  const [health, setHealth] = useState<HealthOut | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioOut[] | null>(null);
  const [tools, setTools] = useState<ToolOut[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [scenarioId, setScenarioId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const [originalId, setOriginalId] = useState<string | null>(null);
  const [repairedId, setRepairedId] = useState<string | null>(null);
  const [restartId, setRestartId] = useState<string | null>(null);
  const original = useRunStream(originalId);
  const repaired = useRunStream(repairedId);
  const restart = useRunStream(restartId);

  const [diagnoses, setDiagnoses] = useState<Diagnoses>({});
  const [activeMethod, setActiveMethod] = useState<LocalizerMethod | null>(null);
  const [busyMethod, setBusyMethod] = useState<LocalizerMethod | null>(null);
  const [diagnoseError, setDiagnoseError] = useState<string | null>(null);

  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getHealth(), getScenarios(), getTools()]).then(
      ([healthOut, scenarioList, toolList]) => {
        if (cancelled) return;
        setHealth(healthOut);
        setScenarios(scenarioList);
        setTools(toolList);
        setScenarioId((current) => current ?? scenarioList[0]?.id ?? null);
      },
      (error: unknown) => {
        if (!cancelled) setLoadError(friendlyError(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const goTo = useCallback((next: Screen): void => {
    setScreen(next);
  }, []);

  // Runs after the new screen has rendered, so the view starts at its top and
  // keyboard and screen reader users land on the new content.
  useEffect(() => {
    window.scrollTo(0, 0);
    mainRef.current?.focus({ preventScroll: true });
  }, [screen]);

  const clearSession = useCallback((): void => {
    setOriginalId(null);
    setRepairedId(null);
    setRestartId(null);
    setDiagnoses({});
    setActiveMethod(null);
    setDiagnoseError(null);
    setStartError(null);
  }, []);

  const reset = useCallback(async (): Promise<void> => {
    setResetting(true);
    setResetError(null);
    try {
      await resetDemo();
      clearSession();
      goTo('start');
    } catch (error) {
      setResetError(friendlyError(error));
    } finally {
      setResetting(false);
    }
  }, [clearSession, goTo]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (event.key !== 'R' || !event.shiftKey || event.ctrlKey || event.metaKey || event.altKey) return;
      if (isTyping(event.target)) return;
      event.preventDefault();
      void reset();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [reset]);

  const run = async (): Promise<void> => {
    if (!scenarioId) return;
    setStarting(true);
    setStartError(null);
    try {
      const started = await startRun(scenarioId);
      clearSession();
      setOriginalId(started.run_id);
      goTo('run');
    } catch (error) {
      setStartError(friendlyError(error));
    } finally {
      setStarting(false);
    }
  };

  const diagnose = async (method: LocalizerMethod): Promise<void> => {
    if (!originalId) return;
    setBusyMethod(method);
    setDiagnoseError(null);
    try {
      const diagnosis = await localize(originalId, method);
      setDiagnoses((current) => ({ ...current, [method]: diagnosis }));
      setActiveMethod(method);
      // The run only reveals what was planted once it has a diagnosis.
      await original.refetch();
    } catch (error) {
      setDiagnoseError(friendlyError(error));
    } finally {
      setBusyMethod(null);
    }
  };

  const originalRun = original.run;
  const originalDone = originalRun !== null && originalRun.status !== 'running' && originalRun.status !== 'error';
  const diagnosed = Object.keys(diagnoses).length > 0;
  const repairedDone = repaired.run !== null && repaired.run.status !== 'running';
  const enabled: Record<Screen, boolean> = {
    start: true,
    run: originalId !== null,
    diagnose: originalDone,
    repair: originalDone && diagnosed,
    compare: originalDone && repairedDone,
    results: true,
  };
  const activeDiagnosis = activeMethod ? (diagnoses[activeMethod] ?? null) : null;
  const scenario = scenarios?.find((s) => s.id === (originalRun?.scenario_id ?? scenarioId)) ?? null;

  return (
    <div className="app">
      <header className="app__header">
        <span className="app__name">Agent Replay</span>
        <StepIndicator current={screen} enabled={enabled} onSelect={goTo} />
        <div className="app__tools">
          {health && (
            <span className={`badge badge--${health.mode}`} title={`Server version ${health.version}`}>
              <span className="badge__dot" aria-hidden="true" />
              {health.mode === 'live' ? 'Live' : 'Offline'}
            </span>
          )}
          {health?.demo_mode && <span className="badge badge--demo">Demo</span>}
          <button
            type="button"
            className="button button--small"
            onClick={() => void reset()}
            disabled={resetting}
            aria-keyshortcuts="Shift+R"
            title="Clear this session and go back to the start (Shift+R)"
          >
            Reset
          </button>
        </div>
      </header>

      <main className="app__main" ref={mainRef} tabIndex={-1}>
        {resetError && <Notice kind="error">Reset failed. {resetError}</Notice>}

        {screen === 'start' && (
          <StartScreen
            scenarios={scenarios}
            loadError={loadError}
            selectedId={scenarioId}
            onSelect={setScenarioId}
            onRun={() => void run()}
            starting={starting}
            startError={startError}
          />
        )}
        {screen === 'run' && originalId && (
          <RunScreen stream={original} scenario={scenario} onNext={() => goTo('diagnose')} />
        )}
        {screen === 'diagnose' && originalRun && (
          <DiagnoseScreen
            run={originalRun}
            diagnoses={diagnoses}
            activeMethod={activeMethod}
            busyMethod={busyMethod}
            error={diagnoseError}
            onDiagnose={(method) => void diagnose(method)}
            onSelect={setActiveMethod}
            onNext={() => goTo('repair')}
          />
        )}
        {screen === 'repair' && originalRun && (
          <RepairScreen
            // A different diagnosis means a different default step.
            key={activeDiagnosis?.diagnosis_id ?? 'none'}
            original={originalRun}
            diagnosis={activeDiagnosis}
            tools={tools}
            repaired={repaired}
            onResumed={setRepairedId}
            onNext={() => goTo('compare')}
          />
        )}
        {screen === 'compare' && originalId && repairedId && (
          <CompareScreen
            originalId={originalId}
            repairedId={repairedId}
            restart={restart}
            onRestarted={setRestartId}
            onNext={() => goTo('results')}
          />
        )}
        {screen === 'results' && <ResultsScreen />}
      </main>
    </div>
  );
}
