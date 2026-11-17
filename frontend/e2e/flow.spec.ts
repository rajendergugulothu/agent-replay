import { expect, test } from '@playwright/test';

// The backend behind this test uses a deterministic fake model: scenario_01
// fails on an unmet prerequisite, the LLM judge blames step 3, and forcing
// course_code "QCS 340" at step 3 makes the continuation pass.
test('run, diagnose, repair, compare, results', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Live', { exact: true })).toBeVisible();

  await page.locator('[data-scenario="scenario_01"]').click();
  await page.getByRole('button', { name: 'Run the agent' }).click();

  const outcome = page.getByRole('complementary', { name: 'Run result' });
  await expect(outcome.getByText('Failed', { exact: true })).toBeVisible();
  await expect(outcome).toContainText('QCS 340 needs QCS 240');
  const timeline = page.getByRole('list', { name: 'Steps of the run' });
  await expect(timeline.locator('[data-step="3"]')).toContainText('Checked prerequisite for QCS 310');
  // Nothing about the planted fault may show before a diagnosis.
  await expect(page.getByText('Fault planted')).toHaveCount(0);

  await page.getByRole('button', { name: 'Find the faulty step' }).click();
  await page.getByRole('button', { name: /LLM judge/ }).click();
  await expect(page.getByText('Step 3 is the fault')).toBeVisible();
  const marked = page.getByRole('list', { name: /marked by the diagnosis/ });
  await expect(marked.locator('[data-step="3"]')).toHaveAttribute('data-tone', 'predicted');
  await expect(marked.locator('[data-step="3"]')).toContainText('Predicted fault');
  await expect(marked.locator('[data-step="2"]')).toHaveAttribute('data-tone', 'clear');
  await expect(marked.locator('[data-step="4"]')).toHaveAttribute('data-tone', 'affected');
  await expect(page.getByText('The fault was planted at step 3.')).toBeVisible();

  await page.getByRole('button', { name: 'Repair step 3' }).click();
  await expect(page.getByLabel('Step to repair')).toHaveValue('3');
  await page.getByLabel('course_code').fill('QCS 340');
  await page.getByRole('button', { name: 'Resume from step 3 with this fix' }).click();

  const repaired = page.getByRole('region', { name: 'Repaired run' });
  await expect(repaired.getByText('Passed', { exact: true })).toBeVisible();
  await expect(repaired.locator('[data-step="1"]')).toContainText('Reused, not re-run');
  await expect(repaired.locator('[data-step="3"]')).toContainText('Your fix');
  await expect(repaired.locator('[data-step="3"]')).toContainText('QCS 340');

  await page.getByRole('button', { name: 'Compare the cost' }).click();
  await expect(page.getByRole('columnheader', { name: /Repaired run/ })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: /Full restart/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Also run a full restart' }).click();
  await expect(page.getByRole('columnheader', { name: /Full restart/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'What did the repair cost?' }).locator('..')).toContainText(
    /cheaper|cost the same/,
  );

  await page.getByRole('button', { name: 'See the experiment results' }).click();
  await expect(page.getByRole('heading', { name: 'Experiment results' })).toBeVisible();
  await expect(page.getByRole('img', { name: /Exact-step accuracy per localizer/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Missed cases/ })).toBeVisible();
});

test('reset returns to the start screen', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Run the agent' }).click();
  await expect(page.getByRole('button', { name: 'Find the faulty step' })).toBeVisible();
  await page.keyboard.press('Shift+R');
  await expect(page.getByRole('heading', { name: 'Watch an AI agent fail, then fix it' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run', exact: true })).toBeDisabled();
});
