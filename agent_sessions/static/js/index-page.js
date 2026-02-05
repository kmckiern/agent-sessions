import {
  DEFAULT_PAGE_SIZE,
  createListState,
  hydrateStateFromUrl,
  syncUrlFromState,
} from "./state.js";
import { initThemeToggle, revealApp } from "./ui.js";
import { fetchBootstrapData, fetchSessions } from "./index-data.js";
import { createListRenderer } from "./index-render.js";
import { initFilterControls } from "./index-filters.js";

const PAGE_SIZE = DEFAULT_PAGE_SIZE;

function collectDomRefs() {
  const dom = {
    providerToolbar: document.getElementById("provider-toolbar"),
    modelToolbar: document.getElementById("model-toolbar"),
    modelOptions: document.getElementById("model-options"),
    searchInput: document.getElementById("session-search"),
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

    this.dom.workingDirList.innerHTML =
      '<span class="filter-empty">Loading directories…</span>';
    this.dom.workingDirToggle.disabled = true;
    this.renderer.updateResultsCount(0);
    this.renderer.renderActiveFilters();
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

  handleWorkingDirToggle(path, isChecked) {
    if (isChecked) {
      this.state.selectedWorkingDirs.add(path);
    } else {
      this.state.selectedWorkingDirs.delete(path);
    }
    this.state.page = 1;
    this.refreshFilterChrome();
    this.loadSessions();
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
  }

  handleModelQuickSelect(modelId) {
    this.state.modelValue = modelId;
    this.state.modelMatchMode = "exact";
    this.state.page = 1;
    this.refreshFilterChrome();
    this.loadSessions();
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
    onModelChange: (value) => {
      controller.state.modelValue = value;
      controller.state.page = 1;
      controller.refreshFilterChrome();
      controller.loadSessions();
    },
    onModelModeChange: (value) => {
      controller.state.modelMatchMode = value === "exact" ? "exact" : "prefix";
      controller.state.page = 1;
      controller.refreshFilterChrome();
      controller.loadSessions();
    },
    onModelProviderChange: (value) => {
      controller.state.modelProvider = value || "";
      controller.state.page = 1;
      controller.refreshFilterChrome();
      controller.loadSessions();
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
