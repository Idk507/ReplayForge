"use client";
import { useEffect, useState, type ReactNode } from "react";
import { request, setCsrfToken } from "../lib/client";
import { sessionSchema, type Session } from "../lib/auth";

export default function SessionGate({enabled,children,signIn}: {enabled:boolean;children:ReactNode;signIn:string|null}) {
  const [user,setUser]=useState<Session|null>(null),[loading,setLoading]=useState(enabled),[error,setError]=useState("");
  useEffect(()=>{
    if(!enabled)return;
    let stopped=false;
    function expired(){setCsrfToken(undefined);setUser(null);setError("Your session expired or access was revoked. Sign in again.");}
    window.addEventListener("replayforge:reauth",expired);
    request("auth/me",sessionSchema).then(value=>{if(!stopped){setCsrfToken(value.csrf_token);setUser(value);}})
      .catch(()=>{if(!stopped)setError("Sign in to view your repositories and private investigation evidence.");})
      .finally(()=>{if(!stopped)setLoading(false);});
    return ()=>{stopped=true;window.removeEventListener("replayforge:reauth",expired);};
  },[enabled]);
  async function logout(){
    setLoading(true);setError("");
    try {
      const response=await fetch("/api/backend/auth/logout",{method:"POST",headers:{"X-CSRF-Token":user?.csrf_token ?? ""}});
      if(!response.ok)throw new Error("LOGOUT_DENIED");
      setCsrfToken(undefined);setUser(null);window.location.assign("/login");
    } catch {setError("Sign out was not confirmed. Please retry.");setLoading(false);}
  }
  if(!enabled)return children;
  if(loading)return <main id="main" className="auth-panel"><p role="status">Checking your session…</p></main>;
  if(!user)return <main id="main" className="auth-panel"><p className="eyebrow">REPLAYFORGE / ACCOUNT</p><h1>Sign in to ReplayForge</h1><p>{error}</p>{signIn?<a className="primary" href={signIn}>Continue with GitHub</a>:<p role="alert">GitHub login is not configured.</p>}<p className="muted">Signing in does not authorize repository execution or pull requests.</p></main>;
  return <><div className="sessionbar"><span>Signed in as <strong>{user.github_login}</strong></span><a href="/settings/integrations">Connected repositories</a><button className="text-button" onClick={()=>void logout()}>Sign out</button>{error&&<span role="alert">{error}</span>}</div>{children}</>;
}
