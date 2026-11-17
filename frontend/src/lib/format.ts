import type { Ratio } from '../api/types';

const whole = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

export function formatTokens(n: number): string {
  return whole.format(n);
}

export function formatSeconds(seconds: number): string {
  // The fake model answers in microseconds; real runs take seconds.
  if (seconds < 0.1) return `${(seconds * 1000).toFixed(1)} ms`;
  return `${seconds.toFixed(1)} s`;
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export function formatCount(numerator: number, denominator: number): string {
  return `${numerator} of ${denominator}`;
}

export function formatRatio(ratio: Ratio): string {
  return `${formatPercent(ratio.value)} (${formatCount(ratio.numerator, ratio.denominator)})`;
}

export function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

/** "wrong_argument_values" -> "Wrong argument values" */
export function plainLabel(code: string): string {
  const words = code.replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
