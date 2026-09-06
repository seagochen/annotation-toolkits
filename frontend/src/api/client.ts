import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type ProjectListItem = components["schemas"]["ProjectListItem"];
export type ProjectDetail = components["schemas"]["ProjectDetail"];

const client = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE_URL || window.location.origin,
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
