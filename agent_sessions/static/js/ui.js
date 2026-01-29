const THEME_KEY = "sessionViewerTheme";

const intlFormatOptions = {
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
};

export function escapeHtml(value) {
  if (value === undefined || value === null) {
    return "";
  }
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function formatDate(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString(undefined, intlFormatOptions);
}

function applyTheme(theme) {
  const isDark = theme === "dark";
  document.body.classList.toggle("theme-dark", isDark);
  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.textContent = isDark ? "Light" : "Dark";
    toggle.setAttribute("aria-pressed", String(isDark));
  }
}

export function initThemeToggle() {
  const toggle = document.getElementById("theme-toggle");
  const stored = localStorage.getItem(THEME_KEY);
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const initial = stored || (prefersDark ? "dark" : "light");
  applyTheme(initial);

  if (toggle) {
    toggle.addEventListener("click", () => {
      const next = document.body.classList.contains("theme-dark")
        ? "light"
        : "dark";
      applyTheme(next);
      localStorage.setItem(THEME_KEY, next);
    });
  }
}

export function revealApp() {
  document.body.classList.remove("is-loading");
  const loading = document.getElementById("app-loading");
  if (loading) {
    loading.remove();
  }
}

export function copyTextToClipboard(text) {
  if (!text) {
    return Promise.reject(new Error("Nothing to copy"));
  }

  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text);
  }

  return new Promise((resolve, reject) => {
    const temp = document.createElement("textarea");
    temp.value = text;
    temp.style.position = "fixed";
    temp.style.opacity = "0";
    document.body.appendChild(temp);
    temp.focus();
    temp.select();
    try {
      const succeeded = document.execCommand("copy");
      if (succeeded) {
        resolve();
      } else {
        reject(new Error("Copy command failed"));
      }
    } catch (error) {
      reject(error);
    } finally {
      document.body.removeChild(temp);
    }
  });
}

export function attachCopyHandlers(scope) {
  const buttons = Array.from(scope.querySelectorAll(".copy-session"));
  buttons.forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const sessionId = button.dataset.sessionId || "";
      if (!sessionId) {
        return;
      }

      const original = button.textContent || "Copy";
      const handleSuccess = () => {
        button.textContent = "Copied!";
        button.disabled = true;
        setTimeout(() => {
          button.textContent = original;
          button.disabled = false;
        }, 1500);
      };

      const handleFailure = () => {
        button.textContent = "Failed";
        setTimeout(() => {
          button.textContent = original;
        }, 1500);
      };
      copyTextToClipboard(sessionId)
        .then(handleSuccess)
        .catch((err) => {
          console.error("Clipboard write failed", err);
          handleFailure();
        });
    });
  });
}
