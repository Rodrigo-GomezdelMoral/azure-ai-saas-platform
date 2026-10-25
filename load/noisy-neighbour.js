// One Starter tenant floods the gateway while a Business tenant runs a steady load. The protected tenant's p95 is the
// result, read from the `protected` scenario's tag. Run it twice: ENFORCE=on (the default deployment) and ENFORCE=off
// (the starter gateway deployed with ENFORCE_QUOTA=false), and publish both numbers side by side; that comparison is
// the proof that isolation works.
import http from "k6/http";
import { check } from "k6";

const base = __ENV.GATEWAY_URL;
const label = __ENV.ENFORCE || "on";
const results = __ENV.RESULTS_DIR || "load/results";
const noisy = { token: __ENV.NOISY_TOKEN, prefix: __ENV.NOISY_PREFIX || "" };
const protectedTenant = { token: __ENV.PROTECTED_TOKEN, prefix: __ENV.PROTECTED_PREFIX };

export const options = {
  scenarios: {
    noisy: {
      executor: "constant-arrival-rate",
      rate: 100,
      timeUnit: "1s",
      duration: "3m",
      preAllocatedVUs: 100,
      maxVUs: 600,
      exec: "flood",
    },
    protected: {
      executor: "constant-arrival-rate",
      rate: 1,
      timeUnit: "1s",
      duration: "3m",
      preAllocatedVUs: 10,
      maxVUs: 50,
      exec: "steady",
    },
  },
  thresholds: {
    "http_req_duration{scenario:protected,expected_response:true}": ["p(95)<500"],
    "http_req_failed{scenario:protected}": ["rate<0.01"],
  },
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
};

function ask(tenant, who) {
  const question = `What does Article ${__VU}-${__ITER} require for ${who}?`;
  return http.post(`${base}${tenant.prefix}/v1/answer`, JSON.stringify({ question }), {
    headers: { Authorization: `Bearer ${tenant.token}`, "Content-Type": "application/json" },
  });
}

export function flood() {
  const res = ask(noisy, "the noisy tenant");
  check(res, { "answered or rejected at the door": (r) => r.status === 200 || r.status === 429 });
}

export function steady() {
  const res = ask(protectedTenant, "the protected tenant");
  check(res, { "protected tenant answered": (r) => r.status === 200 });
}

export function handleSummary(data) {
  return { [`${results}/noisy-neighbour-${label}.json`]: JSON.stringify(data, null, 2) };
}
