import { fetchJSON } from "./api.js";
import { buildSessionQuery } from "./state.js";

/**
 * Fetch providers, sessions, and working directory metadata for the landing page.
 */
export async function fetchBootstrapData(state) {
  const params = buildSessionQuery(state);
  const [providers, models, sessions, workingDirs] = await Promise.allSettled([
    fetchJSON("/api/providers"),
    fetchJSON("/api/models"),
    fetchJSON(`/api/sessions?${params.toString()}`),
    fetchJSON("/api/working-dirs"),
  ]);
  return { providers, models, sessions, workingDirs };
}

/**
 * Fetch the current page of sessions based on list state.
 */
export async function fetchSessions(state) {
  const params = buildSessionQuery(state);
  return fetchJSON(`/api/sessions?${params.toString()}`);
}
