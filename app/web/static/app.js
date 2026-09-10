// FailEcho homepage: fetch live figures, render them, copy buttons. That is all.
// No framework, no router, no state container.

(function () {
  "use strict";

  var REFRESH_MS = 30000;

  var fmt = new Intl.NumberFormat("en-US");

  function number(value) {
    return typeof value === "number" ? fmt.format(value) : "—";
  }

  function percent(rate) {
    return rate === null || rate === undefined ? "—" : (rate * 100).toFixed(1) + "%";
  }

  function el(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    var node = el(id);
    if (node) node.textContent = value;
  }

  function setHTML(id, value) {
    var node = el(id);
    if (node) node.innerHTML = value;
  }

  function show(id, visible) {
    var node = el(id);
    if (node) node.hidden = !visible;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function getJSON(path) {
    return fetch(path, { headers: { accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error(path + " -> " + r.status);
      return r.json();
    });
  }

  // -- live network ------------------------------------------------------
  function renderStats(stats) {
    setText("stat-real-24h", number(stats.real_observations_24h));
    setText("stat-real-reporters", number(stats.real_reporters_24h));
    setText("stat-real-fingerprints", number(stats.real_failure_fingerprints));
    setText("stat-real-incidents", number(stats.real_active_failures));

    // The zero is shown, never hidden: an empty network is the honest state.
    show("real-empty", stats.real_observations_24h === 0);

    show("demo-stats-block", stats.demo_data);
    if (stats.demo_data) {
      setText("stat-demo-agent", number(stats.demo_agent_observations));
      setText("stat-synthetic", number(stats.synthetic_observations));
    }

    show("demo-mode-badge", stats.demo_mode);
    show("demo-mode-banner", stats.demo_mode);

    setText("updated", "updated " + new Date().toLocaleTimeString());
  }

  // -- incidents ---------------------------------------------------------
  function statusCell(status) {
    return (
      '<span class="status status--' + status + '">' +
      '<span class="status-mark" aria-hidden="true"></span>' +
      escapeHtml(status.replace(/_/g, " ")) +
      "</span>"
    );
  }

  function renderServices(rows) {
    var body = el("services-body");
    if (!body) return;

    if (!rows.length) {
      body.innerHTML =
        '<tr><td colspan="5" class="muted">No active incidents. ' +
        "Nothing has been reported in the last hour.</td></tr>";
      return;
    }

    body.innerHTML = rows
      .map(function (row) {
        var rate = row.failure_rate_5m !== null ? row.failure_rate_5m : row.failure_rate_1h;
        var demo = row.demo_data ? '<span class="tag">DEMO</span>' : "";
        return (
          "<tr>" +
          '<td class="svc">' + escapeHtml(row.service) + demo + "</td>" +
          '<td class="op">' + escapeHtml(row.operation) + "</td>" +
          "<td>" + statusCell(row.status) + "</td>" +
          '<td class="num">' + percent(rate) + "</td>" +
          '<td class="num">' + number(row.observations_5m) + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  // -- recovery intelligence --------------------------------------------
  function renderRecovery(entries) {
    var list = el("recovery-list");
    if (!list) return;

    if (!entries.length) {
      list.innerHTML =
        '<p class="empty"><strong>No recovery echo has enough evidence yet.</strong> ' +
        "An action is named only once independent reporters agree on it.</p>";
      return;
    }

    list.innerHTML = entries
      .map(function (entry) {
        var demo = entry.demo_data ? '<span class="tag">DEMO</span>' : "";
        return (
          '<article class="echo">' +
          '<div class="echo-head">' +
          '<span class="echo-target">' +
          escapeHtml(entry.service) + " / " + escapeHtml(entry.operation) + demo +
          "</span>" +
          '<span class="echo-kind">' + escapeHtml(entry.error_type || "") + "</span>" +
          "</div>" +
          '<p class="echo-msg">' + escapeHtml(entry.normalized_error || "") + "</p>" +
          '<dl class="echo-grid">' +
          "<div><dt>Recovery echo</dt>" +
          '<dd class="action">' + escapeHtml(entry.action) + "</dd></div>" +
          "<div><dt>Worked</dt><dd>" +
          number(entry.successes) + " / " + number(entry.attempts) +
          '<span class="sub">' + percent(entry.success_rate) + " successful</span></dd></div>" +
          "<div><dt>Confidence</dt><dd>" + entry.confidence.toFixed(2) +
          '<span class="sub">Wilson lower bound</span></dd></div>' +
          "<div><dt>Independent reporters</dt><dd>" +
          number(entry.unique_reporters) +
          "</dd></div>" +
          "</dl>" +
          "</article>"
        );
      })
      .join("");
  }

  // -- copy buttons ------------------------------------------------------
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        resolve();
      } catch (err) {
        reject(err);
      } finally {
        document.body.removeChild(area);
      }
    });
  }

  function wireCopyButtons() {
    var buttons = document.querySelectorAll("[data-copy-target]");
    Array.prototype.forEach.call(buttons, function (button) {
      button.addEventListener("click", function () {
        var source = el(button.getAttribute("data-copy-target"));
        if (!source) return;
        var original = button.textContent;
        copyText(source.textContent).then(
          function () {
            button.textContent = "Copied";
            setText("copy-status", "Copied to clipboard");
            setTimeout(function () { button.textContent = original; }, 1600);
          },
          function () {
            button.textContent = "Copy failed";
            setText("copy-status", "Copy failed. Select the text manually.");
            setTimeout(function () { button.textContent = original; }, 1600);
          }
        );
      });
    });
  }

  // The endpoint an agent should use: the configured public URL when this
  // instance has one (FIN_PUBLIC_URL, rendered into data-public-url), else
  // whatever origin this page was served from. Never a hardcoded localhost.
  function publicOrigin() {
    var declared = document.body.getAttribute("data-public-url");
    if (declared && declared.indexOf("{{") === -1) return declared.replace(/\/$/, "");
    return window.location.origin;
  }

  function showEndpoints() {
    var origin = publicOrigin();
    // The server renders {{PUBLIC_URL}} into the page; this only has to fix
    // things up when the page is opened on an origin the server did not know.
    ["mcp-endpoint", "code-mcp", "code-rest", "code-python"].forEach(function (id) {
      var node = el(id);
      if (!node) return;
      node.textContent = node.textContent.split("{{PUBLIC_URL}}").join(origin);
    });
  }

  function refresh() {
    return Promise.all([
      getJSON("/v1/stats"),
      getJSON("/v1/services"),
      getJSON("/v1/recovery-intelligence?limit=3"),
    ])
      .then(function (results) {
        renderStats(results[0]);
        renderServices(results[1]);
        renderRecovery(results[2]);
        var dot = el("live-dot");
        if (dot) dot.style.background = "var(--red)";
      })
      .catch(function () {
        var dot = el("live-dot");
        if (dot) dot.style.background = "var(--unknown)";
        setText("updated", "network unreachable");
      });
  }

  // Poll only while the tab is actually being looked at. A forgotten open tab
  // used to keep asking every 30s forever -- pure load, and it inflated our
  // own traffic numbers to the point of hiding real visitors.
  var timer = null;

  function startPolling() {
    if (timer === null) timer = setInterval(refresh, REFRESH_MS);
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      stopPolling();
    } else {
      refresh();
      startPolling();
    }
  });

  showEndpoints();
  wireCopyButtons();
  refresh();
  if (!document.hidden) startPolling();
})();
