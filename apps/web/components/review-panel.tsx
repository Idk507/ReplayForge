"use client";

import {useRef, useState} from "react";
import {z} from "zod";
import {request, errorMessage} from "../lib/client";
import {ref, uuid, type Run} from "../lib/contracts";

const bindingSchema = z.object({run_id:uuid, repository_id:uuid, owner_id:uuid,
  source_commit:z.string().regex(/^[a-f0-9]{40}$/), patch_ref:ref, verification_ref:ref,
  target_branch:z.string().min(1).max(120), policy_version:z.string()});
const receiptSchema = z.object({binding:bindingSchema, reviewer_id:uuid,
  decision:z.enum(["approve","reject"]), nonce:uuid, issued_at:z.iso.datetime({offset:true}),
  expires_at:z.iso.datetime({offset:true}), can_authorize_publication:z.literal(false)});
const reviewSchema = z.object({binding:bindingSchema, expected_version:z.number().int().nonnegative(),
  receipt:receiptSchema.nullable(), publication_version:z.number().int().nonnegative().nullable()});
const publicationSchema=z.object({html_url:z.string().regex(/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/[1-9][0-9]*$/),origin:z.literal("REMOTE_SERVICE")});
type Review=z.infer<typeof reviewSchema>;

export default function ReviewPanel({run,diff}:{run:Run;diff:string}) {
  const [review,setReview]=useState<Review|null>(null),[checked,setChecked]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[url,setUrl]=useState("");
  const nonce=useRef<string|null>(null);
  async function load() {
    const value=await request(`runs/${run.run_id}/review`,reviewSchema);
    const digest=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(diff))),b=>b.toString(16).padStart(2,"0")).join("");
    if(value.binding.run_id!==run.run_id || value.binding.repository_id!==run.repository_id ||
      value.binding.source_commit!==run.source_commit || value.binding.verification_ref!==run.verification_ref ||
      value.binding.patch_ref!==`sha256:${digest}`) throw new Error("REVIEW_CHANGED");
    setReview(value); setChecked(false);return value;
  }
  async function act(action:"load"|"approve"|"reject"|"publish") {
    setBusy(true);setError("");
    try {
      if(action==="load") {await load();return;}
      if(!review || !checked) return;
      if(action==="publish") {
        if(review.receipt?.decision!=="approve" || review.publication_version===null) return;
        const result=await request(`runs/${run.run_id}/publication`,publicationSchema,{method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({binding:review.binding,nonce:review.receipt.nonce,expected_version:review.publication_version})});
        setUrl(result.html_url);return;
      }
      nonce.current??=crypto.randomUUID();
      const receipt=await request(`runs/${run.run_id}/approval`,receiptSchema,{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({binding:review.binding,expected_version:review.expected_version,decision:action,nonce:nonce.current})});
      if(JSON.stringify(receipt.binding)!==JSON.stringify(review.binding)) throw new Error("REVIEW_CHANGED");
      await load();
    } catch(e) {setError(errorMessage(e));}
    finally {setBusy(false);}
  }
  return <section className="review-panel" aria-label="Human review">
    <h3>Human review</h3><p>Review the patch and evidence before recording your decision. Creating a draft PR is a separate action.</p>
    <button disabled={busy} onClick={()=>void act("load")}>Load exact review</button>
    {error && <p role="alert">{error} A submitted action may be unconfirmed; load the recorded review before retrying.</p>}
    {review && <><dl className="review-bindings"><dt>Source commit</dt><dd><code>{review.binding.source_commit}</code></dd>
      <dt>Patch SHA256</dt><dd><code>{review.binding.patch_ref}</code></dd><dt>Verification</dt><dd><code>{review.binding.verification_ref}</code></dd>
      <dt>Target branch</dt><dd>{review.binding.target_branch}</dd></dl>
      {review.receipt && <p role="status">Decision recorded: {review.receipt.decision}. Expires {new Date(review.receipt.expires_at).toLocaleString()}.</p>}
      {!url && <label><input type="checkbox" checked={checked} disabled={busy} onChange={e=>setChecked(e.target.checked)}/> I reviewed this exact patch, evidence and target branch.</label>}
      {!review.receipt && <div className="review-actions"><button disabled={busy||!checked} onClick={()=>void act("approve")}>Approve exact patch</button>
        <button className="danger" disabled={busy||!checked} onClick={()=>void act("reject")}>Reject patch</button></div>}
      {review.receipt?.decision==="approve" && !url && <button disabled={busy||!checked} onClick={()=>void act("publish")}>Create approved draft PR</button>}
      {url && <p role="status"><a href={url} target="_blank" rel="noreferrer">Open recorded draft PR ↗</a></p>}
    </>}
  </section>;
}
