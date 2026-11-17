import type { DiagnosisOut, StepOut } from '../api/types';

export type StepTone = 'none' | 'clear' | 'predicted' | 'affected';

export interface StepMark {
  stepNumber: number;
  tone: StepTone;
  /** Text shown next to the shape, so color is never the only signal. */
  label: string;
  reused: boolean;
}

const LABELS: Record<StepTone, string> = {
  none: '',
  clear: 'Before the fault',
  predicted: 'Predicted fault',
  affected: 'Affected',
};

function toneFor(stepNumber: number, diagnosis: DiagnosisOut | null): StepTone {
  if (!diagnosis || diagnosis.predicted_step === null) return 'none';
  if (stepNumber === diagnosis.predicted_step) return 'predicted';
  if (diagnosis.affected_steps.includes(stepNumber)) return 'affected';
  if (stepNumber < diagnosis.predicted_step) return 'clear';
  return 'none';
}

/** How each timeline row is marked for a diagnosis (or for none). */
export function markSteps(steps: StepOut[], diagnosis: DiagnosisOut | null): StepMark[] {
  return steps.map((step) => {
    const tone = toneFor(step.step_number, diagnosis);
    return {
      stepNumber: step.step_number,
      tone,
      label: LABELS[tone],
      reused: step.origin === 'reused',
    };
  });
}
