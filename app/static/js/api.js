/**
 * Sanchay AI — Vanilla JavaScript API & UI Helpers
 * - CSRF token injection on all state-changing fetch requests
 * - Toast notification feedback system
 * - Theme toggle (Dark / Light) with localStorage persistence
 * - Double-submission prevention on buttons and forms
 */

(function (window) {
    "use strict";

    // 1. CSRF Token extraction
    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") : "";
    }

    // 2. Core Fetch Helper
    async function sanchayFetch(url, options) {
        const opts = Object.assign({ credentials: "same-origin" }, options || {});
        const headers = new Headers(opts.headers || {});

        if (!headers.has("Accept")) {
            headers.set("Accept", "application/json");
        }

        const method = (opts.method || "GET").toUpperCase();
        if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
            const csrfToken = getCsrfToken();
            if (csrfToken && !headers.has("X-CSRFToken")) {
                headers.set("X-CSRFToken", csrfToken);
            }
            if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
                headers.set("Content-Type", "application/json");
                opts.body = JSON.stringify(opts.body);
            }
        }

        opts.headers = headers;
        const response = await window.fetch(url, opts);
        const contentType = response.headers.get("content-type") || "";
        const data = contentType.includes("application/json")
            ? await response.json()
            : await response.text();

        return {
            ok: response.ok,
            status: response.status,
            data: data,
        };
    }

    // 3. Convenience JSON fetcher
    async function sanchayFetchJSON(url, options) {
        const res = await sanchayFetch(url, options);
        return res.data;
    }

    // 4. Toast Notification System
    function ensureToastContainer() {
        let container = document.getElementById("toast-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "toast-container";
            document.body.appendChild(container);
        }
        return container;
    }

    function showToast(message, type = "info", duration = 4000) {
        const container = ensureToastContainer();
        const toast = document.createElement("div");
        toast.className = `sanchay-toast toast-${type}`;

        const iconMap = {
            success: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: var(--color-success); flex-shrink: 0;"><polyline points="20 6 9 17 4 12"></polyline></svg>`,
            error: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: var(--color-danger); flex-shrink: 0;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>`,
            info: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: var(--color-info); flex-shrink: 0;"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`,
            warning: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="color: var(--color-warning); flex-shrink: 0;"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`
        };

        const icon = iconMap[type] || iconMap.info;
        toast.innerHTML = `${icon}<span>${message}</span>`;
        container.appendChild(toast);

        setTimeout(() => {
            toast.style.transition = "opacity 0.25s ease, transform 0.25s ease";
            toast.style.opacity = "0";
            toast.style.transform = "translateX(40px)";
            setTimeout(() => {
                if (toast.parentNode) toast.parentNode.removeChild(toast);
            }, 250);
        }, duration);
    }

    // 5. Theme Toggle Logic
    function initTheme() {
        const toggleBtn = document.getElementById("theme-toggle-btn");
        if (!toggleBtn) return;

        toggleBtn.addEventListener("click", function () {
            const currentTheme = document.documentElement.getAttribute("data-theme") || "dark";
            const newTheme = currentTheme === "light" ? "dark" : "light";
            document.documentElement.setAttribute("data-theme", newTheme);
            localStorage.setItem("sanchay_theme", newTheme);

            // Re-render any active SVG charts if available
            window.dispatchEvent(new Event("themechange"));
        });
    }

    // 6. Form Double-Click Guard
    function initDoubleClickGuards() {
        document.querySelectorAll("form").forEach(form => {
            form.addEventListener("submit", function () {
                const submitBtn = form.querySelector('button[type="submit"]');
                if (submitBtn && !submitBtn.dataset.preventDisabled) {
                    setTimeout(() => {
                        submitBtn.disabled = true;
                        submitBtn.style.opacity = "0.6";
                    }, 10);
                }
            });
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        initTheme();
        initDoubleClickGuards();
    });

    const SanchayAPI = {
        getCsrfToken: getCsrfToken,
        fetch: sanchayFetch,
        fetchJSON: sanchayFetchJSON,
        toast: showToast,
    };

    window.SanchayAPI = SanchayAPI;
    window.sanchayAPI = SanchayAPI;
    window.showToast = showToast;
})(window);
