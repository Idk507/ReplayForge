/** Loopback session BFF. Never substitutes a server operator token for a user. */
export async function sessionBridge(request: Request, segments: string[]): Promise<Response> {
  const url=new URL(request.url), host=request.headers.get("host") ?? url.host;
  const origin=`http://${host}`;
  if(!/^(127\.0\.0\.1|localhost):[0-9]{1,5}$/.test(host) ||
     request.headers.get("sec-fetch-site")==="cross-site" ||
     (request.headers.has("origin") && request.headers.get("origin")!==origin)) {
    return Response.json({error_class:"LOCAL_ORIGIN_REQUIRED"},{status:403});
  }
  const path=segments.join("/"), id="[a-fA-F0-9-]{36}", digest="sha256:[a-f0-9]{64}";
  const read=new RegExp(`^runs(?:/${id}(?:/(?:events|verification|patch|research)|/evidence/${digest}(?:/artifacts/${digest})?)?)?$`);
  const readable=read.test(path) || ["auth/me","github/installations","github/repositories","github/head"].includes(path);
  const writable=path==="runs" || path==="auth/logout" ||
    new RegExp(`^runs/${id}/(?:cancel|approvals|pull-request)$`).test(path) ||
    /^repositories\/[1-9][0-9]{0,18}\/connect$/.test(path);
  if((request.method!=="GET" || !readable) && (request.method!=="POST" || !writable))
    return Response.json({error_class:"PATH_DENIED"},{status:404});
  if(request.method==="POST" && request.headers.get("origin")!==origin)
    return Response.json({error_class:"LOCAL_ORIGIN_REQUIRED"},{status:403});
  const query=[...url.searchParams.keys()];
  if(query.length && !((path.endsWith("/events") && query.length===1 && query[0]==="after" && /^[0-9]{1,15}$/.test(url.searchParams.get("after") ?? "")) ||
    (["github/repositories","github/head"].includes(path) &&
      query.length===new Set(query).size && query.length<=(path==="github/head"?3:2) &&
      query.includes("installation_id") && (path!=="github/head"||query.includes("repository_id")) &&
      query.every(key=>["installation_id","page",...(path==="github/head"?["repository_id"]:[])].includes(key) && /^[1-9][0-9]{0,18}$/.test(url.searchParams.get(key)??"")))))
    return Response.json({error_class:"QUERY_DENIED"},{status:400});
  const base=process.env.REPLAYFORGE_API_URL ?? "http://127.0.0.1:8000";
  if(!/^http:\/\/127\.0\.0\.1:[0-9]{1,5}$/.test(base))
    return Response.json({error_class:"BACKEND_NOT_CONFIGURED"},{status:503});
  const cookie=request.headers.get("cookie") ?? "";
  if(cookie.length>8192) return Response.json({error_class:"AUTH_REQUIRED"},{status:401});
  const values=cookie.split(";").map(v=>v.trim()).filter(v=>v.startsWith("rf_dev_session="));
  if(values.length!==1 || !/^rf_dev_session=[A-Za-z0-9_-]{43}$/.test(values[0]))
    return Response.json({error_class:"AUTH_REQUIRED"},{status:401});
  const headers=new Headers({Cookie:values[0],"Accept-Encoding":"identity"});
  if(request.headers.has("origin")) headers.set("Origin",request.headers.get("origin")!);
  if(request.headers.has("x-csrf-token")) {
    const csrf=request.headers.get("x-csrf-token")!;
    if(!/^[a-f0-9]{64}$/.test(csrf)) return Response.json({error_class:"CSRF_DENIED"},{status:403});
    headers.set("X-CSRF-Token",csrf);
  }
  try {
    let body: string | undefined;
    if(request.method==="POST" && path!=="auth/logout") {
      const raw=await bounded(request.body,32768);
      body=JSON.stringify(JSON.parse(new TextDecoder().decode(raw)));
      headers.set("Content-Type","application/json");
    }
    const upstream=await fetch(`${base}/api/v1/${path}${url.search}`,{method:request.method,
      headers,body,redirect:"error",cache:"no-store",signal:AbortSignal.timeout(30000)});
    const output=new Headers({"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"});
    if(path==="auth/logout") for(const value of upstream.headers.getSetCookie()) output.append("Set-Cookie",value);
    if(!upstream.ok) {
      await upstream.body?.cancel();
      return Response.json({error_class:"BACKEND_DENIED"},{status:[400,401,403,404,409,422,429,503].includes(upstream.status)?upstream.status:502,headers:output});
    }
    if(upstream.status===204) return new Response(null,{status:204,headers:output});
    const data=await bounded(upstream.body,1048576);
    output.set("Content-Type",path.includes("/artifacts/")?"application/octet-stream":path.endsWith("/events")?"text/event-stream":"application/json");
    if(path.includes("/artifacts/")) output.set("Content-Disposition","attachment; filename=artifact.txt");
    return new Response(data,{status:upstream.status,headers:output});
  } catch {
    return Response.json({error_class:request.method==="POST"?"REQUEST_UNCONFIRMED":"BACKEND_UNAVAILABLE"},{status:502,headers:{"Cache-Control":"no-store"}});
  }
}

async function bounded(stream: ReadableStream<Uint8Array> | null,max: number): Promise<ArrayBuffer> {
  const reader=stream?.getReader(); if(!reader)return new ArrayBuffer(0);
  const chunks:Uint8Array[]=[];let length=0;
  try {while(true){const {done,value}=await reader.read();if(done)break;length+=value.length;if(length>max)throw new Error("BODY_LIMIT");chunks.push(value);}}
  finally {await reader.cancel();reader.releaseLock();}
  const result=new Uint8Array(length);let offset=0;
  for(const chunk of chunks){result.set(chunk,offset);offset+=chunk.length;}return result.buffer;
}
