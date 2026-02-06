import {
  DEFAULT_PAGE_SIZE,
  createListState,
  hydrateStateFromUrl,
  syncUrlFromState,
} from "./state.js";
import { initThemeToggle, revealApp, escapeHtml } from "./ui.js";
import { fetchBootstrapData, fetchSearchHits, fetchSessions } from "./index-data.js";
import { createListRenderer } from "./index-render.js";
import { initFilterControls } from "./index-filters.js";
import { createSessionLink } from "./api.js";

const PAGE_SIZE = DEFAULT_PAGE_SIZE;

function collectDomRefs() {
  const dom = {
    providerToolbar: document.getElementById("provider-toolbar"),
    modelToolbar: document.getElementById("model-toolbar"),
    modelOptions: document.getElementById("model-options"),
    searchInput: document.getElementById("session-search"),
    searchPanel: document.getElementById("search-panel"),
    searchPanelList: document.getElementById("search-panel-list"),
    searchPanelMeta: document.getElementById("search-panel-meta"),
    searchPanelEmpty: document.getElementById("search-panel-empty"),
    modelInput: document.getElementById("model-filter"),
    modelMatchMode: document.getElementById("model-match-mode"),
    modelProviderFilter: document.getElementById("model-provider-filter"),
    resetFilters: document.getElementById("filters-reset"),
    activeFilters: document.getElementById("active-filters"),
    resultsCount: document.getElementById("results-count"),
    filtersGroupToggle: document.getElementById("filters-group-toggle"),
    filtersGroupBody: document.getElementById("filters-group-body"),
    tableBody: document.getElementById("sessions-body"),
    pagePrev: document.getElementById("page-prev"),
    pageNext: document.getElementById("page-next"),
    pageInfo: document.getElementById("page-info"),
    workingDirList: document.getElementById("working-dir-list"),
    workingDirToggle: document.getElementById("working-dir-toggle"),
    providerToggle: document.getElementById("provider-toggle"),
    modelToggle: document.getElementById("model-toggle"),
    providerBody: document.getElementById("provider-body"),
    providerHeader: document.getElementById("provider-header"),
    modelBody: document.getElementById("model-body"),
    modelHeader: document.getElementById("model-header"),
    workingDirBody: document.getElementById("working-dir-body"),
    workingDirHeader: document.getElementById("working-dir-header"),
  };
  const requiredKeys = Object.keys(dom).filter((key) => key !== "resetFilters");
  if (requiredKeys.some((key) => !dom[key])) {
    return null;
  }
  return dom;
}

class IndexController {
  constructor(dom) {
    this.dom = dom;
    this.state = createListState(PAGE_SIZE);
    hydrateStateFromUrl(this.state);
    this.renderer = createListRenderer(dom, this.state);

    this.onWorkingDirToggle = this.handleWorkingDirToggle.bind(this);
    this.onProviderToggle = this.handleProviderToggle.bind(this);
    this.onModelQuickSelect = this.handleModelQuickSelect.bind(this);

    this.searchHits = [];
    this.searchPanelOpen = false;
    this.searchPanelSuppressed = false;
    this.searchPanelStatus = "idle";
    this.searchSelection = -1;
    this.searchPanelHasMore = false;
    this.searchHitsCache = new Map();
    this.searchHitsRequestId = 0;

    this.onSearchInputKeydown = this.handleSearchInputKeydown.bind(this);
    this.onSearchHitClick = this.handleSearchHitClick.bind(this);
    this.onSearchHitHover = this.handleSearchHitHover.bind(this);

    this.dom.workingDirList.innerHTML =
      '<span class="filter-empty">Loading directories…</span>';
    this.dom.workingDirToggle.disabled = true;
    this.renderer.updateResultsCount(0);
    this.renderer.renderActiveFilters();

    this.bindSearchPanelEvents();
  }

  syncWorkingDirState(entries) {
    const pending = this.state.pendingWorkingDirFilters;
    const previousSelection = new Set(this.state.selectedWorkingDirs);
    const previousTotal = this.state.workingDirs.length;
    const hadSelection = previousSelection.size > 0;
    const hadAllSelected =
      previousTotal > 0 && previousSelection.size === previousTotal;

    this.state.workingDirs = entries;

    if (pending) {
      const nextSelection = new Set();
      entries.forEach((entry) => {
        if (pending.has(entry.path)) {
          nextSelection.add(entry.path);
        }
      });
      this.state.selectedWorkingDirs = nextSelection;
      this.state.pendingWorkingDirFilters = null;
      return;
    }

    if (!hadSelection || hadAllSelected) {
      this.state.selectedWorkingDirs = new Set(
        entries.map((entry) => entry.path)
      );
      return;
    }
    const nextSelection = new Set();
    entries.forEach((entry) => {
      if (previousSelection.has(entry.path)) {
        nextSelection.add(entry.path);
      }
    });
    this.state.selectedWorkingDirs = nextSelection;
  }

  refreshFilterChrome({ includeWorkingDirs = false } = {}) {
    this.renderer.renderProviders(this.onProviderToggle);
    this.renderer.renderModels(this.onModelQuickSelect);
    this.renderer.renderModelProviderOptions();
    this.renderer.renderActiveFilters();
    if (includeWorkingDirs) {
      this.renderer.renderWorkingDirs(this.onWorkingDirToggle);
    } else {
      this.renderer.updateWorkingDirToggleButton();
    }

    this.dom.modelMatchMode.value =
      this.state.modelMatchMode === "exact" ? "exact" : "prefix";
    this.dom.modelProviderFilter.value = this.state.modelProvider || "";
    this.dom.modelInput.value = this.state.modelValue || "";
    this.dom.searchInput.value = this.state.search || "";
  }

  bindSearchPanelEvents() {
    if (this.dom.searchInput) {
      this.dom.searchInput.addEventListener(
        "keydown",
        this.onSearchInputKeydown
      );
      this.dom.searchInput.addEventListener("focus", () => {
        if (this.state.search && !this.searchPanelSuppressed) {
          this.renderSearchPanel();
        }
      });
    }

    if (this.dom.searchPanelList) {
      this.dom.searchPanelList.addEventListener("click", this.onSearchHitClick);
      this.dom.searchPanelList.addEventListener(
        "mousemove",
        this.onSearchHitHover
      );
    }
  }

  setSearchPanelOpen(isOpen) {
    this.searchPanelOpen = isOpen;
    if (!this.dom.searchPanel) {
      return;
    }
    this.dom.searchPanel.classList.toggle("is-open", isOpen);
    this.dom.searchPanel.setAttribute("aria-hidden", String(!isOpen));
    if (this.dom.searchInput) {
      this.dom.searchInput.setAttribute("aria-expanded", String(isOpen));
    }
  }

  shouldShowSearchPanel() {
    return Boolean(this.state.search) && !this.searchPanelSuppressed;
  }

  clearSearchHits() {
    this.searchHits = [];
    this.searchPanelStatus = "idle";
    this.searchSelection = -1;
    this.searchPanelHasMore = false;
    this.searchPanelSuppressed = false;
    this.renderSearchPanel();
  }

  refreshSearchHits() {
    if (this.state.search && !this.searchPanelSuppressed) {
      this.updateSearchHits(this.state.search);
    }
  }

  updateSearchHits(value) {
    const query = (value || "").trim();
    if (!query) {
      this.clearSearchHits();
      return;
    }

    this.searchPanelSuppressed = false;
    this.searchPanelStatus = "loading";
    this.searchSelection = -1;
    this.searchHits = [];
    this.searchPanelHasMore = false;
    this.renderSearchPanel();

    const requestId = ++this.searchHitsRequestId;
    const cacheKey = this.buildSearchHitsCacheKey();
    if (this.searchHitsCache.has(cacheKey)) {
      this.applySearchHits(this.searchHitsCache.get(cacheKey));
      return;
    }

    fetchSearchHits(this.state, 8)
      .then((payload) => {
        if (requestId !== this.searchHitsRequestId) {
          return;
        }
        this.searchHitsCache.set(cacheKey, payload);
        this.applySearchHits(payload);
      })
      .catch((error) => {
        if (requestId !== this.searchHitsRequestId) {
          return;
        }
        console.error("Search hits request failed", error);
        this.searchPanelStatus = "error";
        this.searchHits = [];
        this.searchPanelHasMore = false;
        this.searchSelection = -1;
        this.renderSearchPanel();
      });
  }

  buildSearchHitsCacheKey() {
    const params = new URLSearchParams();
    params.set("search", this.state.search || "");
    params.set("model_value", this.state.modelValue || "");
    params.set("model_match", this.state.modelMatchMode || "");
    params.set("model_provider", this.state.modelProvider || "");
    params.set("providers", Array.from(this.state.activeProviders).sort().join(","));
    params.set(
      "working_dirs",
      Array.from(this.state.selectedWorkingDirs).sort().join(",")
    );
    return params.toString();
  }

  applySearchHits(payload) {
    const hits = Array.isArray(payload?.hits) ? payload.hits : [];
    this.searchHits = hits.slice(0, 8);
    this.searchPanelStatus = hits.length ? "ready" : "empty";
    this.searchPanelHasMore = Boolean(payload?.has_more);
    this.renderSearchPanel();
  }

  renderSearchPanel() {
    const shouldShow = this.shouldShowSearchPanel();
    this.setSearchPanelOpen(shouldShow);
    if (!shouldShow || !this.dom.searchPanelList || !this.dom.searchPanelMeta) {
      return;
    }

    const list = this.dom.searchPanelList;
    const empty = this.dom.searchPanelEmpty;
    const meta = this.dom.searchPanelMeta;

    if (this.searchHits.length) {
      meta.hidden = false;
      const suffix = this.searchPanelHasMore ? "+" : "";
      meta.textContent = `${this.searchHits.length}${suffix} matches`;
    } else {
      meta.hidden = true;
      meta.textContent = "";
    }

    if (!this.searchHits.length) {
      list.innerHTML = "";
      if (empty) {
        empty.hidden = false;
        if (this.searchPanelStatus === "loading") {
          empty.textContent = "Searching…";
        } else if (this.searchPanelStatus === "error") {
          empty.textContent = "Search unavailable";
        } else {
          empty.textContent = "No matches";
        }
      }
      return;
    }

    if (empty) {
      empty.hidden = true;
    }

    list.innerHTML = this.searchHits
      .map((hit, index) => {
        const isActive = index === this.searchSelection;
        const sessionId = hit.session_id || "Session";
        const snippet = this.renderSearchSnippet(hit);
        return `
          <button
            type="button"
            class="search-hit${isActive ? " active" : ""}"
            data-index="${index}"
            role="option"
            aria-selected="${String(isActive)}"
            tabindex="-1"
          >
            <span class="search-hit-id">${escapeHtml(sessionId)}</span>
            <span class="search-hit-snippet">${snippet}</span>
          </button>
        `;
      })
      .join("");
  }

  renderSearchSnippet(hit) {
    const SIDE_CONTEXT = 96;
    const snippet = hit?.snippet || "";
    const start = Number(hit?.snippet_match_start);
    const length = Number(hit?.snippet_match_length);
    if (
      !Number.isFinite(start) ||
      !Number.isFinite(length) ||
      start < 0 ||
      length <= 0 ||
      start >= snippet.length
    ) {
      return escapeHtml(snippet);
    }
    const end = Math.min(snippet.length, start + length);
    const beforeRaw = snippet.slice(0, start);
    const match = snippet.slice(start, end);
    const afterRaw = snippet.slice(end);
    const before =
      beforeRaw.length > SIDE_CONTEXT
        ? `…${beforeRaw.slice(beforeRaw.length - SIDE_CONTEXT)}`
        : beforeRaw;
    const after =
      afterRaw.length > SIDE_CONTEXT
        ? `${afterRaw.slice(0, SIDE_CONTEXT)}…`
        : afterRaw;
    return `${escapeHtml(before)}<mark class="search-match">${escapeHtml(
      match
    )}</mark>${escapeHtml(after)}`;
  }

  handleSearchHitClick(event) {
    const button = event.target.closest(".search-hit");
    if (!button) {
      return;
    }
    const index = Number(button.dataset.index);
    if (!Number.isFinite(index)) {
      return;
    }
    this.openSearchHit(index);
  }

  handleSearchHitHover(event) {
    const button = event.target.closest(".search-hit");
    if (!button) {
      return;
    }
    const index = Number(button.dataset.index);
    if (!Number.isFinite(index)) {
      return;
    }
    this.searchSelection = index;
    this.renderSearchPanel();
  }

  handleSearchInputKeydown(event) {
    if (event.key === "Escape") {
      if (this.searchPanelOpen) {
        event.preventDefault();
        this.searchPanelSuppressed = true;
        this.setSearchPanelOpen(false);
      }
      return;
    }

    if (!this.searchPanelOpen || !this.searchHits.length) {
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (this.searchSelection < 0) {
        this.searchSelection = 0;
      } else {
        this.searchSelection = Math.min(
          this.searchSelection + 1,
          this.searchHits.length - 1
        );
      }
      this.renderSearchPanel();
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (this.searchSelection < 0) {
        this.searchSelection = this.searchHits.length - 1;
      } else {
        this.searchSelection = Math.max(this.searchSelection - 1, 0);
      }
      this.renderSearchPanel();
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      const index =
        this.searchSelection >= 0 ? this.searchSelection : 0;
      this.openSearchHit(index);
    }
  }

  openSearchHit(index) {
    if (!Number.isFinite(index)) {
      return;
    }
    const hit = this.searchHits[index];
    if (!hit) {
      return;
    }
    const link = createSessionLink(hit);
    const url = new URL(link, window.location.origin);
    if (Number.isFinite(hit.message_index)) {
      url.searchParams.set("match_index", String(hit.message_index));
    }
    if (Number.isFinite(hit.match_start)) {
      url.searchParams.set("match_start", String(hit.match_start));
    }
    if (Number.isFinite(hit.match_length)) {
      url.searchParams.set("match_length", String(hit.match_length));
    }
    window.location.href = url.toString();
  }

  handleWorkingDirToggle(path, isChecked) {
    if (isChecked) {
      this.state.selectedWorkingDirs.add(path);
    } else {
      this.state.selectedWorkingDirs.delete(path);
    }
    this.state.page = 1;
    this.refreshFilterChrome();
    this.loadSessions();
    this.refreshSearchHits();
  }

  toggleAllWorkingDirs() {
    const total = this.state.workingDirs.length;
    if (total === 0) {
      return;
    }
    const allSelected = this.state.selectedWorkingDirs.size === total;
    if (allSelected) {
      this.state.selectedWorkingDirs.clear();
    } else {
      this.state.selectedWorkingDirs = new Set(
        this.state.workingDirs.map((entry) => entry.path)
      );
    }
    this.state.page = 1;
    this.refreshFilterChrome({ includeWorkingDirs: true });
    this.loadSessions();
    this.refreshSearchHits();
  }

  handleProviderToggle(providerId) {
    if (this.state.activeProviders.has(providerId)) {
      this.state.activeProviders.delete(providerId);
    } else {
      this.state.activeProviders.add(providerId);
    }
    this.state.page = 1;
    this.refreshFilterChrome();
    this.loadSessions();
    this.refreshSearchHits();
  }

  toggleAllProviders() {
    const total = this.state.providers.length;
    if (total === 0) {
      return;
    }
    const allSelected = this.state.activeProviders.size === total;
    if (allSelected) {
      this.state.activeProviders.clear();
    } else {
      this.state.activeProviders = new Set(
        this.state.providers.map((provider) => provider.id)
      );
    }
    this.state.page = 1;
    this.refreshFilterChrome();
    this.loadSessions();
    this.refreshSearchHits();
  }

  handleModelQuickSelect(modelId) {
    this.state.modelValue = modelId;
    this.state.modelMatchMode = "exact";
    this.state.page = 1;
    this.refreshFilterChrome();
    this.loadSessions();
    this.refreshSearchHits();
  }

  toggleAllModels() {
    const total = this.state.models.length;
    if (total === 0) {
      return;
    }
    const allSelected =
      !this.state.modelValue && !this.state.modelProvider;
    if (allSelected) {
      this.state.modelValue = "__none__";
      this.state.modelMatchMode = "exact";
    } else {
      this.state.modelValue = "";
      this.state.modelMatchMode = "prefix";
      this.state.modelProvider = "";
    }
    this.state.page = 1;
    this.refreshFilterChrome();
    this.loadSessions();
    this.refreshSearchHits();
  }

  resetAllFilters() {
    this.state.search = "";
    this.state.modelValue = "";
    this.state.modelMatchMode = "prefix";
    this.state.modelProvider = "";
    this.state.activeProviders = new Set(
      this.state.providers.map((provider) => provider.id)
    );
    this.state.selectedWorkingDirs = new Set(
      this.state.workingDirs.map((entry) => entry.path)
    );
    this.state.page = 1;
    this.refreshFilterChrome({ includeWorkingDirs: true });
    this.clearSearchHits();
    this.loadSessions();
  }

  _showNoWorkingDirSelection() {
    this.state.page = 1;
    this.dom.tableBody.innerHTML =
      '<tr><td colspan="7" class="empty">No working directories selected.</td></tr>';
    this.dom.pageInfo.textContent = "Select at least one working directory";
    this.dom.pagePrev.disabled = true;
    this.dom.pageNext.disabled = true;
    this.renderer.updateResultsCount(0);
    this.renderer.renderActiveFilters();
    syncUrlFromState(this.state);
    this.state.pendingRefresh = false;
  }

  async loadSessions() {
    if (this.state.busy) {
      this.state.pendingRefresh = true;
      return;
    }
    if (
      this.state.workingDirs.length > 0 &&
      this.state.selectedWorkingDirs.size === 0
    ) {
      this._showNoWorkingDirSelection();
      return;
    }

    this.state.busy = true;
    this.state.pendingRefresh = false;
    this.renderer.setLoading("Loading sessions…");
    this.renderer.renderActiveFilters();
    syncUrlFromState(this.state);

    try {
      const payload = await fetchSessions(this.state);
      this.state.page = payload.page;
      this.renderer.renderSessions(payload);
      this.renderer.updatePagination(payload);
      syncUrlFromState(this.state);
    } catch (error) {
      console.error("Failed to load sessions", error);
      this.renderer.setLoading("Failed to load sessions.");
      this.dom.pageInfo.textContent = error.message;
    } finally {
      this.state.busy = false;
      if (this.state.pendingRefresh) {
        this.state.pendingRefresh = false;
        this.loadSessions();
      }
    }
  }

  async loadInitialData() {
    this.state.busy = true;
    this.state.pendingRefresh = false;
    const results = await fetchBootstrapData(this.state);

    this.handleProviderBootstrap(results.providers);
    this.handleModelBootstrap(results.models);
    this.handleSessionBootstrap(results.sessions);
    this.handleWorkingDirBootstrap(results.workingDirs);

    this.refreshFilterChrome({ includeWorkingDirs: true });
    this.state.busy = false;
    syncUrlFromState(this.state);
    revealApp();
    if (this.state.search) {
      this.updateSearchHits(this.state.search);
    }
    if (this.state.pendingRefresh) {
      this.state.pendingRefresh = false;
      this.loadSessions();
    }
  }

  handleProviderBootstrap(result) {
    if (result.status === "fulfilled") {
      const payload = result.value;
      this.state.providers = payload.providers || [];
      const pending = this.state.pendingProviderFilters;
      if (pending) {
        this.state.activeProviders = new Set(
          this.state.providers
            .filter((provider) => pending.has(provider.id))
            .map((provider) => provider.id)
        );
        this.state.pendingProviderFilters = null;
      } else {
        this.state.activeProviders = new Set(
          this.state.providers.map((provider) => provider.id)
        );
      }
      this.renderer.renderProviders(this.onProviderToggle);
      this.renderer.renderModelProviderOptions();
      return;
    }
    console.error("Failed to load providers", result.reason);
    this.dom.providerToolbar.innerHTML =
      '<span class="provider-empty">Failed to load providers.</span>';
    this.dom.pageInfo.textContent = "Provider request failed";
  }

  handleModelBootstrap(result) {
    if (result.status === "fulfilled") {
      const payload = result.value;
      this.state.models = payload.models || [];
      this.renderer.renderModels(this.onModelQuickSelect);
      this.renderer.renderModelOptions();
      return;
    }
    console.error("Failed to load models", result.reason);
    this.dom.modelToolbar.innerHTML =
      '<span class="provider-empty">Failed to load models.</span>';
  }

  handleSessionBootstrap(result) {
    if (result.status === "fulfilled") {
      const payload = result.value;
      this.state.page = payload.page;
      this.renderer.renderSessions(payload);
      this.renderer.updatePagination(payload);
      return;
    }
    console.error("Failed to load sessions", result.reason);
    this.renderer.setLoading("Failed to load sessions.");
    this.dom.pageInfo.textContent =
      (result.reason && result.reason.message) || "Session request failed";
  }

  handleWorkingDirBootstrap(result) {
    if (result.status === "fulfilled") {
      const payload = result.value;
      this.syncWorkingDirState(payload.working_dirs || []);
      this.renderer.renderWorkingDirs(this.onWorkingDirToggle);
      return;
    }
    console.error("Failed to load working directories", result.reason);
    this.dom.workingDirList.innerHTML =
      '<span class="filter-empty">Failed to load directories.</span>';
    this.dom.workingDirToggle.disabled = true;
  }
}

function initIndexPage() {
  const dom = collectDomRefs();
  if (!dom) {
    revealApp();
    return;
  }

  const controller = new IndexController(dom);
  const refreshSearchHits = () => {
    controller.refreshSearchHits();
  };

  initFilterControls(dom, controller.state, {
    onToggleAllWorkingDirs: controller.toggleAllWorkingDirs.bind(controller),
    onToggleAllProviders: controller.toggleAllProviders.bind(controller),
    onToggleAllModels: controller.toggleAllModels.bind(controller),
    onSearchChange: (value) => {
      controller.state.search = value;
      controller.state.page = 1;
      controller.refreshFilterChrome();
      controller.loadSessions();
    },
    onSearchPreview: (value) => {
      controller.updateSearchHits(value);
    },
    onModelChange: (value) => {
      controller.state.modelValue = value;
      controller.state.page = 1;
      controller.refreshFilterChrome();
      controller.loadSessions();
      refreshSearchHits();
    },
    onModelModeChange: (value) => {
      controller.state.modelMatchMode = value === "exact" ? "exact" : "prefix";
      controller.state.page = 1;
      controller.refreshFilterChrome();
      controller.loadSessions();
      refreshSearchHits();
    },
    onModelProviderChange: (value) => {
      controller.state.modelProvider = value || "";
      controller.state.page = 1;
      controller.refreshFilterChrome();
      controller.loadSessions();
      refreshSearchHits();
    },
    onResetAll: () => {
      controller.resetAllFilters();
    },
    onPageChange: (nextPage) => {
      controller.state.page = nextPage;
      controller.loadSessions();
    },
  });

  initThemeToggle();
  controller.loadInitialData();
}

function bootstrap() {
  initIndexPage();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootstrap);
} else {
  bootstrap();
}
