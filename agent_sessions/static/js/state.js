export const DEFAULT_PAGE_SIZE = 10;

export function createListState(pageSize = DEFAULT_PAGE_SIZE) {
  return {
    providers: [],
    activeProviders: new Set(),
    workingDirs: [],
    selectedWorkingDirs: new Set(),
    page: 1,
    pageSize,
    totalPages: 0,
    search: "",
    busy: false,
    pendingRefresh: false,
  };
}

export function buildSessionQuery(state) {
  const params = new URLSearchParams();
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  if (state.search) {
    params.set("search", state.search);
  }

  const providers = Array.from(state.activeProviders);
  if (providers.length && providers.length !== state.providers.length) {
    providers.forEach((id) => params.append("provider", id));
  }

  const workingDirCount = state.workingDirs.length;
  const included = Array.from(state.selectedWorkingDirs);
  if (included.length > 0 && included.length < workingDirCount) {
    included.forEach((dir) => params.append("include_working_dir", dir));
  } else if (workingDirCount === 0 && included.length > 0) {
    included.forEach((dir) => params.append("include_working_dir", dir));
  }

  return params;
}
