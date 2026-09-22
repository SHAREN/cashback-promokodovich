(() => {
  const DATA_KEY = "cashback-promokodovich-data-v1";
  const BANK_COLORS = {
    vtb: "#2a7fff",
    alfa: "#ef3f4f",
    sber: "#32c96c",
    ozon: "#675cff"
  };

  const els = {
    banks: document.getElementById("banks"),
    period: document.getElementById("period"),
    mode: document.getElementById("mode"),
    updated: document.getElementById("updated"),
    empty: document.getElementById("empty"),
    retry: document.getElementById("retry"),
    install: document.getElementById("install")
  };

  let installPrompt = null;

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
    })[ch]);
  }

  function niceDate(value) {
    if (!value) return "время обновления неизвестно";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return "Обновлено " + new Intl.DateTimeFormat("ru-RU", {
      day: "numeric", month: "long", hour: "2-digit", minute: "2-digit"
    }).format(d);
  }

  function bankColor(slug) {
    return BANK_COLORS[slug] || "#77839b";
  }

  function entryHtml(entry) {
    const bits = [entry.limit_text, entry.conditions, entry.note].filter(Boolean);
    return `
      <div class="entry">
        <div class="rate">${escapeHtml(entry.cashback || "—")}</div>
        <div class="category">
          <div class="category-name">${escapeHtml(entry.category)}</div>
          ${bits.length ? `<div class="meta">${escapeHtml(bits.join(" · "))}</div>` : ""}
        </div>
      </div>`;
  }

  function render(data, source) {
    els.empty.classList.add("hidden");
    els.banks.classList.remove("hidden");
    els.period.textContent = data.period_label || data.period || "Текущий месяц";
    els.mode.textContent = source === "network" ? "актуально" : "офлайн-копия";
    els.mode.className = "mode " + (source === "network" ? "online" : "offline");
    els.updated.textContent = niceDate(data.updated_at || data.generated_at);

    els.banks.innerHTML = (data.banks || []).map(bank => {
      const entries = bank.entries || [];
      return `
        <article class="bank" style="--bank:${bankColor(bank.slug)}">
          <div class="bank-head">
            <div class="bank-title">
              <span class="bank-dot"></span>
              <div class="bank-name">${escapeHtml(bank.name)}</div>
            </div>
          </div>
          ${entries.length
            ? `<div class="entries">${entries.map(entryHtml).join("")}</div>`
            : '<div class="unfilled">Кэшбэки на этот месяц ещё не внесены</div>'}
        </article>`;
    }).join("");
  }

  function showEmpty() {
    els.banks.classList.add("hidden");
    els.empty.classList.remove("hidden");
    els.period.textContent = "—";
    els.mode.textContent = navigator.onLine ? "нет данных" : "нет сети";
    els.mode.className = "mode offline";
    els.updated.textContent = "Нет сохранённой копии";
  }

  function readCachedData() {
    try {
      return JSON.parse(localStorage.getItem(DATA_KEY) || "null");
    } catch (_) {
      return null;
    }
  }

  function renderCachedFirst() {
    const cached = readCachedData();
    if (cached) {
      render(cached, "cache");
      return true;
    }
    showEmpty();
    return false;
  }

  async function refreshFromNetwork() {
    try {
      const response = await fetch("./data.json?fresh=" + Date.now(), { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const data = await response.json();
      localStorage.setItem(DATA_KEY, JSON.stringify(data));
      const fromWorkerCache = response.headers.get("X-Cashback-Cache") === "1";
      render(data, fromWorkerCache ? "cache" : "network");
      return !fromWorkerCache;
    } catch (err) {
      const cached = readCachedData();
      if (cached) {
        render(cached, "cache");
        return false;
      }
      showEmpty();
      return false;
    }
  }

  function loadData() {
    renderCachedFirst();
    if (navigator.onLine) {
      refreshFromNetwork();
    }
  }

  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault();
    installPrompt = event;
    els.install.classList.remove("hidden");
  });

  els.install.addEventListener("click", async () => {
    if (!installPrompt) return;
    await installPrompt.prompt();
    installPrompt = null;
    els.install.classList.add("hidden");
  });

  els.retry.addEventListener("click", refreshFromNetwork);
  window.addEventListener("online", refreshFromNetwork);
  window.addEventListener("offline", () => {
    const cached = readCachedData();
    if (cached) render(cached, "cache");
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && navigator.onLine) refreshFromNetwork();
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => navigator.serviceWorker.register("./sw.js", { updateViaCache: "none" }).catch(() => {}));
  }

  if (navigator.storage && navigator.storage.persist) {
    navigator.storage.persist().catch(() => {});
  }

  loadData();
})();
