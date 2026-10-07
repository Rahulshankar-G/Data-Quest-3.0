export type JsonRecord = Record<string, unknown>;

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path.startsWith("/") ? path : `/api/adpilot/${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`AdPilot API ${response.status}: ${message}`);
  }
  return response.json() as Promise<T>;
}
