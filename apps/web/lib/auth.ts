import { z } from "zod";
export const sessionSchema=z.object({id:z.uuid(),github_id:z.number().int().safe().positive(),github_login:z.string().max(39),avatar_url:z.string().max(250),roles:z.array(z.string()).max(10),csrf_token:z.string().regex(/^[a-f0-9]{64}$/)});
export type Session=z.infer<typeof sessionSchema>;
export function loginUrl(): string | null {
  const base=process.env.REPLAYFORGE_API_URL ?? "http://127.0.0.1:8000";
  return /^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(base)?`${base}/api/v1/auth/github/start`:null;
}
