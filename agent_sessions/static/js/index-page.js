import { DEFAULT_PAGE_SIZE, createListState } from "./state.js";
import { initThemeToggle, revealApp } from "./ui.js";
import { fetchBootstrapData, fetchSessions } from "./index-data.js";
import { createListRenderer } from "./index-render.js";
import { initFilterControls } from "./index-filters.js";

const PAGE_SIZE = DEFAULT_PAGE_SIZE;

function collectDomRefs() {
  const dom = {
    providerToolbar: document.getElementById("provider-toolbar"),
    searchInput: document.getElementById("session-search"),
    tableBody: document.getElementById("sessions-body"),
    pagePrev: document.getElementById("page-prev"),
    pageNext: document.getElementById("page-next"),
    pageInfo: document.getElementById("page-info"),
    workingDirList: document.getElementById("working-dir-list"),
    workingDirToggle: document.getElementById("working-dir-toggle"),
    workingDirBody: document.getElementById("working-dir-body"),
    workingDirHeader: document.getElementById("working-dir-header"),
    providerBody: document.getElementById("provider-body"),
    providerHeader: document.getElementById("provider-header"),
  };
  if (Object.values(dom).some((node) => !node)) {
    return null;
  }
  return dom;
}

class IndexController {
  constructor(dom) {
    this.dom = dom;
    this.state = createListState(PAGE_SIZE);
    this.renderer = createListRenderer(dom, this.state);
    this.dom.workingDirList.innerHTML =
      '<span class="filter-empty">Loading directories…</span>';
    this.dom.workingDirToggle.disabled = true;
  }

  syncWorkingDirState(entries) {
    const previousSelection = new Set(this.state.selectedWorkingDirs);
    const previousTotal = this.state.workingDirs.length;
    const hadSelection = previousSelection.size > 0;
    const hadAllSelected =
      previousTotal > 0 && previousSelection.size === previousTotal;

    this.state.workingDirs = entries;

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

  handleWorkingDirToggle(path, isChecked) {
    if (isChecked) {
      this.state.selectedWorkingDirs.add(path);
    } else {
      this.state.selectedWorkingDirs.delete(path);
    }
    this.state.page = 1;
    this.renderer.updateWorkingDirToggleButton();
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
    this.renderer.renderWorkingDirs(this.handleWorkingDirToggle.bind(this));
    this.loadSessions();
  }

  handleProviderToggle(providerId) {
    if (this.state.activeProviders.has(providerId)) {
      this.state.activeProviders.delete(providerId);
    } else {
      this.state.activeProviders.add(providerId);
    }
    this.state.page = 1;
    this.loadSessions();
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
      this.state.page = 1;
      this.dom.tableBody.innerHTML =
        '<tr><td colspan="7" class="empty">No working directories selected.</td></tr>';
      this.dom.pageInfo.textContent = "Select at least one working directory";
      this.dom.pagePrev.disabled = true;
      this.dom.pageNext.disabled = true;
      this.state.pendingRefresh = false;
      return;
    }

    this.state.busy = true;
    this.state.pendingRefresh = false;
    this.renderer.setLoading("Loading sessions…");

    try {
      const payload = await fetchSessions(this.state);
      this.state.page = payload.page;
      this.renderer.renderSessions(payload);
      this.renderer.updatePagination(payload);
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
    this.handleSessionBootstrap(results.sessions);
    this.handleWorkingDirBootstrap(results.workingDirs);

    this.state.busy = false;
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
      this.state.activeProviders = new Set(
        this.state.providers.map((item) => item.id)
      );
      this.renderer.renderProviders(this.handleProviderToggle.bind(this));
      return;
    }
    console.error("Failed to load providers", result.reason);
    this.dom.providerToolbar.innerHTML =
      '<span class="provider-empty">Failed to load providers.</span>';
    this.dom.pageInfo.textContent = "Provider request failed";
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
      this.renderer.renderWorkingDirs(this.handleWorkingDirToggle.bind(this));
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
    onSearchChange: (value) => {
      controller.state.search = value;
      controller.state.page = 1;
      controller.loadSessions();
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
