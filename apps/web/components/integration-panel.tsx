"use client";
import { useEffect, useState } from "react";
import { z } from "zod";
import { request, errorMessage } from "../lib/client";
const installations=z.array(z.object({id:z.number().int().safe().positive(),account:z.object({login:z.string().max(39)})})).max(50);
const repositories=z.array(z.object({id:z.number().int().safe().positive(),full_name:z.string().max(140),private:z.boolean(),permissions:z.object({pull:z.boolean(),push:z.boolean()})})).max(50);
const connectedSchema=z.object({repository_id:z.uuid(),commits:z.array(z.string().regex(/^[a-f0-9]{40}$/)).length(1)});
type Connected=z.infer<typeof connectedSchema>;
export default function IntegrationPanel(){
  const [installs,setInstalls]=useState<z.infer<typeof installations>>([]),[repos,setRepos]=useState<z.infer<typeof repositories>>([]);
  const [installation,setInstallation]=useState(""),[repository,setRepository]=useState(""),[commit,setCommit]=useState("");
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(""),[connected,setConnected]=useState<Connected|null>(null);
  useEffect(()=>{let stopped=false;request("github/installations",installations).then(v=>{if(!stopped)setInstalls(v);}).catch(e=>{if(!stopped)setError(errorMessage(e));}).finally(()=>{if(!stopped)setLoading(false);});return()=>{stopped=true;};},[]);
  useEffect(()=>{
    if(!installation)return;
    let stopped=false;
    request(`github/repositories?installation_id=${installation}`,repositories).then(v=>{if(!stopped)setRepos(v);}).catch(e=>{if(!stopped)setError(errorMessage(e));});
    return()=>{stopped=true;};
  },[installation]);
  const selected=repos.find(v=>String(v.id)===repository);
  async function connect(event:React.FormEvent<HTMLFormElement>){
    event.preventDefault();if(!selected)return;
    const form=new FormData(event.currentTarget);setBusy(true);setError("");setConnected(null);
    try {setConnected(await request(`repositories/${selected.id}/connect`,connectedSchema,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({installation_id:Number(installation),repository_id:selected.id,source_commit:commit,consent:form.get("consent")==="on",private_access_approved:form.get("private")==="on"})}));}
    catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  }
  return <main id="main" className="auth-panel integrations"><p className="eyebrow">REPLAYFORGE / GITHUB</p><h1>Connect a repository</h1><p>Choose a repository available to both your GitHub account and the ReplayForge App. Pin the exact source commit.</p>
    {loading&&<p role="status">Loading GitHub installations…</p>}{error&&<p role="alert" className="auth-error">{error} Check your GitHub access or sign in again.</p>}
    {!loading&&!installs.length&&!error&&<p>No installations are available. Install the ReplayForge GitHub App on a selected repository, then reload this page.</p>}
    <form onSubmit={event=>void connect(event)}>
      <label htmlFor="installation">GitHub installation</label><select id="installation" value={installation} disabled={loading||busy} required onChange={e=>{setInstallation(e.target.value);setRepository("");setRepos([]);setError("");setConnected(null);}}><option value="">Choose an installation</option>{installs.map(v=><option key={v.id} value={v.id}>{v.account.login}</option>)}</select>
      <label htmlFor="github-repository">Repository</label><select id="github-repository" value={repository} disabled={!installation||busy} required onChange={e=>{setRepository(e.target.value);setConnected(null);}}><option value="">Choose a repository</option>{repos.map(v=><option key={v.id} value={v.id}>{v.full_name}{v.private?" (private)":""}</option>)}</select>
      {selected&&<p className="muted">GitHub access: {selected.permissions.push?"read and push":"read only"}. Connecting does not authorize a pull request.</p>}
      <label htmlFor="source-commit">Source commit</label><input id="source-commit" value={commit} onChange={e=>{setCommit(e.target.value);setConnected(null);}} required minLength={40} maxLength={40} pattern="[a-f0-9]{40}" placeholder="Full 40-character lowercase commit SHA" />
      <label className="check-label"><input type="checkbox" name="consent" required disabled={busy}/>I authorize ReplayForge to inspect this pinned commit and run bounded tests in its isolated remote runner.</label>
      {selected?.private&&<label className="check-label"><input type="checkbox" name="private" required disabled={busy}/>I explicitly approve access to this private repository.</label>}
      <p className="muted">Scope: src/, app/, tests/, README and Python dependency manifests. No workflow edits, merge or deployment. Creating an investigation is a separate action.</p>
      <button className="primary" type="submit" disabled={busy||!selected}>{busy?"Checking repository access…":"Connect repository"}</button>
    </form>
    {connected&&<section className="connection-result" aria-live="polite"><h2>Repository connected</h2><label htmlFor="connected-id">Approved repository ID</label><input id="connected-id" readOnly value={connected.repository_id}/><a className="primary" href={`/?repository=${connected.repository_id}&commit=${connected.commits[0]}`}>Start an investigation</a></section>}
    <p><a href="/">Back to workbench</a></p>
  </main>;
}
