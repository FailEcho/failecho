// FailEcho homepage: fetch live figures, render them, copy buttons. That is all.
// No framework, no router, no state container.

(function () {
  "use strict";

  var REFRESH_MS = 30000;
  // A summary, not a log: /v1/services holds the rest.
  var SERVICE_ROWS = 8;

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

  // A figure that moved since the last poll flashes once. The page claims the
  // network is live; this is it showing that rather than asserting it. First
  // paint never flashes -- nothing changed, the page just arrived -- and the
  // clock is deliberately not a stat, or every cycle would flash.
  function setStat(id, value) {
    var node = el(id);
    if (!node) return;
    var seen = node.getAttribute("data-seen");
    node.textContent = value;
    node.setAttribute("data-seen", String(value));
    if (seen === null || seen === String(value)) return;
    node.classList.remove("ticked");
    void node.offsetWidth; // restart the animation rather than let it no-op
    node.classList.add("ticked");
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
    setStat("stat-real-24h", number(stats.real_observations_24h));
    setStat("stat-real-reporters", number(stats.real_reporters_24h));
    setStat("stat-real-fingerprints", number(stats.real_failure_fingerprints));
    setStat("stat-real-incidents", number(stats.real_active_failures));

    // Zeros stay visible: an empty network is the honest state.
    show("real-empty", stats.real_observations_24h === 0);

    // Our own agents: labelled, never adoption.
    show("first-party-block", stats.first_party_observations > 0);
    setStat("stat-first-party", number(stats.first_party_observations || 0));

    show("demo-stats-block", stats.demo_data);
    if (stats.demo_data) {
      setStat("stat-demo-agent", number(stats.demo_agent_observations));
      setStat("stat-synthetic", number(stats.synthetic_observations));
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

  // Provenance tags: demo and first-party never pass for independent agents.
  function sourceTags(item) {
    return (
      (item.demo_data ? '<span class="tag">DEMO</span>' : "") +
      (item.first_party_data ? '<span class="tag">FIRST-PARTY</span>' : "")
    );
  }

  function renderServices(rows) {
    var body = el("services-body");
    if (!body) return;

    // An empty table looks broken; say it in a sentence.
    show("incidents-table", rows.length > 0);
    show("incidents-empty", rows.length === 0);
    if (!rows.length) {
      body.innerHTML = "";
      return;
    }

    body.innerHTML = rows
      .map(function (row) {
        // No rate below the evidence threshold: the status already says so.
        var rate = row.status === "INSUFFICIENT_DATA" ? null
          : (row.failure_rate_5m !== null ? row.failure_rate_5m : row.failure_rate_1h);
        var tags = sourceTags(row);
        return (
          "<tr>" +
          '<td class="svc">' + escapeHtml(row.service) + tags + "</td>" +
          '<td class="op">' + escapeHtml(row.operation) + "</td>" +
          "<td>" + statusCell(row.status) + "</td>" +
          '<td class="num">' + percent(rate) + "</td>" +
          '<td class="num">' + number(row.observations_5m) + "</td>" +
          "</tr>"
        );
      })
      .join("");

    if (rows.length >= SERVICE_ROWS) {
      body.innerHTML +=
        '<tr><td colspan="5" class="muted">Showing the ' + SERVICE_ROWS +
        ' worst. The rest are in <a href="/v1/services">/v1/services</a>.</td></tr>';
    }
  }

  // -- recovery intelligence --------------------------------------------
  function renderRecovery(entries) {
    var list = el("recovery-list");
    if (!list) return;

    if (!entries.length) {
      list.innerHTML =
        '<div class="bootstrap">' +
        '<p class="bootstrap-lead">No recovery echo has enough real evidence yet.</p>' +
        "<p>Recovery actions only appear once independent agents have provided " +
        "enough observed outcomes.</p>" +
        '<p><a class="btn btn--sm" href="/demo">See the demo</a></p>' +
        "</div>";
      return;
    }

    list.innerHTML = entries
      .map(function (entry) {
        var tags = sourceTags(entry);
        return (
          '<article class="echo">' +
          '<div class="echo-head">' +
          '<span class="echo-target">' +
          escapeHtml(entry.service) + " / " + escapeHtml(entry.operation) + tags +
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
    // The async clipboard rejects on a page without permission as readily as
    // on a browser without the API, and the whole code block is the control
    // now, so a rejection is a dead click. Fall through to the old way.
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () {
        return legacyCopy(text);
      });
    }
    return legacyCopy(text);
  }

  function legacyCopy(text) {
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
    var controls = document.querySelectorAll("[data-copy-target]");
    Array.prototype.forEach.call(controls, function (control) {
      // The hero's code blocks are the control themselves, so their feedback
      // cannot be their own text -- rewriting it would delete the command.
      var isBlock = control.classList.contains("copyable");
      var original = control.textContent;

      function done(ok) {
        setText("copy-status", ok
          ? "Copied to clipboard"
          : "Copy failed. Select the text manually.");
        if (isBlock) {
          control.classList.toggle("is-copied", ok);
          setTimeout(function () { control.classList.remove("is-copied"); }, 1600);
          return;
        }
        control.textContent = ok ? "Copied" : "Copy failed";
        setTimeout(function () { control.textContent = original; }, 1600);
      }

      function copy() {
        var source = el(control.getAttribute("data-copy-target"));
        if (!source) return;
        copyText(source.textContent).then(
          function () { done(true); },
          function () { done(false); }
        );
      }

      control.addEventListener("click", copy);
      if (!isBlock) return;
      // role="button" on a pre buys the announcement, not the behaviour.
      control.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        copy();
      });
    });
  }

  function refresh() {
    // Each page asks only for what it shows: the homepage carries two numbers,
    // /network carries the tables.
    var wants = [];
    if (el("stat-real-24h")) wants.push(getJSON("/v1/stats").then(renderStats));
    if (el("services-body")) {
      wants.push(getJSON("/v1/services?limit=" + SERVICE_ROWS).then(renderServices));
    }
    if (el("recovery-list")) {
      wants.push(getJSON("/v1/recovery-intelligence?limit=3").then(renderRecovery));
    }
    return Promise.all(wants)
      .then(function () {
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

  // -- a copy control on every code box ------------------------------------
  // Built here rather than written into each page: the pages carry the code,
  // and a button that only works with a script running has no business being
  // in the markup. Boxes that already ship their own button are left alone.
  function addCopyButtons() {
    var blocks = document.querySelectorAll("pre > code");
    Array.prototype.forEach.call(blocks, function (code, i) {
      var box = code.parentNode.parentNode;
      if (!box || box.querySelector("[data-copy-target]")) return;
      if (!code.id) code.id = "code-block-" + i;

      var button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn--sm copy-btn";
      button.textContent = "Copy";
      button.setAttribute("data-copy-target", code.id);
      button.setAttribute("aria-label", "Copy this code");

      box.classList.add("has-copy");
      box.insertBefore(button, box.firstChild);
    });
  }

  // -- install tabs ------------------------------------------------------
  // Three ways in, one card. Without JS the first panel is the visible one and
  // /setup carries the rest, so nothing here is load-bearing.
  function wireTabs() {
    var tabs = document.querySelectorAll('[role="tab"]');
    if (!tabs.length) return;

    function select(tab) {
      for (var i = 0; i < tabs.length; i++) {
        var on = tabs[i] === tab;
        tabs[i].setAttribute("aria-selected", on ? "true" : "false");
        tabs[i].tabIndex = on ? 0 : -1;
        var panel = el(tabs[i].getAttribute("aria-controls"));
        if (panel) panel.hidden = !on;
      }
    }

    select(tabs[0]);
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].addEventListener("click", function () { select(this); });
      tabs[i].addEventListener("keydown", function (event) {
        var step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        event.preventDefault();
        var at = Array.prototype.indexOf.call(tabs, this);
        var next = tabs[(at + step + tabs.length) % tabs.length];
        select(next);
        next.focus();
      });
    }
  }

  // -- on-this-page rail ---------------------------------------------------
  // Which section you are in, marked as you scroll. Read off the sections'
  // own positions rather than an observer: the answer is then the same one
  // the reader sees, at any scroll speed, with no threshold to tune.
  function wireRail() {
    var links = document.querySelectorAll(".pagenav-links a");
    if (!links.length) return;

    var pairs = [];
    Array.prototype.forEach.call(links, function (link) {
      var section = el(link.getAttribute("href").slice(1));
      if (section) pairs.push([link, section]);
    });
    if (!pairs.length) return;

    var last = 0;
    var trailing = null;

    function mark() {
      last = Date.now();
      // Just under the sticky header: the first thing you can actually read.
      var line = 120;
      var current = pairs[0];
      for (var i = 0; i < pairs.length; i++) {
        if (pairs[i][1].getBoundingClientRect().top <= line) current = pairs[i];
      }
      // A short last section never reaches the line. At the bottom of the
      // page it is nonetheless the one you are looking at.
      if (window.innerHeight + window.pageYOffset >=
          document.documentElement.scrollHeight - 4) {
        current = pairs[pairs.length - 1];
      }
      for (var j = 0; j < pairs.length; j++) {
        var on = pairs[j] === current;
        pairs[j][0].classList.toggle("here", on);
        if (on) pairs[j][0].setAttribute("aria-current", "true");
        else pairs[j][0].removeAttribute("aria-current");
      }
    }

    // Throttled on the clock, not on a frame. A requestAnimationFrame latch
    // is the usual shape here and it has a failure mode this cannot have: if
    // the frame never arrives -- a background tab, a throttled renderer --
    // the latch stays set and the rail freezes for the rest of the session.
    function schedule() {
      var since = Date.now() - last;
      if (trailing) window.clearTimeout(trailing);
      if (since >= 100) return mark();
      // The scroll that stops mid-throttle still gets its answer.
      trailing = window.setTimeout(mark, 100 - since);
    }

    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    mark();
  }

  addCopyButtons();
  wireCopyButtons();
  wireTabs();
  wireRail();
  if (el("stat-real-24h")) {
    refresh();
    if (!document.hidden) startPolling();
  }
})();
