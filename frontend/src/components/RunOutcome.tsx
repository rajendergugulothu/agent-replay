import type { RunOut } from '../api/types';
import { formatSeconds, formatTokens } from '../lib/format';
import { runErrorMessage } from '../lib/messages';
import { StatusMark } from './StatusMark';

interface Props {
  run: RunOut;
}

/** The headline for a finished run: passed, failed, or stopped by an error. */
export function RunOutcome({ run }: Props): JSX.Element {
  const error = runErrorMessage(run);
  const cost = `${run.steps.length} steps, ${formatTokens(run.tokens)} tokens, ${formatSeconds(run.seconds)}`;
  if (error) {
    return (
      <div className="outcome outcome--error" role="alert">
        <p className="outcome__title">
          <StatusMark kind="affected" size={24} /> Run stopped
        </p>
        <p>{error}</p>
      </div>
    );
  }
  if (run.status === 'passed') {
    return (
      <div className="outcome outcome--passed" role="status">
        <p className="outcome__title">
          <StatusMark kind="clear" size={24} /> Passed
        </p>
        <p>The schedule meets every rule. {cost}.</p>
      </div>
    );
  }
  return (
    <div className="outcome outcome--failed" role="status">
      <p className="outcome__title">
        <StatusMark kind="predicted" size={24} /> Failed
      </p>
      {run.validation && run.validation.reasons.length > 0 && (
        <ul>
          {run.validation.reasons.map((reason, i) => (
            <li key={i}>{reason.message}</li>
          ))}
        </ul>
      )}
      <p className="muted">{cost}.</p>
    </div>
  );
}
