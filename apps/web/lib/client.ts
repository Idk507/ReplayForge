import type { z } from "zod";
export class ApiError extends Error {constructor(public status: number) {super(`Request failed (${status})`);}}
export async function request<T>(path: string, schema: z.ZodType<T>, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, {...options, cache: "no-store"});
  if (!response.ok) throw new ApiError(response.status);
  return schema.parse(await response.json());
}
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return ({401: "API authentication required. Check the server configuration.", 403: "Access denied for this repository or local session.", 404: "Not found, unavailable, or expired.", 409: "Run changed. Refresh before trying again.", 422: "Check the repository ID, commit and incident details.", 503: "Backend is not configured or unavailable."} as Record<number, string>)[error.status] ?? "Connection interrupted. Retry when the backend is available.";
  return "The response could not be validated. Refresh or check the backend.";
}
