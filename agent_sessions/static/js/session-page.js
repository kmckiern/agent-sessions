import { fetchJSON } from "./api.js";
import {
  attachCopyHandlers,
  copyTextToClipboard,
  escapeHtml,
  formatDate,
  initThemeToggle,
  revealApp,
} from "./ui.js";

function initSessionPage() {
  const params = new URLSearchParams(window.location.search);
  const provider = params.get("provider");
  const sessionId = params.get("session") || params.get("session_id");
  const sourcePath = params.get("source_path") || params.get("path");

  const titleEl = document.getElementById("session-title");
  const copyButton = document.getElementById("session-copy");
  const metaProvider = document.getElementById("meta-provider");
  const metaModel = document.getElementById("meta-model");
  const metaWorkingDir = document.getElementById("meta-working-dir");
  const metaStarted = document.getElementById("meta-started");
  const metaUpdated = document.getElementById("meta-updated");
  const metaMessages = document.getElementById("meta-messages");
  const metaSource = document.getElementById("meta-source");
  const tableBody = document.getElementById("message-body");
  const copyTableButton = document.getElementById("session-copy-table");

  let tableExportText = "";

  if (!titleEl || !tableBody) {
    revealApp();
    return;
  }

  if (copyButton) {
    copyButton.dataset.sessionId = sessionId || "";
    copyButton.disabled = !sessionId;
    attachCopyHandlers(document);
  }

  const normalizeValue = (value) => {
    if (value === undefined || value === null) {
      return "";
    }
    return String(value);
  };

  const renderError = (message) => {
    if (titleEl) {
      titleEl.textContent = "Session unavailable";
    }
    tableBody.innerHTML = `<tr><td colspan="3" class="empty">${escapeHtml(
      message
    )}</td></tr>`;
    if (copyButton) {
      copyButton.disabled = true;
      copyButton.dataset.sessionId = "";
    }
    if (copyTableButton) {
      copyTableButton.disabled = true;
      copyTableButton.textContent = "Copy";
    }
    tableExportText = "";
    revealApp();
  };

  if (copyTableButton) {
    copyTableButton.disabled = true;
    copyTableButton.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (!tableExportText) {
        return;
      }

      const original = copyTableButton.textContent || "Copy";
      const handleSuccess = () => {
        copyTableButton.textContent = "Copied!";
        copyTableButton.disabled = true;
        setTimeout(() => {
          copyTableButton.textContent = original;
          copyTableButton.disabled = false;
        }, 1500);
      };

      const handleFailure = () => {
        copyTableButton.textContent = "Failed";
        setTimeout(() => {
          copyTableButton.textContent = original;
        }, 1500);
      };

      copyTextToClipboard(tableExportText)
        .then(handleSuccess)
        .catch((err) => {
          console.error("Clipboard write failed", err);
          handleFailure();
        });
    });
  }

  if (!provider || !sessionId) {
    renderError("Missing provider or session id.");
    return;
  }

  const query = new URLSearchParams();
  if (sourcePath) {
    query.set("source_path", sourcePath);
  }

  const encodedProvider = encodeURIComponent(provider);
  const encodedSession = encodeURIComponent(sessionId);
  const queryString = query.toString();

  fetchJSON(
    `/api/sessions/${encodedProvider}/${encodedSession}${
      queryString ? `?${queryString}` : ""
    }`
  )
    .then((payload) => {
      if (!payload.session) {
        renderError("Session payload missing.");
        return;
      }

      const session = payload.session;
      titleEl.textContent = session.session_id || "Session detail";
      if (copyButton) {
        const resolvedId = session.session_id || "";
        copyButton.dataset.sessionId = resolvedId;
        copyButton.disabled = !resolvedId;
      }
      document.title = session.session_id
        ? `Sessions · ${session.session_id}`
        : "Session Detail";

      if (metaProvider) {
        metaProvider.textContent =
          session.provider_label || session.provider || "Unknown";
      }
      if (metaModel) {
        metaModel.textContent = session.model || "—";
      }
      if (metaWorkingDir) {
        metaWorkingDir.textContent = session.working_dir || "—";
      }
      if (metaStarted) {
        metaStarted.textContent = formatDate(session.started_at);
      }
      if (metaUpdated) {
        metaUpdated.textContent = formatDate(session.updated_at);
      }
      if (metaMessages) {
        metaMessages.textContent = String(session.message_count || 0);
      }
      if (metaSource) {
        metaSource.textContent = session.source_path || "—";
      }

      if (!Array.isArray(session.messages) || !session.messages.length) {
        tableBody.innerHTML =
          '<tr><td colspan="3" class="empty">No messages recorded.</td></tr>';
        tableExportText = JSON.stringify([], null, 2);
        if (copyTableButton) {
          copyTableButton.disabled = false;
          copyTableButton.textContent = "Copy";
        }
        return;
      }

      const resolveTime = (message) => {
        const value = message?.created_at;
        if (!value) {
          return null;
        }
        const time = new Date(value).getTime();
        return Number.isNaN(time) ? null : time;
      };

      const ascendingMessages = [...session.messages].sort((a, b) => {
        const aTime = resolveTime(a);
        const bTime = resolveTime(b);
        if (aTime === null && bTime === null) {
          return 0;
        }
        if (aTime === null) {
          return -1;
        }
        if (bTime === null) {
          return 1;
        }
        return aTime - bTime;
      });

      const tableMessages = [...ascendingMessages].reverse();

      const rows = tableMessages
        .map((message) => {
          const timestamp = formatDate(message.created_at);
          const role = message.role || "—";
          const content = message.content || "";
          return `
            <tr>
              <td class="cell cell-wrap">${escapeHtml(timestamp)}</td>
              <td class="cell cell-truncate">${escapeHtml(role)}</td>
              <td class="cell cell-wrap cell-content"><div class="message-detail">${escapeHtml(
                content
              )}</div></td>
            </tr>
          `;
        })
        .join("");

      tableBody.innerHTML = rows;
      const exportRows = ascendingMessages.map((message) => ({
        timestamp: normalizeValue(formatDate(message.created_at)),
        role: normalizeValue(message.role || "—"),
        content: normalizeValue(message.content || ""),
      }));
      tableExportText = JSON.stringify(exportRows, null, 2);
      if (copyTableButton) {
        copyTableButton.disabled = false;
        copyTableButton.textContent = "Copy";
      }
    })
    .catch((error) => {
      console.error("Failed to load session detail", error);
      renderError(error.message);
    })
    .finally(() => {
      revealApp();
    });
}

function bootstrap() {
  initThemeToggle();
  initSessionPage();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootstrap);
} else {
  bootstrap();
}
