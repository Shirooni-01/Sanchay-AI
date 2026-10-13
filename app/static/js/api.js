/**
 * Sanchay AI — Vanilla JavaScript API Helper (Phase 1)
 * Automatically attaches the X-CSRFToken header from <meta name="csrf-token">
 * to all state-changing fetch() requests.
 */

(function (window) {
    "use strict";

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute("content") : "";
    }

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

    window.SanchayAPI = {
        getCsrfToken: getCsrfToken,
        fetch: sanchayFetch,
    };
})(window);
