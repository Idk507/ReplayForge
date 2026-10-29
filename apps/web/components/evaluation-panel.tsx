"use client";

import {useEffect, useState} from "react";
import {evaluationReportSchema, type EvaluationReport} from "../lib/contracts";

export default function EvaluationPanel() {
  const [report,setReport]=useState<EvaluationReport|null>(null);
  const [failed,setFailed]=useState(false);
  useEffect(()=>{
    let active=true;
    fetch("/evidence/m4-evaluation-status.json",{cache:"no-store"})
      .then(response=>{
        if(!response.ok) throw new Error("EVALUATION_REPORT_UNAVAILABLE");
        return response.json();
      })
      .then(value=>evaluationReportSchema.parse(value))
      .then(value=>{if(active)setReport(value);})
      .catch(()=>{if(active)setFailed(true);});
    return ()=>{active=false;};
  },[]);
  if(failed) return <p className="error" role="alert">Evaluation status is unavailable or invalid.</p>;
  if(!report) return <p className="muted" role="status">Loading evaluation inventory…</p>;
  const totals=report.totals;
  return <section className="evaluation-panel" aria-label="Evaluation results">
    <p className="badge neutral">{report.evidence_authority.replaceAll("_"," ")}</p>
    <h1>Evaluation status</h1>
    <p className="lede small">Inventory and accounting only. This report does not authenticate execution, oracle results, or verification gates.</p>
    <p role="status"><strong>{totals.attempted_cases} attempts · {totals.live_attempts} live</strong></p>
    <p>{totals.available_cases} available · {totals.unavailable_cases} unavailable · {totals.verified_oracle_correct} oracle-correct verified · {totals.false_verified_count} false-verified</p>
    {totals.attempted_cases===0&&<p className="blank-panel">No execution outcomes are available. Rates and cost remain unavailable because there are no eligible attempts.</p>}
    <div className="artifacts"><table><thead><tr><th>Case</th><th>Fixture</th><th>Availability</th><th>Attempts</th><th>Note</th></tr></thead><tbody>
      {report.cases.map(item=><tr key={item.case_id}><td><span className="mobile-field-label">Case</span>{item.case_id}</td><td><span className="mobile-field-label">Fixture</span><code>{item.fixture_id}</code></td><td><span className="mobile-field-label">Availability</span>{item.available?"Available":"Unavailable"}</td><td><span className="mobile-field-label">Attempts</span>{item.attempted_cases}</td><td><span className="mobile-field-label">Note</span>{item.unavailable_reason??(item.available?"Present; execution not implied":"—")}</td></tr>)}
    </tbody></table></div>
    {report.attempts.length>0&&<div className="artifacts"><h2>Attempt details</h2><table><thead><tr><th>Case / trace</th><th>Origin</th><th>Status</th><th>Failure category</th><th>Cost estimate</th><th>Latency</th></tr></thead><tbody>
      {report.attempts.map((attempt,index)=><tr key={`${attempt.trace_id??"unattempted"}-${index}`}><td><span className="mobile-field-label">Case / trace</span><code>{attempt.case_id}<br/>{attempt.trace_id??"No execution trace"}</code></td><td><span className="mobile-field-label">Origin</span>{attempt.origin}</td><td><span className="mobile-field-label">Status</span>{attempt.status}</td><td><span className="mobile-field-label">Failure category</span>{attempt.failure_category??"—"}</td><td><span className="mobile-field-label">Cost estimate</span>{attempt.provider_estimated_cost_usd===null?"Not reported":`$${attempt.provider_estimated_cost_usd.toFixed(4)}`}</td><td><span className="mobile-field-label">Latency</span>{attempt.latency_seconds===null?"Not reported":`${attempt.latency_seconds.toFixed(2)} s`}</td></tr>)}
    </tbody></table></div>}
    <p className="muted">Benchmark totals are unauthenticated aggregates. Only separately validated durable receipts can support production verification.</p>
    <a className="secondary" href="/">Back to workbench</a>
  </section>;
}
