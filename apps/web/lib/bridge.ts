import { sessionBridge } from "./session-bridge";
import { createSchema } from "./contracts";

const id = "[a-fA-F0-9-]{36}";
const digest = "sha256:[a-f0-9]{64}";
const readPath = new RegExp(`^runs(?:/${id}(?:/(?:events|verification|executions|patch|research)|/source-manifests/${digest}|/evidence/${digest}(?:/artifacts/${digest})?)?)?$`);

/** Local-only BFF: fixed destination, closed paths, server-side bearer, no retries. */
export async function bridge(request: Request, segments: string[]): Promise<Response> {
  if(process.env.REPLAYFORGE_WEB_AUTH_MODE==="github") return sessionBridge(request,segments);
  if(process.env.REPLAYFORGE_WEB_AUTH_MODE && process.env.REPLAYFORGE_WEB_AUTH_MODE!=="local") return Response.json({error_class:"AUTH_MODE_INVALID"},{status:503});
  const url = new URL(request.url);
  const host = request.headers.get("host") ?? url.host;
  const localOrigin = `http://${host}`;
  if (!/^(127\.0\.0\.1|localhost):[0-9]{1,5}$/.test(host) ||
      request.headers.get("sec-fetch-site") === "cross-site" ||
      (request.headers.has("origin") && request.headers.get("origin") !== localOrigin)) {
    return Response.json({error_class: "LOCAL_ORIGIN_REQUIRED"}, {status: 403});
  }
  const path = segments.join("/");
  const write = request.method === "POST" && (path === "runs" || new RegExp(`^runs/${id}/cancel$`).test(path));
  if (!(request.method === "GET" && readPath.test(path)) && !write) {
    return Response.json({error_class: "PATH_DENIED"}, {status: 404});
  }
  if (write && request.headers.get("origin") !== localOrigin) {
    return Response.json({error_class: "LOCAL_ORIGIN_REQUIRED"}, {status: 403});
  }
  if ([...url.searchParams.keys()].some(k => k !== "after") ||
      (url.search && (!path.endsWith("/events") || !/^[0-9]{1,15}$/.test(url.searchParams.get("after") ?? "")))) {
    return Response.json({error_class: "QUERY_DENIED"}, {status: 400});
  }
  const base = process.env.REPLAYFORGE_API_URL ?? "http://127.0.0.1:8000";
  const token = process.env.REPLAYFORGE_API_TOKEN;
  if (!/^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(base) || !token || !/^[\x21-\x7e]{32,256}$/.test(token)) {
    return Response.json({error_class: "BACKEND_NOT_CONFIGURED"}, {status: 503});
  }
  try {
    let body: string | undefined;
    if (write) {
      if (Number(request.headers.get("content-length") ?? "0") > 32768) throw new Error("BODY_LIMIT");
      const raw = await bounded(request.body, 32768);
      const parsed: unknown = JSON.parse(new TextDecoder().decode(raw));
      if (path === "runs") body = JSON.stringify(createSchema.parse(parsed));
      else {
        const value = parsed as {expected_version?: unknown};
        if (Object.keys(value).length !== 1 || !Number.isSafeInteger(value.expected_version) || Number(value.expected_version) < 0) throw new Error("BODY_INVALID");
        body = JSON.stringify(value);
      }
    }
    const headers: Record<string, string> = {Authorization: `Bearer ${token}`, "Accept-Encoding": "identity"};
    if (body) headers["Content-Type"] = "application/json";
    const upstream = await fetch(`${base}/api/v1/${path}${url.search}`, {
      method: request.method, headers, body, cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(10000),
    });
    if (!upstream.ok) {
      const status = [400,401,403,404,409,422,503].includes(upstream.status) ? upstream.status : 502;
      await upstream.body?.cancel();
      return Response.json({error_class: `BACKEND_${status}`}, {status, headers: {"Cache-Control": "no-store"}});
    }
    const data = await bounded(upstream.body, 1048576);
    const artifact = path.includes("/artifacts/");
    return new Response(data, {status: upstream.status, headers: {
      "Content-Type": artifact ? "application/octet-stream" : path.endsWith("/events") ? "text/event-stream" : "application/json",
      "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
      ...(artifact ? {"Content-Disposition": "attachment; filename=artifact.txt"} : {}),
    }});
  } catch {
    return Response.json({error_class: write ? "REQUEST_UNCONFIRMED" : "BACKEND_UNAVAILABLE"}, {status: 502, headers: {"Cache-Control": "no-store"}});
  }
}

async function bounded(stream: ReadableStream<Uint8Array> | null, max: number): Promise<ArrayBuffer> {
  const reader = stream?.getReader();
  if (!reader) return new ArrayBuffer(0);
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.length;
      if (size > max) throw new Error("BODY_LIMIT");
      chunks.push(value);
    }
  } finally {await reader.cancel(); reader.releaseLock();}
  const result = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) {result.set(chunk, offset); offset += chunk.length;}
  return result.buffer;
}
