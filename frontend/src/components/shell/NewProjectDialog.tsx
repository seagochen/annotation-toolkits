import { useEffect, useRef, useState } from "react";
import { useLocation } from "wouter";

import { listTaskTypes, type TaskTypeInfo } from "../../api/client";

/**
 * Step one of creating a project: pick the task type. Confirming moves on to
 * the property page (/new/<type>), which owns everything else.
 */
export function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const [, navigate] = useLocation();
  const [types, setTypes] = useState<TaskTypeInfo[] | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    let active = true;
    listTaskTypes().then(
      (loaded) => {
        if (!active) return;
        setTypes(loaded);
        setSelected((current) => current || loaded[0]?.type || "");
      },
      (caught: unknown) => active && setError(caught instanceof Error ? caught.message : "未知错误"),
    );
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
    };
    window.addEventListener("keydown", onKeyDown);
    panelRef.current?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  function confirm() {
    if (!selected) return;
    onClose();
    navigate(`/new/${selected}`);
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div aria-labelledby="new-project-title" aria-modal="true" className="modal" ref={panelRef} role="dialog" tabIndex={-1}>
        <header className="modal-header">
          <h2 id="new-project-title">新建项目</h2>
          <p>选择这个项目要做的标注任务，下一步填写项目属性。</p>
        </header>
        {error && <p className="inline-error" role="alert">{error}</p>}
        {!types && !error && <p className="modal-loading">正在读取任务类型…</p>}
        {types && (
          <div aria-label="标注任务类型" className="type-menu" role="radiogroup">
            {types.map((type) => (
              <label className={type.type === selected ? "type-option selected" : "type-option"} key={type.type}>
                <input
                  checked={type.type === selected}
                  name="task-type"
                  onChange={() => setSelected(type.type)}
                  onDoubleClick={confirm}
                  type="radio"
                  value={type.type}
                />
                <span className="type-option-text">
                  <strong>{type.label}</strong>
                  <span>{type.description}</span>
                </span>
              </label>
            ))}
          </div>
        )}
        <footer className="modal-footer">
          <button className="button-secondary" onClick={onClose} type="button">取消</button>
          <button className="button-primary" disabled={!selected} onClick={confirm} type="button">
            下一步
          </button>
        </footer>
      </div>
    </div>
  );
}
