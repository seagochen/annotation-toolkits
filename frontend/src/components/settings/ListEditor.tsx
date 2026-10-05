import { useState, type DragEvent, type KeyboardEvent } from "react";

/**
 * An ordered list of names (categories, labels) edited one entry at a time:
 * type a name and confirm it, then delete or drag entries to reorder. Order
 * matters — mask values and COCO category ids follow it — so it is shown as
 * a numbered list rather than as tags.
 *
 * The first `fixed` entries are locked (the project already has annotations
 * that refer to them by position): they cannot be deleted, moved, or have
 * anything moved in front of them.
 */
export function ListEditor({
  id,
  value,
  fixed,
  disabled,
  onChange,
}: {
  id: string;
  value: string[];
  fixed: number;
  disabled: boolean;
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [editError, setEditError] = useState("");

  function confirm() {
    // Pasting "a, b, c" adds all three; a name itself rarely contains a comma.
    const names = draft
      .split(/[,，\n]/)
      .map((name) => name.trim())
      .filter(Boolean);
    if (!names.length) return;
    const duplicates = names.filter((name, index) => value.includes(name) || names.indexOf(name) !== index);
    if (duplicates.length) {
      setError(`“${duplicates[0]}”已存在`);
      return;
    }
    onChange([...value, ...names]);
    setDraft("");
    setError("");
  }

  function startEdit(index: number) {
    setEditing(index);
    setEditText(value[index]);
    setEditError("");
  }

  function cancelEdit() {
    setEditing(null);
    setEditError("");
  }

  function saveEdit() {
    if (editing === null) return;
    const name = editText.trim();
    if (!name) {
      setEditError("名称不能为空");
      return;
    }
    if (value.some((entry, index) => entry === name && index !== editing)) {
      setEditError(`“${name}”已存在`);
      return;
    }
    onChange(value.map((entry, index) => (index === editing ? name : entry)));
    cancelEdit();
  }

  function onEditKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      saveEdit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelEdit();
    }
  }

  function move(from: number, to: number) {
    const target = Math.max(fixed, Math.min(value.length - 1, to));
    if (from < fixed || from === target) return;
    const next = [...value];
    const [entry] = next.splice(from, 1);
    next.splice(target, 0, entry);
    onChange(next);
  }

  function onDraftKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    // While an IME is composing, Enter picks the candidate; it is not a confirm.
    if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      confirm();
    }
  }

  function onHandleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      event.preventDefault();
      const target = index + (event.key === "ArrowUp" ? -1 : 1);
      move(index, target);
      // Keep focus on the moved entry's handle so repeated presses keep moving it.
      requestAnimationFrame(() => {
        const clamped = Math.max(fixed, Math.min(value.length - 1, target));
        document.getElementById(`${id}-handle-${clamped}`)?.focus();
      });
    }
  }

  function onDragOver(event: DragEvent<HTMLLIElement>, index: number) {
    if (dragFrom === null || index < fixed) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    setDropAt(index);
  }

  function onDrop(event: DragEvent<HTMLLIElement>, index: number) {
    event.preventDefault();
    if (dragFrom !== null) move(dragFrom, index);
    setDragFrom(null);
    setDropAt(null);
  }

  return (
    <div className="list-editor">
      {value.length > 0 && (
        <ol aria-label="已添加" className="list-entries">
          {value.map((entry, index) => {
            const locked = disabled || index < fixed;
            const isEditing = editing === index;
            const classes = ["list-entry"];
            if (dragFrom === index) classes.push("dragging");
            if (dropAt === index && dragFrom !== index) classes.push(dragFrom !== null && dragFrom < index ? "drop-after" : "drop-before");
            return (
              <li
                className={classes.join(" ")}
                key={entry}
                onDragLeave={() => setDropAt((current) => (current === index ? null : current))}
                onDragOver={(event) => onDragOver(event, index)}
                onDrop={(event) => onDrop(event, index)}
              >
                <span aria-hidden="true" className="list-entry-index">
                  {index + 1}.
                </span>
                <div className="list-entry-box">
                  {isEditing ? (
                    <input
                      aria-invalid={editError ? true : undefined}
                      aria-label={`修改 ${entry} 的名称`}
                      autoFocus
                      className="list-entry-edit"
                      onChange={(event) => {
                        setEditText(event.target.value);
                        setEditError("");
                      }}
                      onKeyDown={onEditKeyDown}
                      type="text"
                      value={editText}
                    />
                  ) : (
                    <span className="list-entry-name">{entry}</span>
                  )}
                  {locked ? (
                    !disabled && (
                      <span className="list-entry-locked" title="已有标注结果引用了它，不能修改、删除或移动">
                        已锁定
                      </span>
                    )
                  ) : isEditing ? (
                    <span className="list-entry-actions">
                      <button aria-label="保存修改" className="list-icon-button confirm" onClick={saveEdit} title="保存（Enter）" type="button">
                        <svg aria-hidden="true" fill="none" height="16" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 16 16" width="16">
                          <path d="M3.5 8.5l3 3 6-7" />
                        </svg>
                      </button>
                      <button aria-label="取消修改" className="list-icon-button" onClick={cancelEdit} title="取消（Esc）" type="button">
                        <svg aria-hidden="true" fill="none" height="16" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" viewBox="0 0 16 16" width="16">
                          <path d="M4 4l8 8M12 4l-8 8" />
                        </svg>
                      </button>
                    </span>
                  ) : (
                    <span className="list-entry-actions">
                      <button
                        aria-label={`修改 ${entry}`}
                        className="list-icon-button"
                        disabled={editing !== null}
                        onClick={() => startEdit(index)}
                        title="修改"
                        type="button"
                      >
                        <svg aria-hidden="true" fill="none" height="16" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" viewBox="0 0 16 16" width="16">
                          <path d="M3 13l.8-3.2 6.6-6.6 2.4 2.4-6.6 6.6L3 13zM9.2 4.4l2.4 2.4" />
                        </svg>
                      </button>
                      <button
                        aria-label={`删除 ${entry}`}
                        className="list-icon-button danger"
                        disabled={editing !== null}
                        onClick={() => onChange(value.filter((item) => item !== entry))}
                        title="删除"
                        type="button"
                      >
                        <svg aria-hidden="true" fill="none" height="16" stroke="currentColor" strokeLinecap="round" strokeWidth="1.7" viewBox="0 0 16 16" width="16">
                          <path d="M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5" />
                        </svg>
                      </button>
                      <button
                        aria-label={`拖动以调整 ${entry} 的顺序（或按 ↑/↓）`}
                        className="list-icon-button handle"
                        disabled={editing !== null}
                        draggable={editing === null}
                        id={`${id}-handle-${index}`}
                        onDragEnd={() => {
                          setDragFrom(null);
                          setDropAt(null);
                        }}
                        onDragStart={(event) => {
                          event.dataTransfer.effectAllowed = "move";
                          // Firefox will not start a drag without some data.
                          event.dataTransfer.setData("text/plain", entry);
                          setDragFrom(index);
                        }}
                        onKeyDown={(event) => onHandleKeyDown(event, index)}
                        title="拖动调整顺序"
                        type="button"
                      >
                        <svg aria-hidden="true" fill="currentColor" height="16" viewBox="0 0 16 16" width="16">
                          {[4, 8, 12].map((y) => (
                            <g key={y}>
                              <circle cx="6" cy={y} r="1.2" />
                              <circle cx="10" cy={y} r="1.2" />
                            </g>
                          ))}
                        </svg>
                      </button>
                    </span>
                  )}
                </div>
                {isEditing && editError && (
                  <p className="list-error list-entry-error" role="alert">
                    {editError}
                  </p>
                )}
              </li>
            );
          })}
        </ol>
      )}
      {!disabled && (
        <div className="list-add">
          <input
            aria-invalid={error ? true : undefined}
            id={id}
            onChange={(event) => {
              setDraft(event.target.value);
              setError("");
            }}
            onKeyDown={onDraftKeyDown}
            placeholder={value.length ? "继续添加…" : "输入名称"}
            type="text"
            value={draft}
          />
          <button className="button-secondary" disabled={!draft.trim()} onClick={confirm} type="button">
            确认
          </button>
        </div>
      )}
      {error && (
        <p className="list-error" role="alert">
          {error}
        </p>
      )}
      {!error && draft.trim() && <p className="list-pending">尚未添加：按 Enter 或点击“确认”。</p>}
    </div>
  );
}
