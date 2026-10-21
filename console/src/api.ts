export type Plan = "starter" | "business" | "enterprise";

export interface Config {
  tenant_id: string;
  client_id: string;
}

export interface Isolation {
  namespace: string;
  index: string;
  database: string;
  container: string;
}

export interface TenantRow {
  id: string;
  name: string;
  plan: Plan;
  pii: string;
  status: string;
  created: string;
  suspended: boolean;
  isolation: Isolation;
}

export interface TenantDocument extends TenantRow {
  steps: Record<string, string>;
  sealed_credential?: string;
}

export interface Order {
  name: string;
  plan: Plan;
  pii: string;
  seal: Record<string, string>;
}

interface Problem {
  title?: string;
  detail?: string;
}

async function call<T>(path: string, token: string | null, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const problem = (await response.json().catch(() => ({}))) as Problem;
    throw new Error(problem.detail ?? `${response.status} on ${path}`);
  }
  return (await response.json()) as T;
}

export const getConfig = (): Promise<Config> => call<Config>("/console/config.json", null);

export const listTenants = (token: string): Promise<TenantRow[]> => call<TenantRow[]>("/admin/tenants", token);

export const getTenant = (token: string, id: string): Promise<TenantDocument> =>
  call<TenantDocument>(`/admin/tenants/${encodeURIComponent(id)}`, token);

export const onboard = (token: string, order: Order): Promise<{ tenant: string; status: string }> =>
  call("/admin/tenants", token, { method: "POST", body: JSON.stringify(order) });
