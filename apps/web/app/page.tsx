import Workbench from "../components/workbench";
import SessionGate from "../components/session-gate";
import { loginUrl } from "../lib/auth";
export const dynamic="force-dynamic";
export default async function Page({searchParams}:{searchParams:Promise<{repository?:string;commit?:string}>}) {
  const params=await searchParams,enabled=process.env.REPLAYFORGE_WEB_AUTH_MODE==="github";
  const repository=typeof params.repository==="string" && /^[a-f0-9-]{36}$/.test(params.repository)?params.repository:"";
  const commit=typeof params.commit==="string" && /^[a-f0-9]{40}$/.test(params.commit)?params.commit:"";
  return <SessionGate enabled={enabled} signIn={loginUrl()}><Workbench sessionMode={enabled} initialRepository={repository} initialCommit={commit}/></SessionGate>;
}
