import type { JsonObject } from '../api/types';
import { formatJson, plainLabel } from '../lib/format';

interface Props {
  injection: JsonObject[];
}

/** What the fault injector changed at a step, as recorded by the backend. */
export function InjectionDetail({ injection }: Props): JSX.Element {
  // A multi-step fault repeats the same record on each step it touched.
  const first = injection[0];
  if (!first) return <></>;
  const kind = typeof first.kind === 'string' ? plainLabel(first.kind) : 'Fault';
  return (
    <div className="injection">
      <p className="injection__kind">
        {kind}
        {typeof first.argument === 'string' && (
          <>
            {' '}
            on <code>{first.argument}</code>
          </>
        )}
      </p>
      <div className="injection__pair">
        <div>
          <p className="field-label">Without the fault</p>
          <pre className="json">{formatJson(first.original ?? null)}</pre>
        </div>
        <div>
          <p className="field-label">With the fault</p>
          <pre className="json">{formatJson(first.injected ?? null)}</pre>
        </div>
      </div>
    </div>
  );
}
