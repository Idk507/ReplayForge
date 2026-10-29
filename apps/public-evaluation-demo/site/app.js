const reportUrl = "/evidence/m4-evaluation-status.json";
const safeCount = (value) => Number.isInteger(value) && value >= 0;
const display = (value) => value === null || value === undefined ? "Not available" : String(value);
const node = (tag, value, className) => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value ?? "—";
  return element;
};
const showError = () => {
  document.querySelector("#loading").hidden = true;
  document.querySelector("#failure").hidden = false;
};
function validReport(report) {
  if (!report || report.schema_version !== "replayforge.m4-evaluation-report.v2" ||
      report.evidence_authority !== "UNAUTHENTICATED_AGGREGATE" ||
      !Array.isArray(report.cases) || !Array.isArray(report.attempts) || !report.totals) return false;
  const countFields = ["available_cases", "unavailable_cases", "attempted_cases", "live_attempts", "test_transport_attempts", "local_static_attempts", "reproduced_baselines", "verified_oracle_correct", "false_verified_count", "inconclusive_count", "patch_scope_rejections", "latency_sample_count"];
  return countFields.every((field) => safeCount(report.totals[field])) &&
    report.cases.every((item) => item && typeof item.case_id === "string" && typeof item.fixture_id === "string" && typeof item.available === "boolean" && safeCount(item.attempted_cases)) &&
    report.attempts.every((item) => item && typeof item.case_id === "string" && typeof item.origin === "string" && typeof item.status === "string");
}
function render(report) {
  const totals = report.totals;
  document.querySelector("#attempted").textContent = totals.attempted_cases;
  document.querySelector("#live-label").textContent = `${totals.live_attempts} live`;
  document.querySelector("#available").textContent = totals.available_cases;
  document.querySelector("#unavailable-label").textContent = `${totals.unavailable_cases} unavailable`;
  document.querySelector("#correct").textContent = totals.verified_oracle_correct;
  document.querySelector("#false-verified").textContent = totals.false_verified_count;
  document.querySelector("#case-count").textContent = `${report.cases.length} cases`;
  document.querySelector("#no-attempts").hidden = totals.attempted_cases !== 0;
  const cases = document.querySelector("#cases");
  for (const item of report.cases) {
    const row = document.createElement("tr");
    row.append(node("td", item.case_id));
    const fixture = node("td"); fixture.append(node("code", item.fixture_id)); row.append(fixture);
    const availability = node("td"); availability.append(node("span", item.available ? "Available" : "Unavailable", `status ${item.available ? "available" : "unavailable"}`)); row.append(availability);
    row.append(node("td", item.attempted_cases));
    row.append(node("td", item.unavailable_reason ?? (item.available ? "Present; execution not implied" : "—")));
    cases.append(row);
  }
  if (report.attempts.length) {
    const section = document.querySelector("#attempt-section");
    section.hidden = false;
    const body = document.querySelector("#attempts");
    for (const item of report.attempts) {
      const row = document.createElement("tr");
      row.append(node("td", `${item.case_id} · ${item.trace_id ?? "No execution trace"}`));
      row.append(node("td", item.origin)); row.append(node("td", item.status));
      row.append(node("td", item.failure_category ?? "—"));
      row.append(node("td", item.provider_estimated_cost_usd === null ? "Not reported" : `$${Number(item.provider_estimated_cost_usd).toFixed(4)}`));
      row.append(node("td", item.latency_seconds === null ? "Not reported" : `${Number(item.latency_seconds).toFixed(2)} s`));
      body.append(row);
    }
  }
  document.querySelector("#loading").hidden = true;
  document.querySelector("#report").hidden = false;
}
fetch(reportUrl, {cache: "no-store", credentials: "omit", redirect: "error"})
  .then((response) => { if (!response.ok) throw new Error("report unavailable"); return response.json(); })
  .then((report) => { if (!validReport(report)) throw new Error("invalid report"); render(report); })
  .catch(showError);