/* P7 dashboard: filters auto-submit, ECharts from <script id="chart-data">, Leaflet map from <script id="map-data">.
   No inline scripts (strict CSP); all data arrives as JSON in the page. */
(function () {
  "use strict";
  // Chart axes stay left-to-right (time runs left to right) in both languages, like the PDF charts;
  // only titles/legends follow the page direction.
  var rtl = document.documentElement.dir === "rtl";
  var palette = ["#2f6fb0", "#d9822b", "#2e8b57", "#c23b3b", "#7b5ea7", "#1b9aaa", "#b8860b", "#e05780",
                 "#4f772d", "#6c757d", "#0b7285", "#a0522d", "#5c7cfa"];
  var font = getComputedStyle(document.body).fontFamily;

  // filters: submit on change (site checkboxes: when the dropdown closes)
  document.querySelectorAll("form[data-autosubmit]").forEach(function (form) {
    form.querySelectorAll("select").forEach(function (el) {
      el.addEventListener("change", function () { form.submit(); });
    });
    var pick = form.querySelector("details.sitepick");
    if (pick) {
      var dirty = false;
      pick.addEventListener("change", function () { dirty = true; });
      pick.addEventListener("toggle", function () { if (!pick.open && dirty) form.submit(); });
      document.addEventListener("click", function (e) { if (pick.open && !pick.contains(e.target)) pick.open = false; });
    }
  });

  function readJSON(id) {
    var el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }

  function base(title) {
    return {
      color: palette,
      textStyle: { fontFamily: font },
      title: { text: title, left: rtl ? "right" : "left", textStyle: { fontSize: 14, fontWeight: 600 } },
      tooltip: { trigger: "axis", confine: true },
      legend: { type: "scroll", bottom: 0 },
      grid: { left: 48, right: 48, top: 44, bottom: 52, containLabel: false },
      animation: false
    };
  }

  var monthNames = null;  // from chart-data, so axis labels follow the page language

  function timeAxis() {
    return { type: "time", axisLabel: { hideOverlap: true, formatter: function (v) {
      var d = new Date(v);
      return d.getDate() === 1 && monthNames ? monthNames[d.getMonth()] : String(d.getDate());
    } } };
  }

  function axisY(unit, extra) {
    return Object.assign({ type: "value", scale: true, name: unit || "", nameTextStyle: { align: "center" },
                           splitLine: { lineStyle: { color: "#eef1f5" } } }, extra || {});
  }

  var builders = {
    lines: function (c) {  // multi-series time lines (overview daily averages)
      var o = base(c.title);
      o.xAxis = timeAxis();
      o.yAxis = axisY(c.unit, { position: "left" });
      o.series = c.series.map(function (s) { return { name: s.name, type: "line", showSymbol: false, data: s.data, lineStyle: { width: 1.5 } }; });
      return o;
    },
    lines_cat: function (c) {
      var o = base(c.title);
      o.xAxis = { type: "category", data: c.x };
      o.yAxis = axisY(c.unit, { position: "left" });
      o.series = c.series.map(function (s) { return { name: s.name, type: "line", data: s.data, connectNulls: false }; });
      return o;
    },
    bars: function (c) {
      var o = base(c.title);
      var two = c.series.some(function (s) { return s.axis === 1; });
      o.xAxis = { type: "category", data: c.categories, axisLabel: { rotate: 45, fontSize: 10 } };
      o.yAxis = [axisY(c.unit, { position: "left", scale: false })];
      if (two) o.yAxis.push(axisY("", { position: "right", splitLine: { show: false }, scale: false }));
      o.grid.bottom = 80;
      o.series = c.series.map(function (s) {
        return s.axis === 1 ? { name: s.name, type: "line", yAxisIndex: 1, data: s.data, symbolSize: 7, lineStyle: { width: 0 } }
                            : { name: s.name, type: "bar", data: s.data };
      });
      return o;
    },
    timeseries: function (c) {  // one site, hourly: limits, out-of-range points, unreliable periods (grey)
      var o = base(c.title);
      o.xAxis = timeAxis();
      // keep both limit lines in view
      o.yAxis = axisY(c.unit, { position: "left",
        min: function (v) { return Math.floor((Math.min(v.min, c.min) - 1) / 5) * 5; },
        max: function (v) { return Math.ceil((Math.max(v.max, c.max) + 1) / 5) * 5; } });
      o.dataZoom = [{ type: "inside" }, { type: "slider", height: 18, bottom: 26 }];
      o.grid.bottom = 76;
      var band = c.stuck.map(function (r) { return [{ xAxis: r[0], name: c.stuck_name }, { xAxis: r[1] }]; });
      o.series = [
        { name: c.name, type: "line", showSymbol: false, data: c.data, lineStyle: { width: 1.2 }, connectNulls: false,
          markLine: { silent: true, symbol: "none", lineStyle: { color: "#c23b3b", type: "dashed" },
                      data: [{ yAxis: c.min, name: c.limit_names[0] }, { yAxis: c.max, name: c.limit_names[1] }],
                      label: { formatter: "{b}: {c}", position: "insideEndTop" } },
          markArea: { silent: true, itemStyle: { color: "rgba(100,110,120,0.22)" }, label: { color: "#444" }, data: band } },
        { name: c.out_name, type: "scatter", data: c.out, symbolSize: 4, itemStyle: { color: "#c23b3b" } },
        { name: c.stuck_name, type: "line", data: [], itemStyle: { color: "rgba(100,110,120,0.5)" } }
      ];
      return o;
    },
    band: function (c) {  // daily min/avg/max
      var o = base(c.title);
      o.xAxis = { type: "category", data: c.x, boundaryGap: false };
      o.yAxis = axisY(c.unit, { position: "left" });
      o.series = [
        { name: c.names[0], type: "line", data: c.min, showSymbol: false, lineStyle: { width: 1, type: "dotted" } },
        { name: c.names[1], type: "line", data: c.avg, showSymbol: false, lineStyle: { width: 2 } },
        { name: c.names[2], type: "line", data: c.max, showSymbol: false, lineStyle: { width: 1, type: "dotted" } }
      ];
      o.color = ["#5fb3e6", "#2f6fb0", "#d9822b"];
      return o;
    },
    profile: function (c) {  // average by hour of day
      var o = base(c.title);
      var two = c.series.some(function (s) { return s.axis === 1; });
      o.xAxis = { type: "category", data: c.x, axisLabel: { interval: 2 } };
      o.yAxis = [axisY(c.unit, { position: "left" })];
      if (two) o.yAxis.push(axisY(c.unit2, { position: "right", splitLine: { show: false } }));
      o.series = c.series.map(function (s) { return { name: s.name, type: "line", yAxisIndex: s.axis || 0, data: s.data, smooth: true, showSymbol: false }; });
      return o;
    }
  };

  var data = readJSON("chart-data");
  var charts = [];
  if (data && data.month_names) monthNames = data.month_names;
  if (data && window.echarts) {
    document.querySelectorAll("[data-chart]").forEach(function (el) {
      var c = data[el.getAttribute("data-chart")];
      if (!c || !builders[c.type]) return;
      var chart = echarts.init(el, null, { renderer: "canvas" });
      chart.setOption(builders[c.type](c));
      charts.push(chart);
    });
    window.addEventListener("resize", function () { charts.forEach(function (ch) { ch.resize(); }); });
  }

  var map = readJSON("map-data");
  var mapEl = document.getElementById("map");
  if (map && mapEl && window.L) {
    var colours = { red: "#c23b3b", amber: "#d4a017", green: "#2e8b57" };
    var m = L.map(mapEl, { scrollWheelZoom: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 12, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(m);
    var bounds = [];
    map.markers.forEach(function (s) {
      var mk = L.circleMarker([s.lat, s.lon], { radius: 9, weight: 2, color: "#fff", fillColor: colours[s.status], fillOpacity: 0.95 });
      var box = document.createElement("div");
      var title = document.createElement("strong");
      title.textContent = s.code + " · " + s.name;
      var line = document.createElement("div");
      line.textContent = s.city + " · " + s.status_label;
      var nums = document.createElement("div");
      nums.textContent = map.labels.temp + ": " + s.temp + " °C · " + map.labels.comp + ": " + s.comp + "%";
      var link = document.createElement("a");
      link.href = s.url;
      link.textContent = map.labels.open;
      [title, line, nums, link].forEach(function (x) { box.appendChild(x); });
      mk.bindPopup(box);
      mk.bindTooltip(s.code, { direction: "top", offset: [0, -8] });
      mk.addTo(m);
      bounds.push([s.lat, s.lon]);
    });
    if (bounds.length) m.fitBounds(bounds, { padding: [24, 24], maxZoom: 9 });
  }
})();
