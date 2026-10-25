const BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem("ds_token");
}

export function setToken(token: string) {
  sessionStorage.setItem("ds_token", token);
}

export function clearToken() {
  sessionStorage.removeItem("ds_token");
}

async function request(path: string, options: RequestInit = {}) {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${BASE}${path}`, { ...options, headers });
  if (res.status === 401 && typeof window !== "undefined") {
    clearToken();
    window.location.href = "/login";
    throw new Error("Session expired");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  login: async (email: string, password: string) => {
    const form = new URLSearchParams({ username: email, password });
    const res = await fetch(`${BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form,
    });
    if (!res.ok) throw new Error("Invalid credentials");
    return res.json();
  },
  register: (org_name: string, email: string, password: string) =>
    request("/auth/register", { method: "POST", body: JSON.stringify({ org_name, email, password }) }),
  chat: (query: string, session_id?: number) =>
    request("/chat", { method: "POST", body: JSON.stringify({ query, session_id }) }),
  feedback: (metric_id: number, feedback: number) =>
    request("/chat/feedback", { method: "POST", body: JSON.stringify({ metric_id, feedback }) }),
  documents: () => request("/documents"),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request("/documents", { method: "POST", body: form });
  },
  deleteDocument: (id: number) => request(`/documents/${id}`, { method: "DELETE" }),
  metricsOverview: () => request("/metrics/overview"),
  metricsTimeseries: () => request("/metrics/timeseries"),
  metricsRecent: () => request("/metrics/recent"),
};
