export const DEFAULT_PAGE_SIZE = 10;

const MANAGED_QUERY_KEYS = new Set([
  "page",
  "page_size",
  "search",
  "provider",
  "include_working_dir",
  "model",
  "model_prefix",
  "model_match",
  "model_provider",
]);

export function createListState(pageSize = DEFAULT_PAGE_SIZE) {
  return {
    providers: [],
    activeProviders: new Set(),
    models: [],
    workingDirs: [],
    selectedWorkingDirs: new Set(),
    page: 1,
    pageSize,
    totalPages: 0,
    search: "",
    modelValue: "",
    modelMatchMode: "prefix",
    modelProvider: "",
    pendingProviderFilters: null,
    pendingWorkingDirFilters: null,
    queryPassthrough: new URLSearchParams(),
    busy: false,
    pendingRefresh: false,
  };
}

export function hydrateStateFromUrl(state, search = window.location.search) {
  const params = new URLSearchParams(search);
  state.page = _coercePositiveInt(params.get("page"), 1);
  state.search = (params.get("search") || "").trim();
  state.modelProvider = (params.get("model_provider") || "").trim();
  if (state.search === "undefined") {
    state.search = "";
  }
  if (state.modelProvider === "undefined") {
    state.modelProvider = "";
  }

  const modelExact = params.getAll("model").filter(Boolean);
  const modelPrefixes = params.getAll("model_prefix").filter(Boolean);
  const modelMatch = (params.get("model_match") || "").trim().toLowerCase();
  if (modelPrefixes.length > 0) {
    state.modelValue = modelPrefixes[0];
    state.modelMatchMode = "prefix";
  } else if (modelExact.length > 0) {
    state.modelValue = modelExact[0];
    state.modelMatchMode = modelMatch === "prefix" ? "prefix" : "exact";
  } else {
    state.modelValue = "";
    state.modelMatchMode = modelMatch === "exact" ? "exact" : "prefix";
  }
  if (state.modelValue === "undefined") state.modelValue = "";

  const providerFilters = new Set(params.getAll("provider").filter(Boolean));
  const workingDirFilters = new Set(
    params.getAll("include_working_dir").filter(Boolean)
  );
  state.pendingProviderFilters =
    providerFilters.size > 0 ? providerFilters : null;
  state.pendingWorkingDirFilters =
    workingDirFilters.size > 0 ? workingDirFilters : null;

  state.queryPassthrough = new URLSearchParams();
  params.forEach((value, key) => {
    if (!MANAGED_QUERY_KEYS.has(key)) {
      state.queryPassthrough.append(key, value);
    }
  });
}

export function buildSessionQuery(state) {
  const params = new URLSearchParams(state.queryPassthrough);
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));

  if (state.search) {
    params.set("search", state.search);
  }

  const modelTerm = (state.modelValue || "").trim();
  const modelMatch = state.modelMatchMode === "exact" ? "exact" : "prefix";
  if (modelTerm) {
    params.set("model_match", modelMatch);
    if (modelMatch === "exact") {
      params.append("model", modelTerm);
    } else {
      params.append("model_prefix", modelTerm);
    }
  }
  if (state.modelProvider) {
    params.set("model_provider", state.modelProvider);
  }

  let providers = Array.from(state.activeProviders);
  if (
    providers.length === 0 &&
    state.providers.length === 0 &&
    state.pendingProviderFilters
  ) {
    providers = Array.from(state.pendingProviderFilters);
  }
  providers.sort((a, b) => a.localeCompare(b));
  if (
    providers.length &&
    (state.providers.length === 0 || providers.length !== state.providers.length)
  ) {
    providers.forEach((id) => params.append("provider", id));
  }

  const workingDirCount = state.workingDirs.length;
  let included = Array.from(state.selectedWorkingDirs);
  if (
    included.length === 0 &&
    workingDirCount === 0 &&
    state.pendingWorkingDirFilters
  ) {
    included = Array.from(state.pendingWorkingDirFilters);
  }
  included.sort((a, b) => a.localeCompare(b));
  if (included.length > 0 && included.length < workingDirCount) {
    included.forEach((dir) => params.append("include_working_dir", dir));
  } else if (workingDirCount === 0 && included.length > 0) {
    included.forEach((dir) => params.append("include_working_dir", dir));
  }

  return params;
}

export function buildSearchHitsQuery(state, limit = 8) {
  const params = buildSessionQuery(state);
  params.delete("page");
  params.delete("page_size");
  if (limit) {
    params.set("limit", String(limit));
  }
  return params;
}

export function syncUrlFromState(state) {
  const params = buildSessionQuery(state);
  const query = params.toString();
  const nextUrl = query ? `${window.location.pathname}?${query}` : window.location.pathname;
  const current = `${window.location.pathname}${window.location.search}`;
  if (nextUrl !== current) {
    window.history.replaceState(null, "", nextUrl);
  }
}

function _coercePositiveInt(value, fallback) {
  if (!value) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return parsed;
}
