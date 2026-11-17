import { formatTokens } from '../lib/format';

export interface CostRow {
  label: string;
  localization: number;
  repair: number;
  replay: number;
  total: number;
  /** Mean tokens of a full restart on the same cases, drawn as the reference mark. */
  reference: number;
}

interface Props {
  title: string;
  rows: CostRow[];
}

const WIDTH = 1100;
const LABEL = 300;
const RIGHT = 130;
const ROW = 54;
const BAR = 26;
const TOP = 8;
const PARTS = ['localization', 'repair', 'replay'] as const;
const PART_LABELS: Record<(typeof PARTS)[number], string> = {
  localization: 'Localization',
  repair: 'Repair',
  replay: 'Replay',
};

/** One horizontal stacked bar of mean tokens per strategy. */
export function StackedCost({ title, rows }: Props): JSX.Element {
  const max = Math.max(...rows.flatMap((r) => [r.total, r.reference]), 1);
  const plot = WIDTH - LABEL - RIGHT;
  const scale = (tokens: number): number => (tokens / max) * plot;
  const height = TOP * 2 + rows.length * ROW;

  return (
    <figure className="chart">
      <ul className="legend" aria-hidden="true">
        {PARTS.map((part) => (
          <li key={part}>
            <span className={`swatch fill-${part}`} /> {PART_LABELS[part]}
          </li>
        ))}
        <li>
          <span className="swatch swatch--reference" /> Mean tokens of a full restart
        </li>
      </ul>
      <svg viewBox={`0 0 ${WIDTH} ${height}`} role="img" aria-label={title}>
        {rows.map((row, i) => {
          const top = TOP + i * ROW;
          let x = LABEL;
          return (
            <g key={row.label}>
              <text x={LABEL - 14} y={top + ROW / 2 + 5} textAnchor="end" className="chart__label">
                {row.label}
              </text>
              {PARTS.map((part) => {
                const width = scale(row[part]);
                const rect = (
                  <rect
                    key={part}
                    x={x}
                    y={top + (ROW - BAR) / 2}
                    // The 2px cut leaves a surface gap between touching segments.
                    width={Math.max(width - 2, 0)}
                    height={BAR}
                    rx="3"
                    className={`fill-${part}`}
                  >
                    <title>{`${row.label}, ${PART_LABELS[part]}: ${formatTokens(row[part])} tokens`}</title>
                  </rect>
                );
                x += width;
                return rect;
              })}
              <text x={LABEL + scale(Math.max(row.total, row.reference)) + 10} y={top + ROW / 2 + 5} className="chart__value">
                {formatTokens(row.total)}
              </text>
              <line
                x1={LABEL + scale(row.reference)}
                x2={LABEL + scale(row.reference)}
                y1={top + 2}
                y2={top + ROW - 2}
                className="chart__reference"
              >
                <title>{`Mean full restart: ${formatTokens(row.reference)} tokens`}</title>
              </line>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
