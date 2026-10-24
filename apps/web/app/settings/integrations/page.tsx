import SessionGate from "../../../components/session-gate";
import IntegrationPanel from "../../../components/integration-panel";
import { loginUrl, installationUrl } from "../../../lib/auth";
export const dynamic="force-dynamic";
export default function Integrations(){
  if(process.env.REPLAYFORGE_WEB_AUTH_MODE!=="github")return <main id="main" className="auth-panel"><h1>Local development mode</h1><p>GitHub repository connection is not enabled here.</p><a href="/">Back to workbench</a></main>;
  return <SessionGate enabled signIn={loginUrl()}><IntegrationPanel installUrl={installationUrl()}/></SessionGate>;
}
