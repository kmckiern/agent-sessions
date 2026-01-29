export async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { Accept: "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      (payload && payload.error) ||
      `Request failed with status ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

export function createSessionLink(session) {
  const link = new URL("/session.html", window.location.origin);
  if (session.provider) {
    link.searchParams.set("provider", session.provider);
  }
  if (session.session_id) {
    link.searchParams.set("session", session.session_id);
  }
  if (session.source_path) {
    link.searchParams.set("source_path", session.source_path);
  }
  return link.toString();
}
