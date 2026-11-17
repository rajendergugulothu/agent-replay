import type { JsonObject } from '../api/types';

export type FieldKind = 'string' | 'optional_string' | 'string_list';

export interface FormField {
  name: string;
  kind: FieldKind;
  required: boolean;
  description: string;
  minLength: number | null;
  maxLength: number | null;
  pattern: string | null;
  minItems: number | null;
  maxItems: number | null;
}

export type FormValues = Record<string, string>;
export type FormErrors = Record<string, string>;

function asObject(value: unknown): JsonObject | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' ? value : null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

/** Turn a tool's JSON schema into form fields. Covers the three shapes the tools use. */
export function fieldsFromSchema(schema: JsonObject): FormField[] {
  const properties = asObject(schema.properties) ?? {};
  const required = Array.isArray(schema.required) ? schema.required : [];
  return Object.entries(properties).flatMap(([name, raw]): FormField[] => {
    const property = asObject(raw);
    if (!property) return [];
    const base = {
      name,
      required: required.includes(name),
      description: asString(property.description) ?? '',
      minItems: null,
      maxItems: null,
    };
    if (property.type === 'array') {
      const items = asObject(property.items) ?? {};
      return [
        {
          ...base,
          kind: 'string_list',
          minLength: asNumber(items.minLength),
          maxLength: asNumber(items.maxLength),
          pattern: asString(items.pattern),
          minItems: asNumber(property.minItems),
          maxItems: asNumber(property.maxItems),
        },
      ];
    }
    // An optional string is written as anyOf [string, null].
    const variants = Array.isArray(property.anyOf) ? property.anyOf.map(asObject) : [];
    const stringVariant = variants.find((v) => v?.type === 'string');
    const nullable = variants.some((v) => v?.type === 'null');
    const source = stringVariant ?? property;
    return [
      {
        ...base,
        kind: nullable ? 'optional_string' : 'string',
        minLength: asNumber(source.minLength),
        maxLength: asNumber(source.maxLength),
        pattern: asString(source.pattern),
      },
    ];
  });
}

/** Form text for the arguments a step was recorded with. */
export function initialValues(fields: FormField[], args: JsonObject | null): FormValues {
  const values: FormValues = {};
  for (const field of fields) {
    const value = args?.[field.name];
    if (Array.isArray(value)) values[field.name] = value.map(String).join('\n');
    else if (value === null || value === undefined) values[field.name] = '';
    else values[field.name] = String(value);
  }
  return values;
}

export function parseList(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter((item) => item !== '');
}

function isTimePattern(pattern: string): boolean {
  return pattern.includes('[0-5]\\d');
}

function stringError(field: FormField, value: string): string | null {
  if (field.maxLength !== null && value.length > field.maxLength) {
    return `Use at most ${field.maxLength} characters.`;
  }
  if (field.minLength !== null && value.length < field.minLength) {
    return `Use at least ${field.minLength} characters.`;
  }
  if (field.pattern !== null && !new RegExp(field.pattern).test(value)) {
    return isTimePattern(field.pattern)
      ? 'Use a 24-hour time as HH:MM, for example 10:00.'
      : 'This value is not in the expected format.';
  }
  return null;
}

function fieldError(field: FormField, text: string): string | null {
  if (field.kind === 'string_list') {
    const items = parseList(text);
    if (items.length === 0) return field.required ? 'Enter at least one value.' : null;
    if (field.minItems !== null && items.length < field.minItems) {
      return `Enter at least ${field.minItems} values.`;
    }
    if (field.maxItems !== null && items.length > field.maxItems) {
      return `Enter at most ${field.maxItems} values.`;
    }
    for (const item of items) {
      const error = stringError(field, item);
      if (error) return `"${item}": ${error}`;
    }
    return null;
  }
  const value = text.trim();
  if (value === '') return field.required ? 'This field is required.' : null;
  return stringError(field, value);
}

export function validateValues(fields: FormField[], values: FormValues): FormErrors {
  const errors: FormErrors = {};
  for (const field of fields) {
    const error = fieldError(field, values[field.name] ?? '');
    if (error) errors[field.name] = error;
  }
  return errors;
}

/** The arguments object to send. Call only after validateValues returns no errors. */
export function toArguments(fields: FormField[], values: FormValues): JsonObject {
  const args: JsonObject = {};
  for (const field of fields) {
    const text = values[field.name] ?? '';
    if (field.kind === 'string_list') {
      const items = parseList(text);
      if (items.length > 0 || field.required) args[field.name] = items;
    } else if (text.trim() !== '') {
      args[field.name] = text.trim();
    } else if (field.kind === 'optional_string') {
      args[field.name] = null;
    }
  }
  return args;
}
