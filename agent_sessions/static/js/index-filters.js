/**
 * Wire up filter controls (search, collapsible panels, pagination).
 */
export function initFilterControls(dom, state, callbacks) {
  let workingDirsCollapsed = true;
  let providersCollapsed = true;
  let searchTimer;

  function setWorkingDirCollapsed(collapsed) {
    workingDirsCollapsed = collapsed;
    if (collapsed) {
      dom.workingDirBody.classList.add("collapsed");
      dom.workingDirBody.setAttribute("aria-hidden", "true");
      dom.workingDirHeader.setAttribute("aria-expanded", "false");
    } else {
      dom.workingDirBody.classList.remove("collapsed");
      dom.workingDirBody.setAttribute("aria-hidden", "false");
      dom.workingDirHeader.setAttribute("aria-expanded", "true");
    }
  }

  function setProviderCollapsed(collapsed) {
    providersCollapsed = collapsed;
    if (collapsed) {
      dom.providerBody.classList.add("collapsed");
      dom.providerBody.setAttribute("aria-hidden", "true");
      dom.providerHeader.setAttribute("aria-expanded", "false");
    } else {
      dom.providerBody.classList.remove("collapsed");
      dom.providerBody.setAttribute("aria-hidden", "false");
      dom.providerHeader.setAttribute("aria-expanded", "true");
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

  dom.workingDirToggle.addEventListener("click", (event) => {
    event.stopPropagation();
    callbacks.onToggleAllWorkingDirs();
  });

  bindCollapsibleHeader(dom.workingDirHeader, () =>
    setWorkingDirCollapsed(!workingDirsCollapsed),
  "#working-dir-toggle");
  bindCollapsibleHeader(dom.providerHeader, () =>
    setProviderCollapsed(!providersCollapsed));

  setWorkingDirCollapsed(true);
  setProviderCollapsed(true);

  if (dom.searchInput) {
    dom.searchInput.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        callbacks.onSearchChange(dom.searchInput.value.trim());
      }, 200);
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

  return { setWorkingDirCollapsed, setProviderCollapsed };
}
