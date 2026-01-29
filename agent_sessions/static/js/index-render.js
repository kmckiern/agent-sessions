import { createSessionLink } from "./api.js";
import { attachCopyHandlers, escapeHtml, formatDate } from "./ui.js";

export function createListRenderer(dom, state) {
  function setLoading(message) {
    dom.tableBody.innerHTML = `<tr><td colspan="7" class="empty">${message}</td></tr>`;
  }

  function renderProviders(onToggle) {
    if (!state.providers.length) {
      dom.providerToolbar.innerHTML =
        '<span class="provider-empty">No providers detected.</span>';
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

  function renderSessions(payload) {
    if (!payload.sessions.length) {
      const message =
        state.activeProviders.size === 0
          ? "No providers selected"
          : state.search
          ? "No matching sessions"
          : "No sessions available";
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

  function updatePagination(meta) {
    state.totalPages = meta.total_pages;
    const displayPage = meta.total_pages === 0 ? 0 : state.page;
    const displayTotal = meta.total_pages;
    const totalSessions = meta.total_sessions;

    if (displayTotal === 0) {
      dom.pageInfo.textContent = state.activeProviders.size
        ? state.search
          ? "No matching sessions"
          : "No sessions available"
        : "No providers selected";
    } else {
      dom.pageInfo.textContent = `Page ${displayPage} of ${displayTotal} (${totalSessions} sessions)`;
    }

    dom.pagePrev.disabled = displayTotal === 0 || state.page <= 1;
    dom.pageNext.disabled = displayTotal === 0 || state.page >= displayTotal;
  }

  return {
    setLoading,
    renderProviders,
    renderWorkingDirs,
    renderSessions,
    updatePagination,
    updateWorkingDirToggleButton,
  };
}
