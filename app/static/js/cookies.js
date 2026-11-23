(function () {
  const storageKey = "consultiq_cookie_preferences";
  const banner = document.querySelector("[data-cookie-banner]");
  const modal = document.querySelector("[data-cookie-modal]");
  const analyticsCheckbox = document.querySelector("[data-cookie-analytics]");

  function readPrefs() {
    try {
      return JSON.parse(localStorage.getItem(storageKey) || "null");
    } catch (_err) {
      return null;
    }
  }

  function writePrefs(prefs) {
    localStorage.setItem(storageKey, JSON.stringify(prefs));
    document.documentElement.dataset.analyticsConsent = prefs.analytics ? "granted" : "denied";
  }

  function showBannerIfNeeded() {
    const prefs = readPrefs();
    if (!banner) return;
    banner.hidden = !!prefs;
    if (analyticsCheckbox) {
      analyticsCheckbox.checked = !!(prefs && prefs.analytics);
    }
    document.documentElement.dataset.analyticsConsent = prefs && prefs.analytics ? "granted" : "denied";
  }

  function closeModal() {
    if (modal) modal.hidden = true;
  }

  document.querySelectorAll("[data-cookie-action]").forEach((button) => {
    button.addEventListener("click", function () {
      const action = button.getAttribute("data-cookie-action");
      if (action === "accept-all") {
        writePrefs({ analytics: true, updatedAt: new Date().toISOString() });
        if (banner) banner.hidden = true;
        closeModal();
      }
      if (action === "reject-nonessential") {
        writePrefs({ analytics: false, updatedAt: new Date().toISOString() });
        if (banner) banner.hidden = true;
        closeModal();
      }
      if (action === "manage" && modal) {
        modal.hidden = false;
      }
      if (action === "save-preferences") {
        writePrefs({ analytics: !!(analyticsCheckbox && analyticsCheckbox.checked), updatedAt: new Date().toISOString() });
        if (banner) banner.hidden = true;
        closeModal();
      }
      if (action === "close-modal") {
        closeModal();
      }
    });
  });

  showBannerIfNeeded();
})();
