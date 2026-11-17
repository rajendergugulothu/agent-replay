import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ToolOut } from '../api/types';
import { fieldsFromSchema, toArguments, validateValues } from '../lib/schemaForm';
import { EditForm } from './EditForm';

// The schemas the backend serves at GET /api/tools.
const searchSections: ToolOut = {
  name: 'search_sections',
  description: 'List the sections of one course with days and times.',
  parameters: {
    additionalProperties: false,
    properties: {
      course_code: { maxLength: 24, minLength: 1, type: 'string' },
      earliest_start: {
        anyOf: [{ pattern: '^([01]\\d|2[0-3]):[0-5]\\d$', type: 'string' }, { type: 'null' }],
        default: null,
        description: '24-hour "HH:MM". Only sections starting at or after it.',
      },
    },
    required: ['course_code'],
    type: 'object',
  },
};

const buildSchedule: ToolOut = {
  name: 'build_schedule',
  description: 'Build a schedule from section ids.',
  parameters: {
    additionalProperties: false,
    properties: {
      student_id: { maxLength: 24, minLength: 1, type: 'string' },
      section_ids: {
        items: { maxLength: 24, minLength: 1, type: 'string' },
        maxItems: 10,
        minItems: 1,
        type: 'array',
      },
    },
    required: ['student_id', 'section_ids'],
    type: 'object',
  },
};

describe('schema form helpers', () => {
  it('reads the three field kinds from the schemas', () => {
    expect(fieldsFromSchema(searchSections.parameters).map((f) => [f.name, f.kind, f.required])).toEqual([
      ['course_code', 'string', true],
      ['earliest_start', 'optional_string', false],
    ]);
    expect(fieldsFromSchema(buildSchedule.parameters)[1]).toMatchObject({
      name: 'section_ids',
      kind: 'string_list',
      required: true,
      maxItems: 10,
      maxLength: 24,
    });
  });

  it('requires required fields and accepts an empty optional one', () => {
    const fields = fieldsFromSchema(searchSections.parameters);
    expect(validateValues(fields, { course_code: '  ', earliest_start: '' })).toEqual({
      course_code: 'This field is required.',
    });
    expect(toArguments(fields, { course_code: 'QCS 340', earliest_start: '' })).toEqual({
      course_code: 'QCS 340',
      earliest_start: null,
    });
  });

  it('enforces the maximum length from the schema', () => {
    const fields = fieldsFromSchema(searchSections.parameters);
    expect(validateValues(fields, { course_code: 'x'.repeat(24), earliest_start: '' })).toEqual({});
    expect(validateValues(fields, { course_code: 'x'.repeat(25), earliest_start: '' }).course_code).toBe(
      'Use at most 24 characters.',
    );
  });

  it('enforces the HH:MM time pattern', () => {
    const fields = fieldsFromSchema(searchSections.parameters);
    for (const bad of ['10', '10am', '24:00', '9:00', '10:60']) {
      expect(validateValues(fields, { course_code: 'QCS 340', earliest_start: bad }).earliest_start).toMatch(/HH:MM/);
    }
    for (const good of ['10:00', '00:00', '23:59']) {
      expect(validateValues(fields, { course_code: 'QCS 340', earliest_start: good })).toEqual({});
    }
  });

  it('validates and parses list fields', () => {
    const fields = fieldsFromSchema(buildSchedule.parameters);
    expect(validateValues(fields, { student_id: 'stu_01', section_ids: ' \n ' }).section_ids).toBe(
      'Enter at least one value.',
    );
    const eleven = Array.from({ length: 11 }, (_, i) => `S-${i}`).join('\n');
    expect(validateValues(fields, { student_id: 'stu_01', section_ids: eleven }).section_ids).toBe(
      'Enter at most 10 values.',
    );
    expect(validateValues(fields, { student_id: 'stu_01', section_ids: `ok\n${'x'.repeat(25)}` }).section_ids).toMatch(
      /at most 24 characters/,
    );
    expect(toArguments(fields, { student_id: 'stu_01', section_ids: 'QCS240-01\n QCS340-01, QCS230-02\n' })).toEqual({
      student_id: 'stu_01',
      section_ids: ['QCS240-01', 'QCS340-01', 'QCS230-02'],
    });
  });
});

describe('EditForm', () => {
  const baseProps = { submitLabel: 'Resume', busy: false, serverError: null };

  it('fills the fields from the recorded arguments and submits edited ones', async () => {
    const onSubmit = vi.fn();
    render(
      <EditForm
        {...baseProps}
        tool={searchSections}
        initialArgs={{ course_code: 'QCS 310', earliest_start: '10:00' }}
        onSubmit={onSubmit}
      />,
    );
    const course = screen.getByLabelText(/course_code/);
    expect(course).toHaveValue('QCS 310');
    await userEvent.clear(course);
    await userEvent.type(course, 'QCS 340');
    await userEvent.click(screen.getByRole('button', { name: 'Resume' }));
    expect(onSubmit).toHaveBeenCalledWith({ course_code: 'QCS 340', earliest_start: '10:00' });
  });

  it('blocks submit and shows each error next to its field', async () => {
    const onSubmit = vi.fn();
    render(
      <EditForm
        {...baseProps}
        tool={searchSections}
        initialArgs={{ course_code: '', earliest_start: '10am' }}
        onSubmit={onSubmit}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Resume' }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/course_code/)).toBeInvalid();
    expect(screen.getByLabelText(/course_code/)).toHaveAccessibleDescription(/This field is required/);
    expect(screen.getByLabelText(/earliest_start/)).toHaveAccessibleDescription(/Use a 24-hour time as HH:MM/);
  });

  it('renders a list field as one value per line', async () => {
    const onSubmit = vi.fn();
    render(
      <EditForm
        {...baseProps}
        tool={buildSchedule}
        initialArgs={{ student_id: 'stu_01', section_ids: ['QCS240-01', 'QCS340-01'] }}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByLabelText(/section_ids/)).toHaveValue('QCS240-01\nQCS340-01');
    await userEvent.click(screen.getByRole('button', { name: 'Resume' }));
    expect(onSubmit).toHaveBeenCalledWith({ student_id: 'stu_01', section_ids: ['QCS240-01', 'QCS340-01'] });
  });

  it('shows the message from a server 422', () => {
    render(
      <EditForm
        {...baseProps}
        tool={searchSections}
        initialArgs={{ course_code: 'QCS 340' }}
        serverError="course_code: String should have at most 24 characters"
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The server rejected these arguments: course_code: String should have at most 24 characters',
    );
  });

  it('disables submit while a run is in progress', () => {
    render(<EditForm {...baseProps} busy tool={searchSections} initialArgs={null} onSubmit={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Resume' })).toBeDisabled();
  });
});
