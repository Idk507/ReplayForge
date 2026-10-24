"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { createSchema, researchSchema, type Research, gateNames, manifestSchema, parseEvents, patchSchema, runSchema, summarySchema, uuid, type Manifest, type Patch, type Run, type RunEvent, type Summary } from "../lib/contracts";
import { ApiError, errorMessage, request } from "../lib/client";

const terminal = new Set(["NOT_VERIFIED", "INCONCLUSIVE", "POLICY_BLOCKED", "CANCELLED", "CLOSED", "PR_OPENED", "PR_FAILED"]);
const label = (text: string) => text.replaceAll("_", " ");
const short = (text: string) => text.slice(0, 8);
const tone = (status: string) => ["VERIFIED", "PASS"].includes(status) ? "good" : ["FAIL", "NOT_VERIFIED", "POLICY_BLOCKED"].includes(status) ? "bad" : "neutral";

export default function Workbench({sessionMode=false,initialRepository="",initialCommit=""}: {sessionMode?:boolean;initialRepository?:string;initialCommit?:string}) {
  const [runs, setRuns] = useState<Run[]>([]), [run, setRun] = useState<Run | null>(null);
  const [selected, setSelected] = useState(""), [view, setView] = useState("timeline");
  const [events, setEvents] = useState<RunEvent[]>([]), [connection, setConnection] = useState("Not connected");
  const [research, setResearch] = useState<Research>([]);
  const [summary, setSummary] = useState<Summary | null>(null), [manifest, setManifest] = useState<Manifest | null>(null), [patch, setPatch] = useState<Patch | null>(null);
  const [error, setError] = useState(""), [detailError, setDetailError] = useState("");
  const [loading, setLoading] = useState(true), [intake, setIntake] = useState(Boolean(initialRepository && initialCommit)), [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{name: string; content: string} | null>(null);
  const [openId, setOpenId] = useState("");
  const nonce = useRef<string | null>(null);
  const activeRun = useRef("");
  const refresh = useCallback(async () => {
    try {setRuns(await request("runs", z.array(runSchema).max(30))); setError("");}
    catch (e) {setError(errorMessage(e));}
    finally {setLoading(false);}
  }, []);
  useEffect(() => {
    let stopped = false;
    request("runs", z.array(runSchema).max(30)).then(items => {
      if (!stopped) {setRuns(items); setLoading(false);}
    }).catch(e => {if (!stopped) {setError(errorMessage(e)); setLoading(false);}});
    return () => {stopped = true;};
  }, []);

  useEffect(() => {
    if (!selected) return;
    let stopped = false, cursor = 0, failures = 0, timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    async function poll() {
      try {
        const current = await request(`runs/${selected}`, runSchema, {signal: abort.signal});
        const response = await fetch(`/api/backend/runs/${selected}/events?after=${cursor}`, {signal: abort.signal, cache: "no-store"});
        if (!response.ok) throw new ApiError(response.status);
        const page = parseEvents(await response.text(), cursor);
        if (stopped) return;
        setRun(previous => previous?.run_id === current.run_id && previous.version >= current.version ? previous : current); setRuns(items => items.map(item => item.run_id === current.run_id && item.version <= current.version ? current : item));
        setEvents(items => [...items, ...page].slice(-500));
        if (page.length) cursor = page[page.length - 1].sequence;
        setConnection("Connected"); setDetailError(""); failures = 0;
        timer = setTimeout(poll, page.length === 100 ? 50 : terminal.has(current.current_stage) || sessionMode ? 10000 : 2500);
      } catch (e) {
        if (stopped) return;
        setConnection("Reconnecting"); setDetailError(errorMessage(e));
        failures++; timer = setTimeout(poll, Math.min(15000, 1000 * 2 ** Math.min(failures, 4)));
      }
    }
    void poll();
    return () => {stopped = true; abort.abort(); clearTimeout(timer);};
  }, [selected,sessionMode]);

  useEffect(() => {
    if (!run) return;
    let stopped = false;
    async function loadEvidence(current: Run) {
      try {
        const citations = await request(`runs/${current.run_id}/research`, researchSchema); if (!stopped) setResearch(citations);
        if (current.verification_ref) {const value = await request(`runs/${current.run_id}/verification`, summarySchema); if (!stopped) setSummary(value);}
        if (current.evidence_manifest_ref) {const value = await request(`runs/${current.run_id}/evidence/${current.evidence_manifest_ref}`, manifestSchema); if (!stopped) setManifest(value);}
        try {const value = await request(`runs/${current.run_id}/patch`, patchSchema); if (!stopped) setPatch(value);}
        catch (e) {if (!(e instanceof ApiError && e.status === 404)) throw e;}
      } catch (e) {if (!stopped) setDetailError(errorMessage(e));}
    }
    void loadEvidence(run);
    return () => {stopped = true;};
  }, [run]);

  function choose(id: string) {
    activeRun.current = id; setSelected(id); setIntake(false); setView("timeline");
    setRun(null); setEvents([]); setSummary(null); setManifest(null);
    setPatch(null); setResearch([]); setPreview(null); setDetailError(""); setConnection("Connecting");
  }

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    nonce.current ??= crypto.randomUUID();
    const value = createSchema.safeParse({repository_id: form.get("repository"), commit_sha: form.get("commit"), incident_text: form.get("incident"), idempotency_key: nonce.current});
    if (!value.success) {setError("Enter a repository UUID, 40-character lowercase commit SHA and incident details."); setBusy(false); return;}
    try {
      const created = await request("runs", runSchema, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(value.data)});
      setRuns(items => [created, ...items.filter(r => r.run_id !== created.run_id)].slice(0,30)); choose(created.run_id); nonce.current = null;
    } catch (e) {setError(`${errorMessage(e)} If delivery was interrupted, retry unchanged details to reuse the request ID.`);}
    finally {setBusy(false);}
  }
  async function cancel() {
    if (!run || !confirm("Cancel this run? Completed evidence will be retained.")) return;
    setBusy(true);
    try {const updated = await request(`runs/${run.run_id}/cancel`, runSchema, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({expected_version: run.version})}); setRun(updated); setRuns(items => items.map(item => item.run_id === updated.run_id ? updated : item));}
    catch (e) {setDetailError(errorMessage(e));} finally {setBusy(false);}
  }
  async function inspect(name: string, artifact: string) {
    if (!run || !manifest) return;
    try {
      const response = await fetch(`/api/backend/runs/${run.run_id}/evidence/${manifest.manifest_id}/artifacts/${artifact}`);
      if (!response.ok) throw new ApiError(response.status);
      const content = await response.arrayBuffer();
      if (content.byteLength > 65536) throw new Error("ARTIFACT_LIMIT");
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", content)), b => b.toString(16).padStart(2,"0")).join("");
      if (`sha256:${hash}` !== artifact) throw new Error("ARTIFACT_INTEGRITY");
      if (activeRun.current !== run.run_id) return;
      setPreview({name, content: new TextDecoder("utf-8", {fatal: true}).decode(content)});
    } catch (e) {setDetailError(errorMessage(e));}
  }

  return <div className="shell">
    <header className="topbar"><a className="brand" href="/"><span className="mark">R↻</span>ReplayForge</a><span className="workspace-name">Investigation workbench</span><span className="local"><span />LOCAL DEVELOPMENT</span></header>
    <div className="workspace">
      <aside className="sidebar" aria-label="Run navigation"><div className="sidebar-top"><span className="eyebrow">WORKSPACE / RUNS</span><button className="primary" onClick={() => {setIntake(true); setError("");}}>＋ New investigation</button></div>
        <div className="section-label"><h2>Recent runs</h2><button className="text-button" onClick={() => void refresh()} disabled={loading} aria-label="Refresh runs">↻</button></div>
        {loading && <p role="status" className="muted inset">Loading runs…</p>}
        {!loading && !runs.length && <p className="muted inset">No recent runs. Create an investigation or open an existing run ID.</p>}
        <nav>{runs.map(item => <button key={item.run_id} className={`run-link ${item.run_id === selected ? "selected" : ""}`} onClick={() => {choose(item.run_id);}}><span className={`status-dot ${tone(item.current_stage)}`} /><span><strong>Run {short(item.run_id)}</strong><small>{label(item.current_stage)}</small></span><span className="chevron">›</span></button>)}</nav>
        <form className="open-run" onSubmit={e => {e.preventDefault(); if (uuid.safeParse(openId).success) {choose(openId);} else setError("Enter a valid run UUID.");}}><label htmlFor="open-id">Open run by ID</label><input id="open-id" value={openId} onChange={e => setOpenId(e.target.value)} placeholder="Run UUID" /><button className="secondary" type="submit">Open run →</button></form>
        <div className="sidebar-foot"><strong>Evidence before approval.</strong><p>Only persisted observations appear here. Missing evidence never counts as a pass.</p></div>
      </aside>
      <main id="main" className="main" tabIndex={-1}>
        {error && <div className="error" role="alert">{error} <button onClick={() => void refresh()}>Retry connection</button></div>}
        {intake ? <section className="intake"><p className="eyebrow">01 / SCOPE THE INCIDENT</p><h1>Start an investigation</h1><p className="lede">Pin the source. Describe the failure. Build a reviewable trail of evidence.</p><form onSubmit={create} onChange={() => {nonce.current = null;}}><label htmlFor="repository">Approved repository ID</label><input id="repository" name="repository" defaultValue={initialRepository} required placeholder="Repository UUID from operator configuration" /><label htmlFor="commit">Source commit</label><input id="commit" name="commit" defaultValue={initialCommit} required minLength={40} maxLength={40} placeholder="Full 40-character commit SHA" /><label htmlFor="incident">What happened?</label><textarea id="incident" name="incident" required maxLength={20000} rows={7} placeholder="Expected behavior, observed failure, and a small reproducible example. Do not include credentials." /><p className="muted">The backend validates repository consent. Creating a run does not authorize cloud execution or publication.</p><button className="primary" disabled={busy}>{busy ? "Submitting…" : "Create investigation →"}</button><button className="text-button" type="button" onClick={() => setIntake(false)}>Back to workspace</button></form></section>
        : !selected ? <section className="empty"><p className="eyebrow">REPRODUCE / REPAIR / VERIFY</p><h1>Every repair needs<br />a trail of evidence.</h1><p className="lede">Trace an incident from pinned source to observed failure, candidate patch and independent replay.</p><button className="primary" onClick={() => setIntake(true)}>Start an investigation →</button><div className="principles"><span><b>01</b>Pinned source</span><span><b>02</b>Observed failure</span><span><b>03</b>Independent replay</span></div></section>
        : <section className="investigation"><div className="breadcrumb">WORKSPACE <span>/</span> INVESTIGATION <span>/</span> {short(selected)}</div><div className="run-title"><div><p className="eyebrow">INCIDENT REPAIR</p><h1>Investigation <span className="mono">{short(selected)}</span></h1></div><span className={`badge ${tone(run?.current_stage ?? "LOADING")}`}>{label(run?.current_stage ?? "LOADING")}</span></div>
          <div className="identity-strip"><span>SOURCE <code>{run?.source_commit ?? "Loading…"}</code></span><span>VERSION <b>{run?.version ?? "—"}</b></span><span className="connection" role="status">● {connection}</span></div>
          {detailError && <div className="error" role="alert">{detailError} Events resume automatically from the last validated sequence.</div>}
          {run?.current_stage==="CREATED"&&<div className="blank-panel" role="status"><strong>Queued: waiting for a worker</strong><p>Your incident is saved. A background worker must start collection before tests, patches or verification can appear. This page does not run repository code itself.</p></div>}
          {run?.failure_reason && <p className="error" role="status">Run ended: {label(run.failure_reason)}</p>}
          <div className="tabs" role="tablist" aria-label="Investigation views">{["timeline", "evidence", "patch"].map(tab => <button role="tab" aria-selected={view === tab} aria-controls={`panel-${tab}`} id={`tab-${tab}`} key={tab} onClick={() => setView(tab)}>{tab === "patch" ? "Patch & risk" : label(tab)}{tab === "timeline" && <small>{events.length}</small>}</button>)}</div>
          <div role="tabpanel" id={`panel-${view}`} aria-labelledby={`tab-${view}`}>
          {view === "timeline" && <div className="timeline-layout"><div><div className="panel-heading"><h2>Investigation timeline</h2><span className="muted">Persisted events · last 500</span></div>{!events.length && <div className="blank-panel">Waiting for recorded events. No steps are assumed complete.</div>}<ol className="timeline">{events.map(event => <li key={event.sequence}><span className="event-number">{String(event.sequence).padStart(2,"0")}</span><div><strong>{label(event.stage)}</strong><p>{label(event.event_type)} <span>· Version {event.version}</span></p></div><time dateTime={event.observed_at}>{new Date(event.observed_at).toLocaleTimeString()}</time></li>)}</ol><section className="citations"><div className="panel-heading"><h2>Documentation research</h2><span className="muted">Advisory evidence</span></div>{!research.length && <p className="muted">No retained documentation search for this run.</p>}{research.map((record,i) => <div key={i}>{record.report.sources.map(source => <article key={source.url}><a href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a><p>{source.snippet}</p><small>{record.report.evidence_origin} · <time dateTime={source.retrieved_at}>{new Date(source.retrieved_at).toLocaleString()}</time></small><code>{source.url}</code></article>)}</div>)}<p className="muted">External content is untrusted and cannot authorize tools or execution.</p></section></div><aside className="run-facts"><p className="eyebrow">RUN CONTRACT</p><h3>Bounded by design</h3><dl><dt>Model calls remaining</dt><dd>{run?.remaining_budget.model_calls ?? "—"}</dd><dt>Sandbox commands</dt><dd>{run?.remaining_budget.sandbox_commands ?? "—"}</dd><dt>Research searches</dt><dd>{run?.remaining_budget.research_searches ?? "—"}</dd><dt>Reserved time remaining</dt><dd>{run?.remaining_budget.execution_seconds ?? "—"} s</dd></dl><p className="muted">Remaining budgets are limits, not measured costs or usage.</p><p className="eyebrow">TRACE ID</p><code>{run?.trace_id ?? "—"}</code>{run && !terminal.has(run.current_stage) && <button className="danger" disabled={busy} onClick={() => void cancel()}>Cancel investigation</button>}</aside></div>}
          {view === "evidence" && <><div className="panel-heading"><h2>Verification matrix</h2><span className="muted">{summary ? label(summary.verdict.status) : "No retained verdict"}</span></div><div className="gate-list">{gateNames.slice(0,7).map(gate => {const record = summary?.gates.find(g => g.gate === gate); return <div className="gate-row" key={gate}><span>{label(gate)}</span><span className={`badge ${tone(record?.status ?? "MISSING")}`}>{record?.status ?? "MISSING"}</span></div>;})}</div><p className="muted">Evidence is not publication approval. Each gate must have authenticated backing before a run can be verified.</p><div className="panel-heading"><h2>Retained artifacts</h2><span className="muted">Integrity-checked previews</span></div>{!manifest && <div className="blank-panel">No retained manifest is available for this run.</div>}<div className="artifacts">{manifest?.entries.map(entry => <button className="artifact" key={entry.name} onClick={() => void inspect(entry.name, entry.artifact_id)}><strong>{entry.name} ↗</strong><span>{entry.size} bytes</span><code>{entry.artifact_id}</code></button>)}</div>{preview && <section className="preview"><div className="panel-heading"><h3>{preview.name}</h3><button className="text-button" onClick={() => setPreview(null)}>Close preview</button></div><pre>{preview.content}</pre></section>}</>}
          {view === "patch" && <><div className="panel-heading"><h2>Candidate patch</h2><span className="muted">Retained proposal · not execution proof</span></div>{!patch ? <div className="blank-panel">No candidate patch is retained or available for this run.</div> : <><p className="lede small">{patch.proposal.justification}</p><pre className="diff">{patch.proposal.unified_diff.split("\n").map((line,i) => <span key={i} className={line.startsWith("+") ? "added" : line.startsWith("-") ? "removed" : ""}>{line}{"\n"}</span>)}</pre><h3>Remaining risk</h3><p>{patch.proposal.risk_notes}</p></>}</>}
          </div>
        </section>}
        <footer>ReplayForge <span>Evidence-backed repair · Human approval required for publication</span></footer>
      </main>
    </div>
  </div>;
}
