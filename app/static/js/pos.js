/**
 * Sanchay AI — Point-of-Sale (POS) Interactive Cart & Receipt Print Helper (Phase 4)
 *
 * Manages temporary client-side cart state on the POS screen while ensuring
 * all prices, availability, and FEFO batch deductions are revalidated on the server.
 */
(function () {
    "use strict";

    document.addEventListener("DOMContentLoaded", function () {
        var printBtn = document.getElementById("print-receipt-btn");
        if (printBtn) {
            printBtn.addEventListener("click", function () {
                window.print();
            });
        }

        var cartTbody = document.getElementById("pos-cart-tbody");
        var cartJsonInput = document.getElementById("pos-cart-json");
        var discountInput = document.getElementById("discount_amount");
        var subtotalEl = document.getElementById("pos-subtotal-display");
        var discountEl = document.getElementById("pos-discount-display");
        var finalTotalEl = document.getElementById("pos-final-total-display");
        var clearBtn = document.getElementById("pos-clear-cart-btn");
        var checkoutForm = document.getElementById("pos-checkout-form");

        if (!cartTbody || !cartJsonInput) {
            return;
        }

        var cart = [];

        function formatInr(amount) {
            return "\u20B9" + Number(amount || 0).toFixed(2);
        }

        function syncCartDisplay() {
            while (cartTbody.firstChild) {
                cartTbody.removeChild(cartTbody.firstChild);
            }

            if (cart.length === 0) {
                var emptyRow = document.createElement("tr");
                var emptyCell = document.createElement("td");
                emptyCell.colSpan = 5;
                emptyCell.className = "text-muted";
                emptyCell.style.textAlign = "center";
                emptyCell.textContent = "Click + Add on any sellable product to add items to the cart.";
                emptyRow.appendChild(emptyCell);
                cartTbody.appendChild(emptyRow);
            } else {
                cart.forEach(function (item, idx) {
                    var tr = document.createElement("tr");

                    var tdProd = document.createElement("td");
                    var strongName = document.createElement("strong");
                    strongName.textContent = item.name;
                    var skuDiv = document.createElement("div");
                    skuDiv.className = "text-muted text-xs";
                    skuDiv.textContent = item.sku + " (Max: " + item.maxSellable + " " + item.unit + ")";
                    tdProd.appendChild(strongName);
                    tdProd.appendChild(skuDiv);

                    var tdPrice = document.createElement("td");
                    tdPrice.textContent = formatInr(item.unitPrice);

                    var tdQty = document.createElement("td");
                    var qtyInput = document.createElement("input");
                    qtyInput.type = "number";
                    qtyInput.min = "1";
                    qtyInput.max = String(item.maxSellable);
                    qtyInput.value = String(item.quantity);
                    qtyInput.className = "form-input";
                    qtyInput.style.width = "76px";
                    qtyInput.addEventListener("change", function () {
                        var nextQty = parseInt(qtyInput.value, 10);
                        if (isNaN(nextQty) || nextQty < 1) {
                            nextQty = 1;
                        }
                        if (nextQty > item.maxSellable) {
                            nextQty = item.maxSellable;
                        }
                        cart[idx].quantity = nextQty;
                        syncCartDisplay();
                    });
                    tdQty.appendChild(qtyInput);

                    var tdLineTotal = document.createElement("td");
                    var lineStrong = document.createElement("strong");
                    lineStrong.textContent = formatInr(item.unitPrice * item.quantity);
                    tdLineTotal.appendChild(lineStrong);

                    var tdRemove = document.createElement("td");
                    var rmBtn = document.createElement("button");
                    rmBtn.type = "button";
                    rmBtn.className = "btn btn-outline btn-sm";
                    rmBtn.textContent = "Remove";
                    rmBtn.addEventListener("click", function () {
                        cart.splice(idx, 1);
                        syncCartDisplay();
                    });
                    tdRemove.appendChild(rmBtn);

                    tr.appendChild(tdProd);
                    tr.appendChild(tdPrice);
                    tr.appendChild(tdQty);
                    tr.appendChild(tdLineTotal);
                    tr.appendChild(tdRemove);
                    cartTbody.appendChild(tr);
                });
            }

            var subtotal = cart.reduce(function (sum, it) {
                return sum + it.unitPrice * it.quantity;
            }, 0);
            var billDiscount = discountInput ? parseFloat(discountInput.value || "0") : 0;
            if (isNaN(billDiscount) || billDiscount < 0) {
                billDiscount = 0;
            }
            var finalTotal = Math.max(0, subtotal - billDiscount);

            if (subtotalEl) subtotalEl.textContent = formatInr(subtotal);
            if (discountEl) discountEl.textContent = formatInr(billDiscount);
            if (finalTotalEl) finalTotalEl.textContent = formatInr(finalTotal);

            var payloadItems = cart.map(function (it) {
                return {
                    product_id: it.productId,
                    quantity: it.quantity,
                    line_discount: "0.00"
                };
            });
            cartJsonInput.value = JSON.stringify(payloadItems);
        }

        var addButtons = document.querySelectorAll(".js-add-to-cart");
        addButtons.forEach(function (btn) {
            btn.addEventListener("click", function () {
                var pid = parseInt(btn.getAttribute("data-product-id"), 10);
                var sku = btn.getAttribute("data-sku") || "";
                var name = btn.getAttribute("data-name") || "";
                var unit = btn.getAttribute("data-unit") || "unit";
                var price = parseFloat(btn.getAttribute("data-price") || "0");
                var sellable = parseInt(btn.getAttribute("data-sellable") || "0", 10);

                var existing = cart.find(function (c) {
                    return c.productId === pid;
                });
                if (existing) {
                    if (existing.quantity < sellable) {
                        existing.quantity += 1;
                    }
                } else if (sellable > 0) {
                    cart.push({
                        productId: pid,
                        sku: sku,
                        name: name,
                        unit: unit,
                        unitPrice: price,
                        quantity: 1,
                        maxSellable: sellable
                    });
                }
                syncCartDisplay();
            });
        });

        if (discountInput) {
            discountInput.addEventListener("input", syncCartDisplay);
        }

        if (clearBtn) {
            clearBtn.addEventListener("click", function () {
                cart = [];
                syncCartDisplay();
            });
        }

        if (checkoutForm) {
            checkoutForm.addEventListener("submit", function () {
                syncCartDisplay();
            });
        }
    });
})();
