/**
 * Sanchay AI - Lightweight Vanilla SVG Chart Library (Production)
 *
 * Implements clean, responsive, pure SVG vector charts without external CDNs
 * or third-party dependencies:
 * - Line / Area Trend Charts (daily and monthly sales & gross profit)
 * - Horizontal Bar Charts (top-selling products by quantity and revenue)
 * - Donut / Segment Charts (inventory category & status distributions)
 * - Grouped Bar Charts (transfer activity by status)
 *
 * Fully integrated with Sanchay AI Design Tokens (Dark / Light Theme).
 */

(function () {
    'use strict';

    function getThemeColors() {
        var style = getComputedStyle(document.documentElement);
        return {
            primary: style.getPropertyValue('--color-primary').trim() || '#2DD4A0',
            primaryHover: style.getPropertyValue('--color-primary-hover').trim() || '#25B88B',
            secondary: style.getPropertyValue('--color-secondary').trim() || '#F5B544',
            border: style.getPropertyValue('--color-border').trim() || '#2C3733',
            text: style.getPropertyValue('--color-text').trim() || '#ECF1EE',
            textMuted: style.getPropertyValue('--color-text-muted').trim() || '#9AA9A2',
            surface: style.getPropertyValue('--color-surface').trim() || '#171E1B',
            surfaceElevated: style.getPropertyValue('--color-surface-elevated').trim() || '#1F2824',
            info: style.getPropertyValue('--color-info').trim() || '#5EC4D9',
            success: style.getPropertyValue('--color-success').trim() || '#4ADE80',
            danger: style.getPropertyValue('--color-danger').trim() || '#EF6B5B',
        };
    }

    window.SanchayCharts = {
        /**
         * Render an interactive responsive SVG Line/Area Trend Chart.
         * @param {string|HTMLElement} target - Container ID or element.
         * @param {Array<{label: string, revenue: number, sales_count: number}>} data
         * @param {Object} options
         */
        renderTrendLineChart: function (target, data, options) {
            var container = typeof target === 'string' ? document.getElementById(target) : target;
            if (!container) return;
            container.innerHTML = '';

            if (!data || data.length === 0) {
                container.innerHTML = '<div class="empty-state" style="padding: 2rem;">No trend data available for this range.</div>';
                return;
            }

            var colors = getThemeColors();
            var opts = options || {};
            var valKey = opts.valueKey || 'revenue';
            var width = container.clientWidth || 500;
            var height = 220;
            var padLeft = 55;
            var padRight = 20;
            var padTop = 20;
            var padBottom = 35;

            var plotW = width - padLeft - padRight;
            var plotH = height - padTop - padBottom;

            var maxVal = 0;
            for (var i = 0; i < data.length; i++) {
                if (data[i][valKey] > maxVal) maxVal = data[i][valKey];
            }
            if (maxVal === 0) maxVal = 100;
            maxVal = Math.ceil(maxVal * 1.15); // headroom

            var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
            svg.setAttribute('class', 'chart-svg');
            svg.setAttribute('width', '100%');
            svg.setAttribute('height', height.toString());
            svg.style.display = 'block';
            svg.style.width = '100%';
            svg.style.height = height + 'px';
            container.style.width = '100%';
            container.style.minHeight = height + 'px';

            // Grid lines & Y axis ticks (4 ticks)
            var numTicks = 4;
            for (var t = 0; t <= numTicks; t++) {
                var tickVal = Math.round((maxVal / numTicks) * t);
                var tickY = padTop + plotH - (plotH * (t / numTicks));

                var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
                line.setAttribute('x1', padLeft);
                line.setAttribute('x2', width - padRight);
                line.setAttribute('y1', tickY);
                line.setAttribute('y2', tickY);
                line.setAttribute('stroke', colors.border);
                line.setAttribute('stroke-dasharray', t === 0 ? 'none' : '3,3');
                svg.appendChild(line);

                var txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                txt.setAttribute('x', padLeft - 8);
                txt.setAttribute('y', tickY + 4);
                txt.setAttribute('text-anchor', 'end');
                txt.setAttribute('font-size', '10');
                txt.setAttribute('fill', colors.textMuted);
                txt.textContent = tickVal >= 1000 ? (tickVal / 1000).toFixed(1) + 'k' : tickVal;
                svg.appendChild(txt);
            }

            // Calculate point coordinates
            var points = [];
            var stepX = data.length > 1 ? plotW / (data.length - 1) : plotW / 2;
            for (var j = 0; j < data.length; j++) {
                var x = padLeft + (data.length > 1 ? j * stepX : plotW / 2);
                var val = data[j][valKey] || 0;
                var y = padTop + plotH - ((val / maxVal) * plotH);
                points.push({ x: x, y: y, item: data[j] });
            }

            // Area path
            if (points.length > 0) {
                var areaD = 'M ' + points[0].x + ' ' + (padTop + plotH);
                for (var a = 0; a < points.length; a++) {
                    areaD += ' L ' + points[a].x + ' ' + points[a].y;
                }
                areaD += ' L ' + points[points.length - 1].x + ' ' + (padTop + plotH) + ' Z';

                var areaPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                areaPath.setAttribute('d', areaD);
                areaPath.setAttribute('fill', 'rgba(45, 212, 160, 0.12)');
                svg.appendChild(areaPath);

                // Line path
                var lineD = 'M ' + points[0].x + ' ' + points[0].y;
                for (var l = 1; l < points.length; l++) {
                    lineD += ' L ' + points[l].x + ' ' + points[l].y;
                }
                var linePath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                linePath.setAttribute('d', lineD);
                linePath.setAttribute('fill', 'none');
                linePath.setAttribute('stroke', colors.primary);
                linePath.setAttribute('stroke-width', '2.5');
                svg.appendChild(linePath);
            }

            // Point markers and X-axis labels
            var labelStep = Math.max(1, Math.floor(data.length / 7));
            for (var p = 0; p < points.length; p++) {
                var pt = points[p];

                // Point circle
                var circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                circle.setAttribute('cx', pt.x);
                circle.setAttribute('cy', pt.y);
                circle.setAttribute('r', '3.5');
                circle.setAttribute('fill', colors.surface);
                circle.setAttribute('stroke', colors.primary);
                circle.setAttribute('stroke-width', '2');
                svg.appendChild(circle);

                // Tooltip title element
                var titleEl = document.createElementNS('http://www.w3.org/2000/svg', 'title');
                titleEl.textContent = (pt.item.label || pt.item.period) + '\nRevenue: \u20B9' + (pt.item.revenue || 0).toLocaleString() + '\nSales: ' + (pt.item.sales_count || 0);
                circle.appendChild(titleEl);

                // X label
                if (p % labelStep === 0 || p === points.length - 1) {
                    var xTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    xTxt.setAttribute('x', pt.x);
                    xTxt.setAttribute('y', height - 8);
                    xTxt.setAttribute('text-anchor', 'middle');
                    xTxt.setAttribute('font-size', '10');
                    xTxt.setAttribute('fill', colors.textMuted);
                    xTxt.textContent = pt.item.label || pt.item.period;
                    svg.appendChild(xTxt);
                }
            }

            container.appendChild(svg);
        },

        /**
         * Render a responsive Horizontal Bar Chart.
         */
        renderHorizontalBars: function (target, items, valKey, labelKey, options) {
            var container = typeof target === 'string' ? document.getElementById(target) : target;
            if (!container) return;
            container.innerHTML = '';

            if (!items || items.length === 0) {
                container.innerHTML = '<div class="empty-state" style="padding: 1.5rem;">No ranking data recorded.</div>';
                return;
            }

            var colors = getThemeColors();
            var opts = options || {};
            var prefix = opts.prefix || '';
            var suffix = opts.suffix || '';
            var barColor = opts.color || colors.primary;

            var maxVal = 0;
            for (var i = 0; i < items.length; i++) {
                if (items[i][valKey] > maxVal) maxVal = items[i][valKey];
            }
            if (maxVal === 0) maxVal = 1;

            var list = document.createElement('div');
            list.style.display = 'flex';
            list.style.flexDirection = 'column';
            list.style.gap = '0.75rem';
            list.style.padding = '0.5rem 0';

            for (var k = 0; k < items.length; k++) {
                var row = items[k];
                var val = row[valKey] || 0;
                var pct = Math.min(100, Math.round((val / maxVal) * 100));

                var rowEl = document.createElement('div');
                rowEl.style.display = 'flex';
                rowEl.style.flexDirection = 'column';
                rowEl.style.gap = '0.25rem';

                var metaEl = document.createElement('div');
                metaEl.style.display = 'flex';
                metaEl.style.justifyContent = 'space-between';
                metaEl.style.fontSize = '0.86rem';

                var nameSpan = document.createElement('span');
                nameSpan.style.fontWeight = '600';
                nameSpan.style.color = colors.text;
                nameSpan.textContent = (k + 1) + '. ' + (row[labelKey] || 'Item');

                var valSpan = document.createElement('strong');
                valSpan.style.color = colors.primary;
                valSpan.textContent = prefix + (typeof val === 'number' ? val.toLocaleString() : val) + suffix;

                metaEl.appendChild(nameSpan);
                metaEl.appendChild(valSpan);
                rowEl.appendChild(metaEl);

                var track = document.createElement('div');
                track.style.height = '8px';
                track.style.background = colors.surfaceElevated;
                track.style.border = '1px solid ' + colors.border;
                track.style.borderRadius = '999px';
                track.style.overflow = 'hidden';

                var fill = document.createElement('div');
                fill.style.height = '100%';
                fill.style.width = pct + '%';
                fill.style.background = barColor;
                fill.style.borderRadius = '999px';
                fill.style.transition = 'width 0.4s ease';

                track.appendChild(fill);
                rowEl.appendChild(track);
                list.appendChild(rowEl);
            }

            container.appendChild(list);
        },

        /**
         * Render a responsive SVG Donut Chart with a structured side legend.
         */
        renderDonutChart: function (target, segments, options) {
            var container = typeof target === 'string' ? document.getElementById(target) : target;
            if (!container) return;
            container.innerHTML = '';

            if (!segments || segments.length === 0) {
                container.innerHTML = '<div class="empty-state" style="padding: 1.5rem;">No categorical distribution data.</div>';
                return;
            }

            var colors = getThemeColors();
            var palette = [colors.primary, colors.secondary, colors.info, '#A78BFA', colors.danger, '#F472B6'];
            var total = 0;
            for (var i = 0; i < segments.length; i++) {
                total += (segments[i].value || 0);
            }
            if (total === 0) total = 1;

            var size = 160;
            var strokeWidth = 26;
            var radius = (size - strokeWidth) / 2;
            var circumference = 2 * Math.PI * radius;

            var wrap = document.createElement('div');
            wrap.style.display = 'flex';
            wrap.style.alignItems = 'center';
            wrap.style.justifyContent = 'center';
            wrap.style.gap = '1.5rem';
            wrap.style.flexWrap = 'wrap';

            var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('width', size);
            svg.setAttribute('height', size);
            svg.setAttribute('viewBox', '0 0 ' + size + ' ' + size);

            var g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
            g.setAttribute('transform', 'rotate(-90 ' + (size / 2) + ' ' + (size / 2) + ')');

            var accumulatedPct = 0;
            for (var s = 0; s < segments.length; s++) {
                var seg = segments[s];
                var pct = (seg.value || 0) / total;
                var dashLen = pct * circumference;
                var dashOffset = -accumulatedPct * circumference;
                accumulatedPct += pct;

                var segColor = seg.color || palette[s % palette.length];

                var circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                circle.setAttribute('cx', size / 2);
                circle.setAttribute('cy', size / 2);
                circle.setAttribute('r', radius);
                circle.setAttribute('fill', 'transparent');
                circle.setAttribute('stroke', segColor);
                circle.setAttribute('stroke-width', strokeWidth);
                circle.setAttribute('stroke-dasharray', dashLen + ' ' + (circumference - dashLen));
                circle.setAttribute('stroke-dashoffset', dashOffset);

                var title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
                title.textContent = seg.label + ': ' + (seg.value || 0).toLocaleString() + ' (' + Math.round(pct * 100) + '%)';
                circle.appendChild(title);

                g.appendChild(circle);
            }
            svg.appendChild(g);

            // Center total label
            var centerVal = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            centerVal.setAttribute('x', size / 2);
            centerVal.setAttribute('y', size / 2 + 5);
            centerVal.setAttribute('text-anchor', 'middle');
            centerVal.setAttribute('font-size', '15');
            centerVal.setAttribute('font-weight', '700');
            centerVal.setAttribute('fill', colors.text);
            centerVal.textContent = total >= 1000 ? (total / 1000).toFixed(1) + 'k' : total;
            svg.appendChild(centerVal);

            wrap.appendChild(svg);

            // Legend
            var legend = document.createElement('div');
            legend.style.display = 'flex';
            legend.style.flexDirection = 'column';
            legend.style.gap = '0.35rem';
            legend.style.minWidth = '140px';

            for (var l = 0; l < segments.length; l++) {
                var item = segments[l];
                var itemColor = item.color || palette[l % palette.length];
                var itemPct = Math.round(((item.value || 0) / total) * 100);

                var legRow = document.createElement('div');
                legRow.style.display = 'flex';
                legRow.style.alignItems = 'center';
                legRow.style.justifyContent = 'space-between';
                legRow.style.fontSize = '0.84rem';
                legRow.style.gap = '0.5rem';

                var left = document.createElement('span');
                left.style.display = 'inline-flex';
                left.style.alignItems = 'center';
                left.style.gap = '0.4rem';
                left.style.color = colors.text;

                var dot = document.createElement('span');
                dot.style.width = '8px';
                dot.style.height = '8px';
                dot.style.borderRadius = '50%';
                dot.style.background = itemColor;
                dot.style.display = 'inline-block';
                dot.style.flexShrink = '0';

                left.appendChild(dot);
                left.appendChild(document.createTextNode(item.label));

                var right = document.createElement('strong');
                right.style.color = colors.textMuted;
                right.textContent = itemPct + '%';

                legRow.appendChild(left);
                legRow.appendChild(right);
                legend.appendChild(legRow);
            }

        }
    };

    function autoInitCharts() {
        var trendEls = document.querySelectorAll('[data-chart-type="trend"]');
        for (var i = 0; i < trendEls.length; i++) {
            var el = trendEls[i];
            var raw = el.getAttribute('data-chart-data');
            if (raw) {
                try {
                    var data = JSON.parse(raw);
                    var valKey = el.getAttribute('data-chart-value-key') || 'revenue';
                    window.SanchayCharts.renderTrendLineChart(el, data, { valueKey: valKey });
                } catch (e) {
                    console.error('Failed to parse chart data:', e);
                }
            }
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', autoInitCharts);
    } else {
        autoInitCharts();
    }

    var resizeTimer;
    window.addEventListener('resize', function () {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(autoInitCharts, 150);
    });
})();
