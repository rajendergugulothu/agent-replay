import { describe, expect, it } from 'vitest';
import type { DiagnosisOut, StepOrigin, StepOut } from '../api/types';
import { markSteps } from './timeline';

function step(stepNumber: number, origin: StepOrigin = 'model'): StepOut {
  return {
    step_number: stepNumber,
    tool_name: 'check_prerequisite',
    tool_args: {},
    tool_output: {},
    text: null,
    summary: `step ${stepNumber}`,
    tokens: 1,
    seconds: 0.1,
    depends_on: [],
    origin,
    injection: null,
  };
}

function diagnosis(predicted: number | null, affected: number[]): DiagnosisOut {
  return {
    diagnosis_id: 'a'.repeat(32),
    method: 'llm',
    predicted_step: predicted,
    failure_type: null,
    reason: '',
    evidence: [],
    tokens: 0,
    seconds: 0,
    used_llm: true,
    affected_steps: affected,
    injected_step: null,
  };
}

const steps = [1, 2, 3, 4, 5].map((n) => step(n));

describe('markSteps', () => {
  it('leaves every step unmarked before any diagnosis', () => {
    expect(markSteps(steps, null).map((m) => m.tone)).toEqual(['none', 'none', 'none', 'none', 'none']);
  });

  it('marks the predicted step, the affected steps, and the earlier steps', () => {
    const marks = markSteps(steps, diagnosis(3, [4, 5]));
    expect(marks.map((m) => m.tone)).toEqual(['clear', 'clear', 'predicted', 'affected', 'affected']);
  });

  it('gives every marked step a text label', () => {
    const marks = markSteps(steps, diagnosis(3, [4, 5]));
    expect(marks.map((m) => m.label)).toEqual([
      'Before the fault',
      'Before the fault',
      'Predicted fault',
      'Affected',
      'Affected',
    ]);
  });

  it('marks nothing when the localizer found no step', () => {
    const marks = markSteps(steps, diagnosis(null, []));
    expect(marks.every((m) => m.tone === 'none' && m.label === '')).toBe(true);
  });

  it('only marks later steps as affected when the diagnosis lists them', () => {
    const marks = markSteps(steps, diagnosis(2, [3]));
    expect(marks.map((m) => m.tone)).toEqual(['clear', 'predicted', 'affected', 'none', 'none']);
  });

  it('flags reused steps whatever their tone', () => {
    const mixed = [step(1, 'reused'), step(2, 'reused'), step(3, 'forced'), step(4)];
    expect(markSteps(mixed, null).map((m) => m.reused)).toEqual([true, true, false, false]);
    const marks = markSteps(mixed, diagnosis(3, [4]));
    expect(marks[0]).toMatchObject({ tone: 'clear', reused: true });
    expect(marks[2]).toMatchObject({ tone: 'predicted', reused: false });
  });
});
