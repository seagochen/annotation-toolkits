import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type ProjectListItem = components["schemas"]["ProjectListItem"];
export type ProjectDetail = components["schemas"]["ProjectDetail"];
export type QueueResponse = components["schemas"]["QueueResponse"];
export type AnnotationResponse = components["schemas"]["AnnotationResponse"];

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
): Promise<QueueResponse> {
  const { data, error, response } = await client.GET(
    "/api/projects/{project_id}/queue",
    {
      params: {
        path: { project_id: projectId },
        query: { offset: 0, limit: 1, status },
      },
    },
  );
  if (!response.ok || !data) {
    throw new ApiError(response.status, errorMessage(error, "无法读取审核队列"));
  }
  return data;
}

export async function submitAnnotation(
  projectId: string,
  itemId: string,
  label: "same" | "different" | "unclear",
  notes: string,
): Promise<AnnotationResponse> {
  const { data, error, response } = await client.POST(
    "/api/projects/{project_id}/annotations",
    {
      params: { path: { project_id: projectId } },
      body: { item_id: itemId, result: { label, notes } },
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
