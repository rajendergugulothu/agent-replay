import { useState } from 'react';
import type { StepOut, ValidationOut } from '../api/types';
import { formatJson, formatSeconds, formatTokens } from '../lib/format';
import type { StepMark } from '../lib/timeline';
import { InjectionDetail } from './InjectionDetail';
import { StatusMark } from './StatusMark';

interface Props {
  label: string;
  steps: StepOut[];
  marks: StepMark[];
  /** The validator's verdict, shown under the last step once the run has ended. */
  validation: ValidationOut | null;
  live: boolean;
}

export function Timeline({ label, steps, marks, validation, live }: Props): JSX.Element {
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set());

  const toggle = (stepNumber: number): void => {
    const next = new Set(open);
    if (!next.delete(stepNumber)) next.add(stepNumber);
    setOpen(next);
  };

  return (
    <div className={live ? 'timeline timeline--live' : 'timeline'}>
      <ol className="timeline__list" aria-label={label} aria-live="polite" aria-relevant="additions">
        {steps.map((step, index) => {
          const mark = marks[index];
          const tone = mark?.tone ?? 'none';
          const isOpen = open.has(step.step_number);
          const isLast = index === steps.length - 1;
          return (
            <li
              key={step.step_number}
              className={`step step--${tone}${mark?.reused ? ' step--reused' : ''}`}
              data-step={step.step_number}
              data-tone={tone}
            >
              <button
                type="button"
                className="step__row"
                aria-expanded={isOpen}
                onClick={() => toggle(step.step_number)}
              >
                <span className="step__num">
                  <span className="visually-hidden">Step </span>
                  {step.step_number}
                </span>
                <span className="step__main">
                  <span className="step__tool">{step.tool_name ?? 'final answer'}</span>
                  <span className="step__summary">{step.summary}</span>
                </span>
                <span className="step__tags">
                  {tone !== 'none' && mark && (
                    <span className={`tag tag--${tone}`}>
                      <StatusMark kind={tone} size={16} />
                      {mark.label}
                    </span>
                  )}
                  {mark?.reused && (
                    <span className="tag tag--reused">
                      <StatusMark kind="reused" size={16} />
                      Reused, not re-run
                    </span>
                  )}
                  {step.origin === 'forced' && (
                    <span className="tag tag--forced">
                      <StatusMark kind="forced" size={16} />
                      Your fix
                    </span>
                  )}
                  {step.injection && (
                    <span className="tag tag--planted">
                      <StatusMark kind="planted" size={14} />
                      Fault planted
                    </span>
                  )}
                </span>
                <span className={`chevron${isOpen ? ' chevron--open' : ''}`} aria-hidden="true" />
              </button>
              {isOpen && (
                <div className="step__detail">
                  {step.text && <p className="step__text">{step.text}</p>}
                  {step.tool_name !== null && (
                    <div className="step__io">
                      <div>
                        <p className="field-label">Arguments</p>
                        <pre className="json">{formatJson(step.tool_args)}</pre>
                      </div>
                      <div>
                        <p className="field-label">Output</p>
                        <pre className="json">{formatJson(step.tool_output)}</pre>
                      </div>
                    </div>
                  )}
                  {step.injection && (
                    <>
                      <p className="field-label">What was planted at this step</p>
                      <InjectionDetail injection={step.injection} />
                    </>
                  )}
                  <p className="step__meta">
                    {step.origin === 'reused'
                      ? 'Copied from the original run. No model call was made.'
                      : `${formatTokens(step.tokens)} tokens, ${formatSeconds(step.seconds)}`}
                  </p>
                </div>
              )}
              {isLast && !live && validation && !validation.passed && (
                <div className="verdict verdict--failed">
                  <p className="verdict__title">
                    <StatusMark kind="predicted" /> The validator rejected the result
                  </p>
                  <ul>
                    {validation.reasons.map((reason, i) => (
                      <li key={i}>{reason.message}</li>
                    ))}
                  </ul>
                </div>
              )}
              {isLast && !live && validation?.passed && (
                <div className="verdict verdict--passed">
                  <p className="verdict__title">
                    <StatusMark kind="clear" /> The validator accepted the schedule
                  </p>
                </div>
              )}
            </li>
          );
        })}
      </ol>
      {live && (
        <p className="timeline__live" role="status">
          <span className="spinner" aria-hidden="true" />
          {steps.length === 0 ? 'Waiting for the first step' : `Agent is working, ${steps.length} steps so far`}
        </p>
      )}
    </div>
  );
}
