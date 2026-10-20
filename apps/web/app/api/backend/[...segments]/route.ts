import { bridge } from "../../../../lib/bridge";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = {params: Promise<{segments: string[]}>};
export async function GET(request: Request, context: Context) {return bridge(request, (await context.params).segments);}
export async function POST(request: Request, context: Context) {return bridge(request, (await context.params).segments);}
