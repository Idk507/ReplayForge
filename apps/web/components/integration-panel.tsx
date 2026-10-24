"use client";
import { useEffect, useState } from "react";
import { z } from "zod";
import { request, errorMessage } from "../lib/client";

const installations = z.array(z.object({id:z.number().int().safe().positive(),account:z.object({login:z.string().max(39)}),permissions:z.object({contents:z.enum(["none","read","write"]).optional()}).optional()})).max(50);
const repositories = z.array(z.object({id:z.number().int().safe().positive(),full_name:z.string().max(140),private:z.boolean(),permissions:z.object({pull:z.boolean(),push:z.boolean()}),disabled:z.boolean().optional()})).max(50);
const connectedSchema = z.object({repository_id:z.uuid(),commits:z.array(z.string().regex(/^[a-f0-9]{40}$/)).length(1)});
const headSchema = z.object({sha:z.string().regex(/^[a-f0-9]{40}$/)});
type Connected = z.infer<typeof connectedSchema>;

export default function IntegrationPanel({installUrl}:{installUrl:string|null}) {
  const [installs,setInstalls] = useState<z.infer<typeof installations>>([]);
  const [repos,setRepos] = useState<z.infer<typeof repositories>>([]);
  const [installation,setInstallation] = useState("");
  const [repository,setRepository] = useState("");
  const [commit,setCommit] = useState("");
  const [page,setPage] = useState(1);
  const [revision,setRevision] = useState(0);
  const [filter,setFilter] = useState("");
  const [loading,setLoading] = useState(true);
  const [repoLoading,setRepoLoading] = useState(false);
  const [headLoading,setHeadLoading] = useState(false);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  const [connected,setConnected] = useState<Connected|null>(null);
  useEffect(() => {
    let stopped=false;
    request("github/installations",installations).then(values => {
      if(stopped)return;
      setInstalls(values);
      setInstallation(previous=>values.some(v=>String(v.id)===previous)?previous:values.length===1?String(values[0].id):"");
    }).catch(e=>{if(!stopped)setError(errorMessage(e));}).finally(()=>{if(!stopped)setLoading(false);});
    return()=>{stopped=true;};
  },[revision]);
  useEffect(() => {
    if(!installation)return;
    let stopped=false;
    request(`github/repositories?installation_id=${installation}&page=${page}`,repositories)
      .then(values=>{if(!stopped)setRepos(values);})
      .catch(e=>{if(!stopped)setError(errorMessage(e));})
      .finally(()=>{if(!stopped)setRepoLoading(false);});
    return()=>{stopped=true;};
  },[installation,page,revision]);
  useEffect(() => {
    if(!repository||!installation)return;
    let stopped=false;
    request(`github/head?installation_id=${installation}&repository_id=${repository}&page=${page}`,headSchema)
      .then(value=>{if(!stopped)setCommit(value.sha);})
      .catch(()=>{if(!stopped)setError("Could not resolve the latest commit. Retry or enter an exact commit under Advanced.");})
      .finally(()=>{if(!stopped)setHeadLoading(false);});
    return()=>{stopped=true;};
  },[repository,installation,page]);
  const selected = repos.find(v=>String(v.id)===repository);
  function resetSelection(){setRepository("");setCommit("");setConnected(null);setError("");setFilter("");setHeadLoading(false);}
  function refresh(){resetSelection();setLoading(true);setRepoLoading(Boolean(installation));setRepos([]);setRevision(v=>v+1);}
  function changePage(value:number){resetSelection();setRepos([]);setRepoLoading(true);setPage(value);}
  async function connect(event:React.FormEvent<HTMLFormElement>){
    event.preventDefault();if(!selected)return;
    const form = new FormData(event.currentTarget);setBusy(true);setError("");setConnected(null);
    try {
      setConnected(await request(`repositories/${selected.id}/connect`,connectedSchema,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({installation_id:Number(installation),repository_id:selected.id,repository_page:page,source_commit:commit,consent:form.get("consent")==="on",private_access_approved:form.get("private")==="on"})}));
    } catch(e){setError(errorMessage(e));} finally{setBusy(false);}
  }
  return <main id="main" className="auth-panel integrations">
    <p className="eyebrow">REPLAYFORGE / GET STARTED</p><h1>Choose your repository</h1>
    <p>1. Choose a repository. 2. Confirm access. 3. Describe the incident.</p>
    <p>We pin the latest source commit automatically. Connecting does not start tests or create a pull request.</p>
    <div className="onboarding-actions">{installUrl&&<a href={installUrl} target="_blank" rel="noopener noreferrer">Manage GitHub access</a>}<button type="button" onClick={refresh} disabled={loading||busy}>Refresh repositories</button></div>
    {loading&&<p role="status">Checking GitHub access...</p>}
    {error&&<p role="alert" className="auth-error">{error}</p>}
    {!loading&&!installs.length&&!error&&<p>Install the ReplayForge GitHub App using Manage GitHub access, choose your repositories, then return and press Refresh repositories.</p>}
    <form onSubmit={event=>void connect(event)}>
      <label htmlFor="installation">GitHub installation</label>
      <select id="installation" value={installation} disabled={loading||busy} required onChange={e=>{if(e.target.value===installation)return;resetSelection();setRepos([]);setRepoLoading(Boolean(e.target.value));setPage(1);setInstallation(e.target.value);}}>
        <option value="">Choose an installation</option>{installs.map(v=><option key={v.id} value={v.id}>{v.account.login}</option>)}
      </select>
      {repoLoading&&<p role="status">Loading repositories...</p>}
      {installation&&!loading&&!repoLoading&&!repos.length&&!error&&<p>No repositories are available on this page. In GitHub, approve Contents read access and select a repository, then refresh. Use Previous if you reached the end of the list.</p>}
      <label htmlFor="repository-filter">Filter repositories on this page</label>
      <input id="repository-filter" value={filter} onChange={e=>setFilter(e.target.value)} placeholder="Repository name" disabled={!installation||repoLoading}/>
      <fieldset className="repository-picker" disabled={!installation||repoLoading||busy}>
        <legend>Repository</legend>
        <div className="repository-options" aria-busy={repoLoading}>
          {repos.filter(v=>v.full_name.toLowerCase().includes(filter.toLowerCase())).map(v=><label key={v.id} className={`repository-choice ${repository===String(v.id)?"selected":""}`}>
            <input type="radio" name="repository-choice" value={v.id} checked={repository===String(v.id)} disabled={!v.permissions.pull||v.disabled} onChange={()=>{setRepository(String(v.id));setCommit("");setConnected(null);setError("");setHeadLoading(true);}}/>
            <span>{v.full_name}{v.private?" (private)":""}</span>
          </label>)}
        </div>
        {repos.length>0&&!repos.some(v=>v.full_name.toLowerCase().includes(filter.toLowerCase()))&&<p>No matching repository on page {page}. Clear the filter or browse another page.</p>}
      </fieldset>
      {installation&&!repoLoading&&error&&<button type="button" onClick={refresh}>Retry page {page}</button>}
      {installation&&<div className="onboarding-actions"><button type="button" disabled={page===1||repoLoading||busy} onClick={()=>changePage(page-1)}>Previous</button><span aria-live="polite">Page {page}</span><button type="button" disabled={repos.length<50||page>=20000||repoLoading||busy} onClick={()=>changePage(page+1)}>Next</button></div>}
      {selected&&<p className="muted">Selected: {selected.full_name}. GitHub access: {selected.permissions.push?"read and push":"read only"}. ReplayForge still requires separate approval to publish.</p>}
      {headLoading&&<p role="status">Pinning the latest source commit...</p>}
      {commit&&<p className="muted">Source pinned to {commit.slice(0,12)}.</p>}
      <details><summary>Advanced: choose an exact source commit</summary><label htmlFor="source-commit">Source commit</label><input id="source-commit" value={commit} onChange={e=>{setCommit(e.target.value);setConnected(null);}} minLength={40} maxLength={40} pattern="[a-f0-9]{40}" disabled={headLoading||busy}/></details>
      <label className="check-label"><input key={`consent-${repository}-${commit}`} type="checkbox" name="consent" required disabled={busy}/>I authorize ReplayForge to inspect this pinned commit and run bounded tests in its isolated remote runner.</label>
      {selected?.private&&<label className="check-label"><input key={`private-${repository}-${commit}`} type="checkbox" name="private" required disabled={busy}/>I explicitly approve access to this private repository.</label>}
      <p className="muted">Scope: src/, app/, tests/, README and Python dependency manifests. No workflow edits, merge or deployment.</p>
      <button className="primary" type="submit" disabled={busy||!selected||!selected.permissions.pull||selected.disabled||headLoading||!commit}>{busy?"Connecting repository...":"Connect repository"}</button>
    </form>
    {connected&&<section className="connection-result" aria-live="polite"><h2>Repository connected</h2><p>Ready. Continue to describe the incident; tests have not started.</p><label htmlFor="connected-id">Approved repository ID</label><input id="connected-id" readOnly value={connected.repository_id}/><a className="primary" href={`/?repository=${connected.repository_id}&commit=${connected.commits[0]}`}>Start an investigation</a></section>}
    <p><a href="/">Back to workbench</a></p>
  </main>;
}
