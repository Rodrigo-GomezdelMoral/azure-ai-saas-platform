import { PublicClientApplication, type AccountInfo } from "@azure/msal-browser";
import { getConfig, getTenant, listTenants, onboard, type Order, type Plan, type TenantDocument, type TenantRow } from "./api";

const STEPS = ["register", "namespace", "federate", "index", "store", "bucket", "credential", "notify"];
const POLL_MS = 2000;

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`missing element ${id}`);
  return found as T;
}

const config = await getConfig();
const scopes = [`api://${config.client_id}/Console.Access`];
const msal = new PublicClientApplication({
  auth: {
    clientId: config.client_id,
    authority: `https://login.microsoftonline.com/${config.tenant_id}`,
    redirectUri: `${location.origin}/console/`,
  },
  cache: { cacheLocation: "sessionStorage" },
});
await msal.initialize();

async function account(): Promise<AccountInfo> {
  const known = msal.getActiveAccount() ?? msal.getAllAccounts()[0];
  if (known) {
    msal.setActiveAccount(known);
    return known;
  }
  const result = await msal.loginPopup({ scopes });
  msal.setActiveAccount(result.account);
  return result.account;
}

async function token(): Promise<string> {
  const current = await account();
  try {
    return (await msal.acquireTokenSilent({ scopes, account: current })).accessToken;
  } catch {
    return (await msal.acquireTokenPopup({ scopes, account: current })).accessToken;
  }
}

function cell(text: string): HTMLTableCellElement {
  const td = document.createElement("td");
  td.textContent = text;
  return td;
}

function renderTenants(rows: TenantRow[]): void {
  const body = element<HTMLTableSectionElement>("tenants");
  body.replaceChildren(
    ...rows.map((row) => {
      const tr = document.createElement("tr");
      const where = `${row.isolation.namespace} · ${row.isolation.database}/${row.isolation.container}`;
      tr.append(cell(row.id), cell(row.name), cell(row.plan), cell(row.pii), cell(where), cell(row.status));
      return tr;
    }),
  );
  element<HTMLElement>("count").textContent = `${rows.length} tenant${rows.length === 1 ? "" : "s"}`;
}

function renderSteps(doc: TenantDocument | null, elapsed: number): void {
  const list = element<HTMLOListElement>("steps");
  list.replaceChildren(
    ...STEPS.map((name) => {
      const li = document.createElement("li");
      const outcome = doc?.steps[name];
      li.className = outcome === undefined ? "pending" : outcome.startsWith("failed") ? "failed" : "done";
      li.textContent = `${name}: ${outcome ?? "…"}`;
      return li;
    }),
  );
  element<HTMLElement>("elapsed").textContent = `${elapsed.toFixed(1)} s`;
}

async function sealingKey(): Promise<{ pair: CryptoKeyPair; jwk: Record<string, string> }> {
  const algorithm = { name: "RSA-OAEP", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" };
  const pair = await crypto.subtle.generateKey(algorithm, true, ["encrypt", "decrypt"]);
  const exported = await crypto.subtle.exportKey("jwk", pair.publicKey);
  return { pair, jwk: { n: exported.n ?? "", e: exported.e ?? "" } };
}

async function open(sealed: string, pair: CryptoKeyPair): Promise<string> {
  const bytes = Uint8Array.from(atob(sealed), (char) => char.charCodeAt(0));
  const wrapped = bytes.slice(0, 256);
  const iv = bytes.slice(256, 268);
  const body = bytes.slice(268);
  const raw = await crypto.subtle.decrypt({ name: "RSA-OAEP" }, pair.privateKey, wrapped);
  const key = await crypto.subtle.importKey("raw", raw, { name: "AES-GCM" }, false, ["decrypt"]);
  return new TextDecoder().decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, body));
}

function reveal(credential: string): void {
  const box = element<HTMLElement>("credential");
  box.hidden = false;
  element<HTMLTextAreaElement>("secret").value = credential;
}

function say(message: string): void {
  element<HTMLElement>("message").textContent = message;
}

async function refresh(): Promise<void> {
  renderTenants(await listTenants(await token()));
  element<HTMLElement>("who").textContent = (await account()).username;
}

async function watch(tenantId: string, pair: CryptoKeyPair, started: number): Promise<void> {
  for (;;) {
    const doc = await getTenant(await token(), tenantId).catch(() => null);
    renderSteps(doc, (performance.now() - started) / 1000);
    if (doc?.status === "ready") {
      if (doc.sealed_credential) reveal(await open(doc.sealed_credential, pair));
      say(`${tenantId} is ready; the credential below is shown once and is not stored anywhere.`);
      return;
    }
    if (doc?.status === "failed") {
      say(`${tenantId} failed; the step above says why. Fix the cause and submit the same name again: every step converges.`);
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
  }
}

async function submit(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  const form = event.currentTarget as HTMLFormElement;
  const data = new FormData(form);
  const { pair, jwk } = await sealingKey();
  const order: Order = { name: String(data.get("name")), plan: data.get("plan") as Plan, pii: String(data.get("pii")), seal: jwk };
  element<HTMLElement>("credential").hidden = true;
  element<HTMLElement>("progress").hidden = false;
  renderSteps(null, 0);
  const started = performance.now();
  try {
    const queued = await onboard(await token(), order);
    say(`${queued.tenant} queued; the worker is provisioning it.`);
    await watch(queued.tenant, pair, started);
  } catch (error) {
    say(error instanceof Error ? error.message : String(error));
  }
  await refresh();
}

element<HTMLFormElement>("onboard").addEventListener("submit", (event) => void submit(event));
element<HTMLButtonElement>("copy").addEventListener("click", () => {
  void navigator.clipboard.writeText(element<HTMLTextAreaElement>("secret").value).then(() => say("copied"));
});
element<HTMLButtonElement>("signin").addEventListener("click", () => void refresh().catch((error) => say(String(error))));
if (msal.getAllAccounts().length > 0) await refresh();
