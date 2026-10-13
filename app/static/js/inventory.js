/**
 * Sanchay AI — Phase 3 Inventory UI Enhancements
 * Auto-populates default purchase cost and toggles expiry date requirement
 * when selecting a product on the Receive Stock page.
 */
(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        var productSelect = document.getElementById("product_id");
        var costInput = document.getElementById("unit_cost");
        var expiryInput = document.getElementById("expiry_date");

        if (!productSelect) {
            return;
        }

        function syncSelectedProductMeta() {
            var selectedOption = productSelect.options[productSelect.selectedIndex];
            if (!selectedOption || !selectedOption.value) {
                return;
            }
            var defaultCost = selectedOption.getAttribute("data-cost");
            var isPerishable = selectedOption.getAttribute("data-perishable") === "true";

            if (costInput && defaultCost) {
                costInput.value = defaultCost;
            }
            if (expiryInput) {
                expiryInput.required = isPerishable;
            }
        }

        productSelect.addEventListener("change", syncSelectedProductMeta);
        syncSelectedProductMeta();
    });
})();
