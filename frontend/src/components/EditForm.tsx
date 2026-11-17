import { useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import type { JsonObject, ToolOut } from '../api/types';
import {
  fieldsFromSchema,
  initialValues,
  toArguments,
  validateValues,
} from '../lib/schemaForm';
import type { FormErrors, FormField } from '../lib/schemaForm';

interface Props {
  tool: ToolOut;
  /** The arguments the step was recorded with. Remount (change `key`) to load others. */
  initialArgs: JsonObject | null;
  submitLabel: string;
  busy: boolean;
  /** The server's own complaint about the arguments (HTTP 422), if any. */
  serverError: string | null;
  onSubmit: (args: JsonObject) => void;
}

function hintFor(field: FormField): string {
  const parts: string[] = [];
  if (field.description) parts.push(field.description);
  if (field.kind === 'string_list') parts.push('One per line.');
  if (field.kind === 'optional_string') parts.push('Optional.');
  if (field.maxLength !== null) parts.push(`At most ${field.maxLength} characters.`);
  return parts.join(' ');
}

export function EditForm({ tool, initialArgs, submitLabel, busy, serverError, onSubmit }: Props): JSX.Element {
  const fields = useMemo(() => fieldsFromSchema(tool.parameters), [tool]);
  const [values, setValues] = useState(() => initialValues(fields, initialArgs));
  const [errors, setErrors] = useState<FormErrors>({});

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    const found = validateValues(fields, values);
    setErrors(found);
    if (Object.keys(found).length === 0) onSubmit(toArguments(fields, values));
  };

  return (
    <form className="edit-form" onSubmit={submit} noValidate>
      {fields.map((field) => {
        const id = `arg-${field.name}`;
        const error = errors[field.name];
        const hint = hintFor(field);
        const describedBy = [hint ? `${id}-hint` : '', error ? `${id}-error` : ''].join(' ').trim();
        const shared = {
          id,
          name: field.name,
          value: values[field.name] ?? '',
          'aria-invalid': error ? true : undefined,
          'aria-describedby': describedBy || undefined,
          'aria-required': field.required,
        };
        return (
          <div key={field.name} className="edit-form__field">
            <label htmlFor={id}>
              <code>{field.name}</code>
              {field.required && <span className="edit-form__required"> (required)</span>}
            </label>
            {field.kind === 'string_list' ? (
              <textarea
                {...shared}
                rows={4}
                spellCheck={false}
                onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
              />
            ) : (
              <input
                {...shared}
                type="text"
                autoComplete="off"
                spellCheck={false}
                onChange={(e) => setValues({ ...values, [field.name]: e.target.value })}
              />
            )}
            {hint && (
              <p id={`${id}-hint`} className="edit-form__hint">
                {hint}
              </p>
            )}
            {error && (
              <p id={`${id}-error`} className="edit-form__error">
                {error}
              </p>
            )}
          </div>
        );
      })}
      {serverError && (
        <p className="edit-form__error" role="alert">
          The server rejected these arguments: {serverError}
        </p>
      )}
      <button type="submit" className="button button--primary" disabled={busy}>
        {submitLabel}
      </button>
    </form>
  );
}
