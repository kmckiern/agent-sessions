import { createSessionLink } from "./api.js";
import { attachCopyHandlers, escapeHtml, formatDate } from "./ui.js";

const QUICK_MODEL_LIMIT = 14;
const MODEL_NONE_SENTINEL = "__none__";

export function createListRenderer(dom, state) {
  function setLoading(message) {
    dom.tableBody.innerHTML = `<tr><td colspan="7" class="empty">${message}</td></tr>`;
  }

  function renderProviders(onToggle) {
    if (!state.providers.length) {
      dom.providerToolbar.innerHTML =
        '<span class="provider-empty">No providers detected.</span>';
      updateProviderToggleButton();
      return;
    }

    dom.providerToolbar.innerHTML = "";
    state.providers.forEach((provider) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.provider = provider.id;
      button.textContent = provider.label;
      button.setAttribute(
        "aria-pressed",
        String(state.activeProviders.has(provider.id))
      );
      button.addEventListener("click", () => {
        onToggle(provider.id);
        button.setAttribute(
          "aria-pressed",
          String(state.activeProviders.has(provider.id))
        );
      });
      dom.providerToolbar.appendChild(button);
    });
    updateProviderToggleButton();
  }

  function renderModels(onSelect) {
    if (!state.models.length) {
      dom.modelToolbar.innerHTML =
        '<span class="provider-empty">No models detected.</span>';
      updateModelToggleButton();
      return;
    }

    const activeModel = (state.modelValue || "").trim().toLowerCase();
    const isExactMode = state.modelMatchMode === "exact";
    const hasModelFilter =
      activeModel.length > 0 && activeModel !== MODEL_NONE_SENTINEL;
    const noModelsSelected = activeModel === MODEL_NONE_SENTINEL;
    dom.modelToolbar.innerHTML = "";
    state.models.slice(0, QUICK_MODEL_LIMIT).forEach((model) => {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.model = model.id;
      button.textContent = `${model.label} (${model.count})`;
      const modelId = model.id.toLowerCase();
      let pressed = !hasModelFilter && !noModelsSelected;
      if (hasModelFilter) {
        pressed = isExactMode
          ? modelId === activeModel
          : modelId.startsWith(activeModel);
      }
      button.setAttribute("aria-pressed", String(pressed));
      button.addEventListener("click", () => {
        onSelect(model.id);
      });
      dom.modelToolbar.appendChild(button);
    });
    updateModelToggleButton();
  }

  function updateProviderToggleButton() {
    const total = state.providers.length;
    if (total === 0) {
      dom.providerToggle.disabled = true;
      dom.providerToggle.textContent = "Select all";
      dom.providerToggle.setAttribute("aria-pressed", "false");
      return;
    }

    dom.providerToggle.disabled = false;
    if (state.activeProviders.size === total) {
      dom.providerToggle.textContent = "Deselect all";
      dom.providerToggle.setAttribute("aria-pressed", "true");
    } else {
      dom.providerToggle.textContent = "Select all";
      dom.providerToggle.setAttribute("aria-pressed", "false");
    }
  }

  function updateModelToggleButton() {
    const total = state.models.length;
    if (total === 0) {
      dom.modelToggle.disabled = true;
      dom.modelToggle.textContent = "Select all";
      dom.modelToggle.setAttribute("aria-pressed", "false");
      return;
    }

    dom.modelToggle.disabled = false;
    const activeModel = (state.modelValue || "").trim().toLowerCase();
    const allSelected = !activeModel && !state.modelProvider;
    if (allSelected) {
      dom.modelToggle.textContent = "Deselect all";
      dom.modelToggle.setAttribute("aria-pressed", "true");
    } else {
      dom.modelToggle.textContent = "Select all";
      dom.modelToggle.setAttribute("aria-pressed", "false");
    }
  }

  function renderModelOptions() {
    if (!dom.modelOptions) {
      return;
    }
    dom.modelOptions.innerHTML = "";
    state.models.forEach((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.label = `${model.label} (${model.count})`;
      dom.modelOptions.appendChild(option);
    });
  }

  function renderModelProviderOptions() {
    const select = dom.modelProviderFilter;
    if (!select) {
      return;
    }

    const known = new Set();
    select.innerHTML = "";
    const anyOption = document.createElement("option");
    anyOption.value = "";
    anyOption.textContent = "Any provider";
    select.appendChild(anyOption);

    state.providers.forEach((provider) => {
      known.add(provider.id);
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.label;
      select.appendChild(option);
    });

    if (state.modelProvider && !known.has(state.modelProvider)) {
      const fallback = document.createElement("option");
      fallback.value = state.modelProvider;
      fallback.textContent = state.modelProvider;
      select.appendChild(fallback);
    }
    select.value = state.modelProvider || "";
  }

  function updateWorkingDirToggleButton() {
    const total = state.workingDirs.length;
    if (total === 0) {
      dom.workingDirToggle.disabled = true;
      dom.workingDirToggle.textContent = "Select all";
      dom.workingDirToggle.setAttribute("aria-pressed", "false");
      return;
    }
    dom.workingDirToggle.disabled = false;
    if (state.selectedWorkingDirs.size === total) {
      dom.workingDirToggle.textContent = "Deselect all";
      dom.workingDirToggle.setAttribute("aria-pressed", "true");
    } else {
      dom.workingDirToggle.textContent = "Select all";
      dom.workingDirToggle.setAttribute("aria-pressed", "false");
    }
  }

  function renderWorkingDirs(onToggle) {
    dom.workingDirList.innerHTML = "";

    if (!state.workingDirs.length) {
      dom.workingDirList.innerHTML =
        '<span class="filter-empty">No working directories detected.</span>';
      updateWorkingDirToggleButton();
      return;
    }

    state.workingDirs.forEach((entry, index) => {
      const { path, count } = entry;
      const label = document.createElement("label");
      label.classList.add("filter-item");
      label.setAttribute("title", path);
      label.htmlFor = `working-dir-${index}`;

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.id = `working-dir-${index}`;
      checkbox.value = path;
      checkbox.checked = state.selectedWorkingDirs.has(path);
      checkbox.addEventListener("change", (event) => {
        onToggle(path, event.target.checked);
      });

      const text = document.createElement("span");
      text.classList.add("filter-item-text");
      text.textContent = path;

      const countBadge = document.createElement("span");
      countBadge.classList.add("filter-item-count");
      countBadge.textContent = `(${count})`;

      label.appendChild(checkbox);
      label.appendChild(text);
      label.appendChild(countBadge);
      dom.workingDirList.appendChild(label);
    });

    updateWorkingDirToggleButton();
  }

  function renderActiveFilters() {
    if (!dom.activeFilters) {
      return;
    }

    const pills = [];
    if (state.search && state.search !== "undefined") {
      pills.push(`Search: ${state.search}`);
    }
    if (state.modelValue && state.modelValue !== "undefined") {
      pills.push(
        `Model ${state.modelMatchMode === "exact" ? "exact" : "prefix"}: ${
          state.modelValue
        }`
      );
    }
    if (state.modelProvider && state.modelProvider !== "undefined") {
      const provider = state.providers.find(
        (entry) => entry.id === state.modelProvider
      );
      pills.push(`Model provider: ${provider ? provider.label : state.modelProvider}`);
    }

    const providerIsSubset =
      state.providers.length > 0 &&
      state.activeProviders.size !== state.providers.length;
    if (providerIsSubset) {
      const labels = state.providers
        .filter((entry) => state.activeProviders.has(entry.id))
        .map((entry) => entry.label);
      if (labels.length === 0) {
        pills.push("Providers: none selected");
      } else if (labels.length <= 3) {
        pills.push(`Providers: ${labels.join(", ")}`);
      } else {
        pills.push(`Providers: ${labels.length} selected`);
      }
    }

    const workingDirIsSubset =
      state.workingDirs.length > 0 &&
      state.selectedWorkingDirs.size !== state.workingDirs.length;
    if (workingDirIsSubset) {
      pills.push(
        `Working dirs: ${state.selectedWorkingDirs.size}/${state.workingDirs.length}`
      );
    }

    if (!pills.length) {
      dom.activeFilters.innerHTML =
        '<span class="active-filter-empty">No active filters.</span>';
      return;
    }

    dom.activeFilters.innerHTML = pills
      .map((text) => `<span class="active-filter-pill">${escapeHtml(text)}</span>`)
      .join("");
  }

  function hasActiveFilters() {
    const providerIsSubset =
      state.providers.length > 0 &&
      state.activeProviders.size !== state.providers.length;
    const workingDirIsSubset =
      state.workingDirs.length > 0 &&
      state.selectedWorkingDirs.size !== state.workingDirs.length;
    return Boolean(
      state.search ||
        state.modelValue ||
        state.modelProvider ||
        providerIsSubset ||
        workingDirIsSubset
    );
  }

  function renderSessions(payload) {
    if (!payload.sessions.length) {
      let message = "No sessions available";
      if (state.activeProviders.size === 0) {
        message = "No providers selected";
      } else if (hasActiveFilters()) {
        message = "No matching sessions";
      }
      setLoading(message);
      return;
    }

    const rows = payload.sessions
      .map((session) => {
        const provider =
          session.provider_label || session.provider || "Unknown";
        const model = session.model || "—";
        const link = escapeHtml(createSessionLink(session));
        const safeId = session.session_id || "";
        const workingDir = session.working_dir || "—";
        const preview = session.preview || "";
        const updatedAt = formatDate(session.updated_at);
        return `
          <tr data-provider="${escapeHtml(session.provider || "")}">
            <td class="cell cell-truncate">${escapeHtml(provider)}</td>
            <td class="cell cell-wrap">${escapeHtml(model)}</td>
            <td class="cell session-cell">
              <a href="${link}" class="cell-truncate">${escapeHtml(safeId)}</a>
              <button type="button" class="copy-session" data-session-id="${escapeHtml(
                safeId
              )}">Copy</button>
            </td>
            <td class="cell cell-wrap">${escapeHtml(updatedAt)}</td>
            <td class="cell cell-wrap cell-last-message"><div class="message-snippet">${escapeHtml(
              preview
            )}</div></td>
            <td class="cell cell-wrap">${escapeHtml(workingDir)}</td>
            <td class="cell numeric">${escapeHtml(session.message_count)}</td>
          </tr>
        `;
      })
      .join("");

    dom.tableBody.innerHTML = rows;
    attachCopyHandlers(dom.tableBody);
  }

  function updateResultsCount(totalSessions) {
    if (!dom.resultsCount) {
      return;
    }
    const count = Number.isFinite(totalSessions) ? totalSessions : 0;
    const suffix = count === 1 ? "result" : "results";
    dom.resultsCount.textContent = `${count} ${suffix}`;
  }

  function updatePagination(meta) {
    state.totalPages = meta.total_pages;
    const displayPage = meta.total_pages === 0 ? 0 : state.page;
    const displayTotal = meta.total_pages;
    const totalSessions = meta.total_sessions;

    updateResultsCount(totalSessions);
    if (displayTotal === 0) {
      dom.pageInfo.textContent = "";
    } else {
      dom.pageInfo.textContent = `Page ${displayPage} of ${displayTotal}`;
    }

    dom.pagePrev.disabled = displayTotal === 0 || state.page <= 1;
    dom.pageNext.disabled = displayTotal === 0 || state.page >= displayTotal;
  }

  return {
    setLoading,
    renderProviders,
    renderModels,
    renderModelOptions,
    renderModelProviderOptions,
    renderWorkingDirs,
    renderSessions,
    renderActiveFilters,
    updatePagination,
    updateResultsCount,
    updateWorkingDirToggleButton,
    updateProviderToggleButton,
    updateModelToggleButton,
  };
}
