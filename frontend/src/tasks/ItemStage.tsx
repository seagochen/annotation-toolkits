import type { ReactNode } from "react";

import { projectFileUrl } from "../api/client";
import { itemText, type QueueItem } from "./useTaskQueue";

/** Whether a queue item is a text document (`media: "text"`) rather than an image. */
export function isTextItem(item: QueueItem): boolean {
  return item.media === "text";
}

/**
 * A text document as the backend decoded it: whitespace and line breaks kept,
 * long documents scroll inside the stage. An undecodable file shows the
 * backend's reason instead of guessed characters.
 */
export function TextDocument({
  path,
  text,
  error,
  children,
}: {
  path: string;
  text: string | null;
  error: string | null;
  /** Replaces the plain text (the span page renders highlighted segments). */
  children?: ReactNode;
}) {
  if (error || text === null) {
    return (
      <div className="stage-text">
        <div className="text-document-error" role="alert">
          <strong>无法显示文本</strong>
          <p>{error || "文本内容不可用。"}</p>
        </div>
      </div>
    );
  }
  return (
    <div className="stage-text">
      <article aria-label={`文本：${path}`} className="text-document">
        {children ?? (text || <span className="muted">（空文档）</span>)}
      </article>
    </div>
  );
}

/** What the stage shows for an image or text item of classification/captioning. */
export function ItemStage({ projectId, item }: { projectId: string; item: QueueItem }) {
  const path = itemText(item, "image_path");
  if (isTextItem(item)) {
    return (
      <TextDocument
        error={item.text_error == null ? null : String(item.text_error)}
        path={path}
        text={typeof item.text === "string" ? item.text : null}
      />
    );
  }
  return (
    <figure className="stage-image">
      <img alt={path} src={projectFileUrl(projectId, path)} />
    </figure>
  );
}
