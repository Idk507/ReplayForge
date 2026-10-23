import SessionGate from "../../../components/session-gate";
import { loginUrl } from "../../../lib/auth";
export const dynamic="force-dynamic";
export default function Success(){return <SessionGate enabled signIn={loginUrl()}><main id="main" className="auth-panel"><p className="eyebrow">REPLAYFORGE / ACCOUNT</p><h1>Signed in</h1><p>Choose a repository and explicitly connect a pinned commit to begin.</p><a className="primary" href="/settings/integrations">Connect a repository</a><p><a href="/">Open workbench</a></p></main></SessionGate>;}
