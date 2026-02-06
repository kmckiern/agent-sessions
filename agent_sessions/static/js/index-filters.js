/**
 * Wire up filter controls (search/model inputs, reset, pagination).
 */
export function initFilterControls(dom, state, callbacks) {
  let filtersGroupCollapsed = true;
  let providersCollapsed = true;
  let modelsCollapsed = true;
  let workingDirsCollapsed = true;
  let searchTimer;
  let modelTimer;

  dom.searchInput.value = state.search || "";
  dom.modelInput.value = state.modelValue || "";
  dom.modelMatchMode.value =
    state.modelMatchMode === "exact" ? "exact" : "prefix";
  dom.modelProviderFilter.value = state.modelProvider || "";

  function setCollapsed(body, header, collapsed) {
    if (collapsed) {
      body.classList.add("collapsed");
      body.setAttribute("aria-hidden", "true");
      header.setAttribute("aria-expanded", "false");
    } else {
      body.classList.remove("collapsed");
      body.setAttribute("aria-hidden", "false");
      header.setAttribute("aria-expanded", "true");
    }
  }

  function setFiltersGroupCollapsed(collapsed) {
    filtersGroupCollapsed = collapsed;
    if (collapsed) {
      dom.filtersGroupBody.classList.add("collapsed");
      dom.filtersGroupBody.setAttribute("aria-hidden", "true");
      dom.filtersGroupToggle.setAttribute("aria-expanded", "false");
      dom.filtersGroupToggle.textContent = "Filters";
    } else {
      dom.filtersGroupBody.classList.remove("collapsed");
      dom.filtersGroupBody.setAttribute("aria-hidden", "false");
      dom.filtersGroupToggle.setAttribute("aria-expanded", "true");
      dom.filtersGroupToggle.textContent = "Filters";
    }
  }

  function bindCollapsibleHeader(element, toggleFn, ignoreSelector) {
    if (!element) {
      return;
    }
    element.addEventListener("click", (event) => {
      if (ignoreSelector && event.target.closest(ignoreSelector)) {
        return;
      }
      toggleFn();
    });
    element.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar") {
        return;
      }
      if (ignoreSelector && event.target.closest(ignoreSelector)) {
        return;
      }
      event.preventDefault();
      toggleFn();
    });
  }

  bindCollapsibleHeader(dom.providerHeader, () => {
    providersCollapsed = !providersCollapsed;
    setCollapsed(dom.providerBody, dom.providerHeader, providersCollapsed);
  }, "#provider-toggle");

  bindCollapsibleHeader(dom.modelHeader, () => {
    modelsCollapsed = !modelsCollapsed;
    setCollapsed(dom.modelBody, dom.modelHeader, modelsCollapsed);
  }, "#model-toggle");

  bindCollapsibleHeader(
    dom.workingDirHeader,
    () => {
      workingDirsCollapsed = !workingDirsCollapsed;
      setCollapsed(dom.workingDirBody, dom.workingDirHeader, workingDirsCollapsed);
    },
    "#working-dir-toggle"
  );

  setCollapsed(dom.providerBody, dom.providerHeader, true);
  setCollapsed(dom.modelBody, dom.modelHeader, true);
  setCollapsed(dom.workingDirBody, dom.workingDirHeader, true);
  setFiltersGroupCollapsed(true);

  dom.filtersGroupToggle.addEventListener("click", () => {
    setFiltersGroupCollapsed(!filtersGroupCollapsed);
  });

  dom.workingDirToggle.addEventListener("click", () => {
    callbacks.onToggleAllWorkingDirs();
  });
  dom.providerToggle.addEventListener("click", () => {
    callbacks.onToggleAllProviders();
  });
  dom.modelToggle.addEventListener("click", () => {
    callbacks.onToggleAllModels();
  });

  dom.searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const nextValue = dom.searchInput.value.trim();
      callbacks.onSearchChange(nextValue);
      if (callbacks.onSearchPreview) {
        callbacks.onSearchPreview(nextValue);
      }
    }, 180);
  });

  dom.modelInput.addEventListener("input", () => {
    clearTimeout(modelTimer);
    modelTimer = setTimeout(() => {
      callbacks.onModelChange(dom.modelInput.value.trim());
    }, 180);
  });

  dom.modelMatchMode.addEventListener("change", () => {
    callbacks.onModelModeChange(dom.modelMatchMode.value);
  });

  dom.modelProviderFilter.addEventListener("change", () => {
    callbacks.onModelProviderChange(dom.modelProviderFilter.value);
  });

  if (dom.resetFilters) {
    dom.resetFilters.addEventListener("click", () => {
      callbacks.onResetAll();
    });
  }

  dom.pagePrev.addEventListener("click", () => {
    if (state.page > 1) {
      callbacks.onPageChange(state.page - 1);
    }
  });

  dom.pageNext.addEventListener("click", () => {
    if (state.page < state.totalPages) {
      callbacks.onPageChange(state.page + 1);
    }
  });
}
