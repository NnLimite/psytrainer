// Клиент API: токен в памяти, refresh-cookie, повтор запроса после 401, ошибки problem+json, SSE через fetch.
const BASE: string = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";
let token: string | null = null;
export const setToken = (t: string | null) => { token = t; };

export type State = { trust: number; anxiety: number; resistance: number; openness: number };
export type StreamEvent =
  | { type: "token"; text: string }
  | { type: "done"; reply: string; state: State | null; triggers: string[] | null; notice?: { code: string; text: string } | null }
  | { type: "ended" } | { type: "out_of_role" } | { type: "error"; message: string };

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public errors?: { field: string; message: string }[]) { super(message); }
}
export const errMsg = (e: unknown) =>
  e instanceof ApiError ? (e.errors?.length ? e.errors.map((x) => `${x.field}: ${x.message}`).join("; ") : e.message) : "Сервер недоступен";

async function toError(r: Response) {
  const p = await r.json().catch(() => ({}));
  return new ApiError(r.status, p.code ?? "error", p.detail ?? p.title ?? r.statusText, p.errors);
}
function raw(path: string, init: RequestInit = {}) {
  const h = new Headers(init.headers);
  if (init.body && !h.has("Content-Type")) h.set("Content-Type", "application/json");
  if (token) h.set("Authorization", `Bearer ${token}`);
  return fetch(BASE + path, { ...init, headers: h, credentials: "include" });
}
async function withRefresh(path: string, init?: RequestInit) {
  let r = await raw(path, init);
  if (r.status === 401 && !path.startsWith("/auth/") && (await refresh())) r = await raw(path, init);
  return r;
}

let pending: Promise<boolean> | null = null;
/** Один общий запрос: refresh-токен одноразовый, два параллельных вызова отозвали бы всю цепочку (важно для React StrictMode). */
export function refresh(): Promise<boolean> {
  pending ??= fetch(BASE + "/auth/refresh", { method: "POST", credentials: "include" })
    .then(async (r) => { if (!r.ok) { token = null; return false; } token = (await r.json()).access_token; return true; })
    .catch(() => false).finally(() => { pending = null; });
  return pending;
}

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const r = await withRefresh(path, init);
  if (!r.ok) throw await toError(r);
  return r.status === 204 ? (undefined as T) : r.json();
}
const send = (method: string) => <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
export const post = send("POST"), put = send("PUT"), patch = send("PATCH"), del = send("DELETE");

export async function download(path: string, filename: string) {
  const r = await withRefresh(path);
  if (!r.ok) throw await toError(r);
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(await r.blob()), download: filename });
  a.click(); URL.revokeObjectURL(a.href);
}

/** Реплика студента, ответ пациента потоком SSE. Ошибки проверок (404/409/422) бросаются до первого события. */
export async function* streamMessage(sessionId: string, text: string): AsyncGenerator<StreamEvent> {
  const body = JSON.stringify({ text, client_message_id: crypto.randomUUID() });
  const r = await withRefresh(`/sessions/${sessionId}/messages/stream`, { method: "POST", body });
  if (!r.ok) throw await toError(r);
  const reader = r.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value;
    for (let i = buf.indexOf("\n\n"); i >= 0; i = buf.indexOf("\n\n")) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      if (block.startsWith("data: ")) yield JSON.parse(block.slice(6)) as StreamEvent; // строки ": ping" игнорируем
    }
  }
}

/** GET/PUT с ETag: версии кейса правятся через If-Match (на 412 перечитайте версию). */
export async function getWithEtag<T>(path: string): Promise<{ data: T; etag: string | null }> {
  const r = await withRefresh(path);
  if (!r.ok) throw await toError(r);
  return { data: await r.json(), etag: r.headers.get("ETag") };
}
export async function putIfMatch<T>(path: string, body: unknown, etag: string): Promise<{ data: T; etag: string | null }> {
  const r = await withRefresh(path, { method: "PUT", body: JSON.stringify(body), headers: { "If-Match": etag } });
  if (!r.ok) throw await toError(r);
  return { data: await r.json(), etag: r.headers.get("ETag") };
}
/** Фоновая задача (оценка, отчёт, выгрузка): опрос до завершения. Возвращает job. */
export async function waitJob(id: string): Promise<{ id: string; status: string; result_url: string | null; error: string | null }> {
  for (;;) {
    const j = await api<{ id: string; status: string; result_url: string | null; error: string | null }>(`/jobs/${id}`);
    if (!["queued", "running"].includes(j.status)) return j;
    await new Promise((r) => setTimeout(r, 1500));
  }
}
