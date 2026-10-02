import type { FieldSpec, SettingsValues } from "../../api/client";
import { ListEditor } from "./ListEditor";
import "./settings.css";

const COLLAPSED_GROUPS = new Set(["高级"]);

function asList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** Defaults for a fresh form, keyed by dotted config path. */
export function defaultValues(fields: readonly FieldSpec[]): SettingsValues {
  const values: SettingsValues = {};
  for (const field of fields) {
    if (field.default !== null && field.default !== undefined) values[field.key] = field.default;
    else if (field.type === "list") values[field.key] = [];
    else if (field.type === "boolean") values[field.key] = false;
  }
  return values;
}

/** Client-side check for the obvious mistakes; the server stays authoritative. */
export function missingRequired(fields: readonly FieldSpec[], values: SettingsValues): string[] {
  return fields
    .filter((field) => field.required && !field.server_only)
    .filter((field) => {
      const value = values[field.key];
      if (field.type === "list") return asList(value).length === 0;
      return value === null || value === undefined || value === "";
    })
    .map((field) => field.label);
}

function FieldInput({
  field,
  value,
  initial,
  annotated,
  onChange,
}: {
  field: FieldSpec;
  value: unknown;
  initial: unknown;
  annotated: boolean;
  onChange: (value: unknown) => void;
}) {
  const id = `field-${field.key}`;
  const locked = field.server_only || (annotated && field.lock === "locked");

  let control;
  if (field.type === "list") {
    control = (
      <ListEditor
        disabled={locked}
        fixed={annotated && field.lock === "append_only" ? asList(initial).length : 0}
        id={id}
        onChange={onChange}
        value={asList(value)}
      />
    );
  } else if (field.type === "select") {
    control = (
      <select disabled={locked} id={id} onChange={(event) => onChange(event.target.value)} value={String(value ?? "")}>
        {!field.required && <option value="">（未设置）</option>}
        {(field.options ?? []).map((option) => (
          <option key={String(option.value)} value={String(option.value)}>
            {option.label}
          </option>
        ))}
      </select>
    );
  } else if (field.type === "boolean") {
    control = (
      <input
        checked={Boolean(value)}
        disabled={locked}
        id={id}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
    );
  } else if (field.type === "integer" || field.type === "number") {
    control = (
      <input
        disabled={locked}
        id={id}
        inputMode={field.type === "integer" ? "numeric" : "decimal"}
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") return onChange(null);
          const parsed = field.type === "integer" ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
          onChange(Number.isNaN(parsed) ? raw : parsed);
        }}
        step={field.type === "integer" ? 1 : "any"}
        type="number"
        value={value === null || value === undefined ? "" : String(value)}
      />
    );
  } else {
    control = (
      <input
        className={field.type === "path" ? "mono" : undefined}
        disabled={locked}
        id={id}
        onChange={(event) => onChange(event.target.value)}
        placeholder={field.type === "path" ? "/服务器上的绝对路径" : undefined}
        spellCheck={false}
        type="text"
        value={value === null || value === undefined ? "" : String(value)}
      />
    );
  }

  let note = field.help ?? "";
  if (field.server_only) note = "出于安全原因，只能在服务器上的配置文件中修改。";
  else if (annotated && field.lock === "locked") note = "已有标注结果，不能再修改。";
  else if (annotated && field.lock === "append_only") note = "已有标注结果：只能在末尾追加，不能删除或调整顺序。";

  return (
    <div className={field.type === "boolean" ? "form-field form-field-inline" : "form-field"}>
      <label htmlFor={id}>
        {field.label}
        {field.required && !locked && <span className="required" aria-hidden="true"> *</span>}
      </label>
      {control}
      {note && <p className="field-help">{note}</p>}
    </div>
  );
}

export function SettingsForm({
  fields,
  values,
  initial = {},
  annotated = false,
  onChange,
}: {
  fields: readonly FieldSpec[];
  values: SettingsValues;
  /** Saved values; append-only lists keep these entries fixed. */
  initial?: SettingsValues;
  annotated?: boolean;
  onChange: (key: string, value: unknown) => void;
}) {
  const groups = new Map<string, FieldSpec[]>();
  for (const field of fields) {
    const group = field.group ?? "";
    groups.set(group, [...(groups.get(group) ?? []), field]);
  }

  return (
    <div className="settings-form">
      {[...groups].map(([group, groupFields]) => {
        const body = groupFields.map((field) => (
          <FieldInput
            annotated={annotated}
            field={field}
            initial={initial[field.key]}
            key={field.key}
            onChange={(value) => onChange(field.key, value)}
            value={values[field.key]}
          />
        ));
        if (!group) return <div className="form-group" key="">{body}</div>;
        if (COLLAPSED_GROUPS.has(group)) {
          return (
            <details className="form-group form-group-collapsible" key={group}>
              <summary>{group}</summary>
              {body}
            </details>
          );
        }
        return (
          <fieldset className="form-group" key={group}>
            <legend>{group}</legend>
            {body}
          </fieldset>
        );
      })}
    </div>
  );
}
