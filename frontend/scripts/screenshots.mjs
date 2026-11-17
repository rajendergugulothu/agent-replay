// Walks the six screens of the built app and saves one screenshot of each.
//   node scripts/screenshots.mjs [baseURL] [outDir]
import { chromium } from '@playwright/test';

const baseURL = process.argv[2] ?? 'http://127.0.0.1:8010';
const outDir = process.argv[3] ?? '../docs/screenshots';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1366, height: 768 }, deviceScaleFactor: 2 });
const shot = (name) => page.screenshot({ path: `${outDir}/${name}.png` });

await page.goto(baseURL);
await page.locator('[data-scenario="scenario_01"]').click();
await page.getByRole('button', { name: 'Run the agent' }).waitFor();
await shot('1-start');

await page.getByRole('button', { name: 'Run the agent' }).click();
await page.getByRole('button', { name: 'Find the faulty step' }).waitFor();
await shot('2-timeline');

await page.getByRole('button', { name: 'Find the faulty step' }).click();
await page.getByRole('button', { name: /LLM judge/ }).click();
await page.getByRole('button', { name: 'Repair step 3' }).waitFor();
await shot('3-diagnose');

await page.getByRole('button', { name: 'Repair step 3' }).click();
await page.getByLabel('course_code').fill('QCS 340');
await shot('4-repair');

await page.getByRole('button', { name: 'Resume from step 3 with this fix' }).click();
await page.getByRole('button', { name: 'Compare the cost' }).click();
await page.getByRole('button', { name: 'Also run a full restart' }).click();
await page.getByRole('columnheader', { name: /Full restart/ }).waitFor();
await shot('5-compare');

await page.getByRole('button', { name: 'See the experiment results' }).click();
await page.getByRole('heading', { name: 'Experiment results' }).waitFor();
await shot('6-results');
await page.screenshot({ path: `${outDir}/6-results-full.png`, fullPage: true });

await browser.close();
