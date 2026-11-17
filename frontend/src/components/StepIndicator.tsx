export type Screen = 'start' | 'run' | 'diagnose' | 'repair' | 'compare' | 'results';

export const SCREENS: { id: Screen; label: string }[] = [
  { id: 'start', label: 'Start' },
  { id: 'run', label: 'Run' },
  { id: 'diagnose', label: 'Diagnose' },
  { id: 'repair', label: 'Repair' },
  { id: 'compare', label: 'Compare' },
  { id: 'results', label: 'Results' },
];

interface Props {
  current: Screen;
  enabled: Record<Screen, boolean>;
  onSelect: (screen: Screen) => void;
}

export function StepIndicator({ current, enabled, onSelect }: Props): JSX.Element {
  return (
    <nav aria-label="Progress">
      <ol className="stepper">
        {SCREENS.map((screen, index) => (
          <li key={screen.id} className="stepper__item">
            <button
              type="button"
              className={`stepper__button${screen.id === current ? ' stepper__button--current' : ''}`}
              aria-current={screen.id === current ? 'step' : undefined}
              disabled={!enabled[screen.id]}
              onClick={() => onSelect(screen.id)}
            >
              <span className="stepper__num" aria-hidden="true">
                {index + 1}
              </span>
              {screen.label}
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}
