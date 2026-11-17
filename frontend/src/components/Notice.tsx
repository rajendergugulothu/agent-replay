import type { ReactNode } from 'react';

interface Props {
  kind: 'error' | 'info' | 'loading' | 'success';
  children: ReactNode;
}

/** One place for loading, empty, and error messages so they look and announce alike. */
export function Notice({ kind, children }: Props): JSX.Element {
  return (
    <div className={`notice notice--${kind}`} role={kind === 'error' ? 'alert' : 'status'}>
      {kind === 'loading' && <span className="spinner" aria-hidden="true" />}
      <div>{children}</div>
    </div>
  );
}
