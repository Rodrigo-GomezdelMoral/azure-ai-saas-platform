// Throughput and p95 of the gateway at five fixed arrival rates against the in-cluster stub, one after another.
// Every question is unique, so each request takes the full path: tenant, quota, embedding, cache miss, stub, settle,
// meter. One threshold per rate makes k6 report each rate's p95; the first rate that fails is the measured ceiling.
import http from "k6/http";
import { check } from "k6";

const base = __ENV.GATEWAY_URL;
const prefix = __ENV.ROUTE_PREFIX || "";
const token = __ENV.TENANT_TOKEN;
const results = __ENV.RESULTS_DIR || "load/results";
const RATES = [5, 10, 20, 40, 80];
const STEP = 45;
const name = (rate) => `rate_${String(rate).padStart(3, "0")}`;

export const options = {
  scenarios: Object.fromEntries(
    RATES.map((rate, i) => [
      name(rate),
      {
        executor: "constant-arrival-rate",
        rate,
        timeUnit: "1s",
        duration: `${STEP}s`,
        startTime: `${i * (STEP + 10)}s`,
        preAllocatedVUs: rate * 2,
        maxVUs: rate * 10,
        gracefulStop: "10s",
      },
    ]),
  ),
  thresholds: Object.fromEntries(
    RATES.flatMap((rate) => [
      [`http_req_duration{scenario:${name(rate)},expected_response:true}`, ["p(95)<500"]],
      [`http_req_failed{scenario:${name(rate)}}`, ["rate<0.01"]],
    ]),
  ),
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
};

export default function () {
  const question = `What does Article ${__VU}-${__ITER} of the regulation require?`;
  const res = http.post(`${base}${prefix}/v1/answer`, JSON.stringify({ question }), {
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  });
  check(res, {
    "answered": (r) => r.status === 200,
    "stream ends with verify": (r) => typeof r.body === "string" && r.body.includes("event: verify"),
  });
}

export function handleSummary(data) {
  return { [`${results}/baseline.json`]: JSON.stringify(data, null, 2) };
}
