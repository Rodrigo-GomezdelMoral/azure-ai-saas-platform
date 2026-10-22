// Ten tenants onboarded at once through the admin API; each virtual user waits for its tenant to become ready and
// records the time it took. Starter by default, because a dedicated plan creates real Azure objects per tenant.
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const base = __ENV.GATEWAY_URL;
const token = __ENV.ADMIN_TOKEN;
const plan = __ENV.PLAN || "starter";
const run = __ENV.RUN_ID || `${Date.now()}`.slice(-6);
const results = __ENV.RESULTS_DIR || "load/results";
const timeToReady = new Trend("onboarding_time_to_ready", true);
const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

export const options = {
  scenarios: {
    tenants: { executor: "per-vu-iterations", vus: 10, iterations: 1, maxDuration: "15m" },
  },
  thresholds: {
    onboarding_time_to_ready: ["p(95)<120000"],
    checks: ["rate>0.99"],
  },
  summaryTrendStats: ["avg", "p(50)", "p(95)", "max"],
};

export default function () {
  const name = `Load ${run} ${String(__VU).padStart(2, "0")}`;
  const started = Date.now();
  const queued = http.post(`${base}/admin/tenants`, JSON.stringify({ name, plan, pii: "none" }), { headers });
  check(queued, { "queued": (r) => r.status === 202 });
  const tenant = queued.json("tenant");
  for (let attempt = 0; attempt < 240; attempt++) {
    const status = http.get(`${base}/admin/tenants/${tenant}`, { headers, tags: { name: "status" } });
    const state = status.status === 200 ? status.json("status") : "missing";
    if (state === "ready" || state === "failed") {
      check(status, { "tenant ready": () => state === "ready" });
      timeToReady.add(Date.now() - started);
      return;
    }
    sleep(2);
  }
  check(null, { "tenant ready": () => false });
}

export function handleSummary(data) {
  return { [`${results}/onboarding.json`]: JSON.stringify(data, null, 2) };
}
