// Throughput and p95 of the gateway at increasing arrival rates against the in-cluster stub, until the objective breaks.
// Every question is unique, so each request takes the full path: tenant, quota, embedding, cache miss, stub, settle, meter.
import http from "k6/http";
import { check } from "k6";

const base = __ENV.GATEWAY_URL;
const prefix = __ENV.ROUTE_PREFIX || "";
const token = __ENV.TENANT_TOKEN;
const results = __ENV.RESULTS_DIR || "load/results";

export const options = {
  scenarios: {
    ramp: {
      executor: "ramping-arrival-rate",
      startRate: 5,
      timeUnit: "1s",
      preAllocatedVUs: 50,
      maxVUs: 400,
      stages: [
        { target: 20, duration: "1m" },
        { target: 50, duration: "1m" },
        { target: 100, duration: "1m" },
        { target: 200, duration: "1m" },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{expected_response:true}": ["p(95)<500"],
  },
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
};

export default function () {
  const question = `What does Article ${__VU}-${__ITER} of the regulation require?`;
  const res = http.post(`${base}${prefix}/v1/answer`, JSON.stringify({ question }), {
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    tags: { scenario: "baseline" },
  });
  check(res, {
    "answered": (r) => r.status === 200,
    "stream ends with verify": (r) => typeof r.body === "string" && r.body.includes("event: verify"),
  });
}

export function handleSummary(data) {
  return { [`${results}/baseline.json`]: JSON.stringify(data, null, 2) };
}
