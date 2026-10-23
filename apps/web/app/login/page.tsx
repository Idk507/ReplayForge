import SessionGate from "../../components/session-gate";
import { loginUrl } from "../../lib/auth";
export const dynamic="force-dynamic";
export default function Login(){
  const enabled=process.env.REPLAYFORGE_WEB_AUTH_MODE==="github";
  return enabled?<SessionGate enabled signIn={loginUrl()}><main id="main" className="auth-panel"><h1>You are signed in</h1><a className="primary" href="/">Open workbench</a></main></SessionGate>:<main id="main" className="auth-panel"><h1>Local development mode</h1><p>GitHub login is not enabled on this server.</p><a href="/">Open local workbench</a></main>;
}
