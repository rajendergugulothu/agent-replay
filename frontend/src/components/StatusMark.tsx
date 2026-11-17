export type MarkKind = 'clear' | 'predicted' | 'affected' | 'reused' | 'planted' | 'forced';

interface Props {
  kind: MarkKind;
  size?: number;
}

// Each state has its own shape, so the states stay distinct without color.
const SHAPES: Record<MarkKind, JSX.Element> = {
  clear: (
    <>
      <circle cx="10" cy="10" r="9" fill="currentColor" />
      <path d="M5.5 10.5l3 3 6-7" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  predicted: (
    <>
      <rect x="1" y="1" width="18" height="18" rx="3" fill="currentColor" />
      <path d="M6 6l8 8M14 6l-8 8" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" />
    </>
  ),
  affected: (
    <>
      <path d="M10 1.5l9 16.5H1z" fill="currentColor" stroke="currentColor" strokeWidth="1" strokeLinejoin="round" />
      <path d="M10 7.5v5" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="10" cy="15.2" r="1.2" fill="#fff" />
    </>
  ),
  reused: (
    <>
      <path d="M16.5 10a6.5 6.5 0 1 1-2.2-4.9" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M15.5 1.5v4.5H11" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </>
  ),
  planted: <path d="M10 1l9 9-9 9-9-9z" fill="currentColor" />,
  forced: (
    <path d="M3 17l1-4L14 3l3 3L7 16z" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" />
  ),
};

export function StatusMark({ kind, size = 18 }: Props): JSX.Element {
  return (
    <svg className={`mark mark--${kind}`} width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      {SHAPES[kind]}
    </svg>
  );
}
