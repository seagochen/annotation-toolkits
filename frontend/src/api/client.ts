import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type ProjectListItem = components["schemas"]["ProjectListItem"];
export type ProjectDetail = components["schemas"]["ProjectDetail"];
export type QueueResponse = components["schemas"]["QueueResponse"];
export type AnnotationResponse = components["schemas"]["AnnotationResponse"];
export type ActionResponse = components["schemas"]["ActionResponse"];
export type ActionListResponse = components["schemas"]["ActionListResponse"];

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || window.location.origin;
const client = createClient<paths>({
  baseUrl: apiBaseUrl,
  fetch: (request) => globalThis.fetch(request),
});

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = error.detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      return String(detail.message);
    }
  }
  return fallback;
}

export async function listProjects(): Promise<ProjectListItem[]> {
  const { data, error, response } = await client.GET("/api/projects");
  if (!response.ok || !data) {
    throw new ApiError(
      response.status,
      errorMessage(error, "无法读取项目列表"),
    );
  }
  return data;
}

export async function getProject(projectId: string): Promise<ProjectDetail> {
  const { data, error, response } = await client.GET(
    "/api/projects/{project_id}",
    { params: { path: { project_id: projectId } } },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法读取项目详情"));
  }
  return data;
}

export async function getQueue(
  projectId: string,
  status = "pending",
  offset = 0,
): Promise<QueueResponse> {
  const { data, error, response } = await client.GET(
    "/api/projects/{project_id}/queue",
    {
      params: {
        path: { project_id: projectId },
        query: { offset, limit: 1, status },
      },
    },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法读取审核队列"));
  }
  return data;
}

/** One page of every queue item, finished or not, for the image list. */
export async function listQueueItems(
  projectId: string,
  offset: number,
  limit: number,
): Promise<QueueResponse> {
  const { data, error, response } = await client.GET(
    "/api/projects/{project_id}/queue",
    { params: { path: { project_id: projectId }, query: { offset, limit } } },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法读取图像列表"));
  }
  return data;
}

export async function submitAnnotation(
  projectId: string,
  itemId: string,
  result: Record<string, unknown>,
): Promise<AnnotationResponse> {
  const { data, error, response } = await client.POST(
    "/api/projects/{project_id}/annotations",
    {
      params: { path: { project_id: projectId } },
      body: { item_id: itemId, result },
    },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法保存审核结果"));
  }
  return data;
}

export function projectFileUrl(projectId: string, path: string): string {
  const encodedPath = path.split("/").map(encodeURIComponent).join("/");
  const endpoint = `/api/projects/${encodeURIComponent(projectId)}/files/${encodedPath}`;
  return `${apiBaseUrl.replace(/\/$/, "")}${endpoint}`;
}

export async function listActions(projectId: string): Promise<ActionListResponse> {
  const { data, error, response } = await client.GET(
    "/api/projects/{project_id}/actions",
    { params: { path: { project_id: projectId } } },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法读取任务动作"));
  }
  return data;
}

export async function startAction(
  projectId: string,
  action: string,
  options: Record<string, boolean> = {},
): Promise<ActionResponse> {
  const { data, error, response } = await client.POST(
    "/api/projects/{project_id}/actions/{action}",
    {
      params: { path: { project_id: projectId, action } },
      body: { options },
    },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法启动任务动作"));
  }
  return data;
}

export async function getAction(
  projectId: string,
  actionId: string,
): Promise<ActionResponse> {
  const { data, error, response } = await client.GET(
    "/api/projects/{project_id}/actions/jobs/{action_id}",
    {
      params: {
        path: { project_id: projectId, action_id: actionId },
      },
    },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法读取任务状态"));
  }
  return data;
}

// --- project management -------------------------------------------------------
// Response types come from the generated `paths` rather than schema names, so
// renaming a Pydantic model on the backend does not ripple into the UI.
type JsonOf<T> = T extends { content: { "application/json": infer R } } ? R : never;
type Ok<Op> = Op extends { responses: { 200: infer R } } ? JsonOf<R> : never;

export type TaskTypeInfo = Ok<paths["/api/task-types"]["get"]>[number];
export type FieldSpec = TaskTypeInfo["fields"][number];
export type ProjectSettings = Ok<paths["/api/projects/{project_id}/settings"]["get"]>;
export type SettingsValues = Record<string, unknown>;

export async function listTaskTypes(): Promise<TaskTypeInfo[]> {
  const { data, error, response } = await client.GET("/api/task-types");
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法读取任务类型"));
  }
  return data;
}

export async function createProject(
  name: string,
  taskType: string,
  settings: SettingsValues,
): Promise<ProjectDetail> {
  const { data, error, response } = await client.POST("/api/projects", {
    body: { name, task_type: taskType, settings },
  });
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法创建项目"));
  }
  return data as ProjectDetail;
}

export async function getSettings(projectId: string): Promise<ProjectSettings> {
  const { data, error, response } = await client.GET(
    "/api/projects/{project_id}/settings",
    { params: { path: { project_id: projectId } } },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法读取项目属性"));
  }
  return data;
}

export async function updateSettings(
  projectId: string,
  update: { name?: string; settings?: SettingsValues },
): Promise<ProjectSettings> {
  const { data, error, response } = await client.PUT(
    "/api/projects/{project_id}/settings",
    { params: { path: { project_id: projectId } }, body: update },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法保存项目属性"));
  }
  return data;
}

export async function updateConfigText(
  projectId: string,
  text: string,
): Promise<ProjectSettings> {
  const { data, error, response } = await client.PUT(
    "/api/projects/{project_id}/config",
    { params: { path: { project_id: projectId } }, body: { text } },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法保存配置文件"));
  }
  return data;
}

export async function deleteProject(projectId: string): Promise<void> {
  const { error, response } = await client.DELETE("/api/projects/{project_id}", {
    params: { path: { project_id: projectId } },
  });
  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(error, "无法删除项目"));
  }
}

export async function linkDirectory(
  projectId: string,
  path: string,
): Promise<ProjectSettings> {
  const { data, error, response } = await client.POST(
    "/api/projects/{project_id}/import/directory",
    { params: { path: { project_id: projectId } }, body: { path } },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法关联目录"));
  }
  return data;
}

function apiUrl(endpoint: string): string {
  return `${apiBaseUrl.replace(/\/$/, "")}${endpoint}`;
}

// Raw-body uploads go through plain fetch: the body is the file itself, which
// openapi-fetch would try to serialise as JSON.
async function postBinary(endpoint: string, body: Blob, fallback: string): Promise<unknown> {
  const response = await globalThis.fetch(apiUrl(endpoint), {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body,
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, errorMessage(payload, fallback));
  return payload;
}

export async function uploadFile(projectId: string, path: string, file: Blob): Promise<void> {
  const query = new URLSearchParams({ path });
  await postBinary(
    `/api/projects/${encodeURIComponent(projectId)}/import/files?${query}`,
    file,
    "上传失败",
  );
}

export async function uploadArchive(
  projectId: string,
  file: Blob,
): Promise<{ imported: number; skipped: number }> {
  const payload = await postBinary(
    `/api/projects/${encodeURIComponent(projectId)}/import/archive`,
    file,
    "压缩包导入失败",
  );
  const result = payload as { imported?: number; skipped?: number } | null;
  return { imported: result?.imported ?? 0, skipped: result?.skipped ?? 0 };
}

export function exportUrl(projectId: string, format: string): string {
  return apiUrl(
    `/api/projects/${encodeURIComponent(projectId)}/export?${new URLSearchParams({ format })}`,
  );
}
