/* Calculator helpers: name autocomplete and trade history in localStorage.
 * Served from /static so the content security policy (script-src 'self') allows it.
 * Nothing here is sent to the server except the autocomplete query. */
(function () {
  "use strict";

  var HISTORY_KEY = "dynasty.tradeHistory.v1";
  var MAX_HISTORY = 15;

  // ---------- autocomplete ----------
  var list = document.createElement("ul");
  list.className = "suggest-list";
  list.hidden = true;
  document.body.appendChild(list);

  var activeArea = null;
  var timer = null;

  function currentLineBounds(area) {
    var pos = area.selectionStart;
    var text = area.value;
    var start = text.lastIndexOf("\n", pos - 1) + 1;
    var end = text.indexOf("\n", pos);
    if (end === -1) end = text.length;
    return { start: start, end: end, text: text.slice(start, end) };
  }

  function hide() {
    list.hidden = true;
    list.innerHTML = "";
  }

  function show(area, results) {
    list.innerHTML = "";
    if (!results.length) return hide();
    var rect = area.getBoundingClientRect();
    list.style.left = rect.left + window.scrollX + "px";
    list.style.top = rect.bottom + window.scrollY + "px";
    list.style.width = rect.width + "px";
    results.forEach(function (name) {
      var item = document.createElement("li");
      item.textContent = name;
      item.addEventListener("mousedown", function (event) {
        event.preventDefault();
        var bounds = currentLineBounds(area);
        area.value = area.value.slice(0, bounds.start) + name + area.value.slice(bounds.end);
        var caret = bounds.start + name.length;
        area.setSelectionRange(caret, caret);
        hide();
        area.focus();
      });
      list.appendChild(item);
    });
    list.hidden = false;
  }

  function query(area) {
    var line = currentLineBounds(area).text.trim();
    if (line.length < 2) return hide();
    var qbs = document.getElementById("qbs");
    var url = "/api/assets?q=" + encodeURIComponent(line) + "&qbs=" + (qbs ? qbs.value : "1");
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : { results: [] }; })
      .then(function (data) { if (activeArea === area) show(area, data.results || []); })
      .catch(hide);
  }

  ["team_a", "team_b"].forEach(function (id) {
    var area = document.getElementById(id);
    if (!area) return;
    area.addEventListener("input", function () {
      activeArea = area;
      clearTimeout(timer);
      timer = setTimeout(function () { query(area); }, 150);
    });
    area.addEventListener("blur", function () { setTimeout(hide, 100); });
    area.addEventListener("keydown", function (event) { if (event.key === "Escape") hide(); });
  });

  // ---------- history ----------
  function readHistory() {
    try {
      var raw = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
      return Array.isArray(raw) ? raw : [];
    } catch (e) {
      return [];
    }
  }

  function writeHistory(items) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, MAX_HISTORY))); } catch (e) { /* storage full or disabled */ }
  }

  var result = document.getElementById("trade-result");
  if (result) {
    var entry = {
      a: result.dataset.teamA || "",
      b: result.dataset.teamB || "",
      qbs: result.dataset.qbs || "1",
      ppr: result.dataset.ppr || "1.0",
      te: result.dataset.te || "0.0",
      fairness: result.dataset.fairness || "",
      winner: result.dataset.winner || "",
      at: new Date().toISOString()
    };
    var items = readHistory().filter(function (h) { return !(h.a === entry.a && h.b === entry.b && h.qbs === entry.qbs); });
    items.unshift(entry);
    writeHistory(items);
  }

  var box = document.getElementById("trade-history");
  if (box) {
    var history = readHistory();
    var body = box.querySelector("[data-history-list]");
    if (!history.length) {
      box.hidden = true;
    } else {
      history.forEach(function (h, index) {
        var row = document.createElement("li");
        var label = document.createElement("span");
        label.textContent = h.a.split("\n").join(", ") + "  for  " + h.b.split("\n").join(", ");
        var meta = document.createElement("span");
        meta.className = "muted small";
        meta.textContent = (h.fairness ? h.fairness + "% even" : "") + (h.winner && h.winner !== "Even" ? " / favors " + h.winner : "");
        var load = document.createElement("button");
        load.type = "button";
        load.className = "btn btn-secondary btn-small";
        load.textContent = "Load";
        load.addEventListener("click", function () {
          document.getElementById("team_a").value = h.a;
          document.getElementById("team_b").value = h.b;
          var qbs = document.getElementById("qbs"); if (qbs) qbs.value = h.qbs;
          var ppr = document.getElementById("ppr"); if (ppr) ppr.value = h.ppr;
          var te = document.getElementById("te"); if (te) te.value = h.te;
        });
        row.appendChild(label); row.appendChild(meta); row.appendChild(load);
        body.appendChild(row);
      });
      var clear = box.querySelector("[data-history-clear]");
      if (clear) clear.addEventListener("click", function () {
        try { localStorage.removeItem(HISTORY_KEY); } catch (e) { /* ignore */ }
        box.hidden = true;
      });
    }
  }

  var copy = document.getElementById("copy-share");
  if (copy) copy.addEventListener("click", function () {
    var input = document.getElementById("share-url");
    input.select();
    if (navigator.clipboard) navigator.clipboard.writeText(input.value);
    copy.textContent = "Copied";
  });
})();
