export interface Bar {
  series: number;
  /** Share from 0 to 1. */
  value: number;
  percent: string;
  count: string;
}

export interface BarGroup {
  label: string;
  bars: Bar[];
}

interface Props {
  title: string;
  series: string[];
  groups: BarGroup[];
}

const WIDTH = 1100;
const HEIGHT = 360;
const TOP = 56;
const BOTTOM = 56;
const LEFT = 56;
const BAR = 64;
const GAP = 2;
const GRID = [0, 0.25, 0.5, 0.75, 1];

/** Vertical bars on a shared 0 to 100% axis, grouped, with the value written on every bar. */
export function GroupedBars({ title, series, groups }: Props): JSX.Element {
  const plotHeight = HEIGHT - TOP - BOTTOM;
  const slot = (WIDTH - LEFT) / Math.max(groups.length, 1);
  const y = (value: number): number => TOP + plotHeight * (1 - value);

  return (
    <figure className="chart">
      {/* One series needs no legend: the chart's heading names it. */}
      {series.length > 1 && (
        <ul className="legend" aria-hidden="true">
          {series.map((name, i) => (
            <li key={name}>
              <span className={`swatch fill-series-${i}`} /> {name}
            </li>
          ))}
        </ul>
      )}
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={title}>
        {GRID.map((tick) => (
          <g key={tick}>
            <line x1={LEFT} x2={WIDTH} y1={y(tick)} y2={y(tick)} className={tick === 0 ? 'chart__axis' : 'chart__grid'} />
            <text x={LEFT - 10} y={y(tick) + 5} textAnchor="end" className="chart__tick">
              {tick * 100}%
            </text>
          </g>
        ))}
        {groups.map((group, g) => {
          const width = group.bars.length * BAR + (group.bars.length - 1) * GAP;
          const start = LEFT + slot * g + (slot - width) / 2;
          return (
            <g key={group.label}>
              {group.bars.map((bar, b) => {
                const x = start + b * (BAR + GAP);
                const top = y(bar.value);
                return (
                  <g key={bar.series}>
                    <title>{`${[group.label, series[bar.series]].filter(Boolean).join(', ')}: ${bar.percent} (${bar.count})`}</title>
                    {/* A sliver keeps a 0% bar visible as a bar and not as a gap. */}
                    <rect
                      x={x}
                      y={Math.min(top, y(0) - 2)}
                      width={BAR}
                      height={Math.max(y(0) - top, 2)}
                      rx="3"
                      className={`fill-series-${bar.series}`}
                    />
                    <text x={x + BAR / 2} y={top - 24} textAnchor="middle" className="chart__value">
                      {bar.percent}
                    </text>
                    <text x={x + BAR / 2} y={top - 7} textAnchor="middle" className="chart__count">
                      {bar.count}
                    </text>
                  </g>
                );
              })}
              <text x={LEFT + slot * g + slot / 2} y={HEIGHT - BOTTOM + 28} textAnchor="middle" className="chart__label">
                {group.label}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
