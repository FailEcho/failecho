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

    // What "independent" means, from the server, and what it is holding back.
    var th = stats.adoption_threshold || {};
    if (th.min_observations) {
      setStat("adopt-min-obs", number(th.min_observations));
      setStat("adopt-min-span", Math.round(th.min_span_seconds / 60) + " minutes");
    }
    show("sparse-note", (stats.sparse_observations || 0) > 0);
    setStat("stat-sparse", number(stats.sparse_observations || 0));

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

    renderCharts(stats);
    setText("updated", "updated " + new Date().toLocaleTimeString());
  }

  // -- charts ---------------------------------------------------------------
  // Two bars each, drawn from the same numbers the cards above them show. No
  // history: the network keeps 48 hours of raw observations and there is no
  // endpoint that would let a line over time be honest.
  function bar(fillId, valueId, value, total) {
    var fill = el(fillId);
    var label = el(valueId);
    if (!fill || !label) return;
    var share = total > 0 ? (value / total) * 100 : 0;
    // A real but tiny number still gets a mark, or the chart says zero when
    // the number does not.
    fill.style.width = (value > 0 ? Math.max(share, 1.5) : 0) + "%";
    label.textContent = number(value);
  }

  function renderCharts(stats) {
    if (el("chart-demand")) {
      var known = stats.known_query_hits_24h || 0;
      var unknown = stats.unknown_query_hits_24h || 0;
      var asked = known + unknown;
      bar("bar-known", "val-known", known, asked);
      bar("bar-unknown", "val-unknown", unknown, asked);
      setText("demand-note", asked === 0
        ? "Nothing has asked the network anything in the last 24 hours."
        : "A network that answers nothing is a network with no evidence in it"
          + " yet, not a network nobody is asking.");
    }

    if (el("chart-provenance")) {
      var agent = stats.real_observations_total || 0;
      var own = stats.first_party_observations || 0;
      var fake = (stats.demo_agent_observations || 0)
        + (stats.synthetic_observations || 0);
      var held = agent + own + fake;
      bar("bar-agent", "val-agent", agent, held);
      bar("bar-own", "val-own", own, held);
      bar("bar-demo", "val-demo", fake, held);
    }
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
        if (entry.verdict === "skip") {
          return (
            '<article class="echo echo--skip">' +
            '<div class="echo-head">' +
            '<span class="echo-target">' +
            escapeHtml(entry.service) + " / " + escapeHtml(entry.operation) + tags +
            "</span>" +
            '<span class="echo-kind">' + escapeHtml(entry.error_type || "") + "</span>" +
            "</div>" +
            '<dl class="echo-grid">' +
            "<div><dt>Verdict</dt>" +
            '<dd class="action">skip</dd></div>' +
            "<div><dt>Tried, failed</dt><dd>" + number(entry.attempts) +
            '<span class="sub">' + escapeHtml(entry.warning || "") + "</span></dd></div>" +
            "<div><dt>Confidence</dt><dd>" + entry.confidence.toFixed(2) +
            '<span class="sub">that the next attempt fails too</span></dd></div>' +
            "<div><dt>Independent reporters</dt><dd>" +
            number(entry.unique_reporters) +
            "</dd></div>" +
            "</dl>" +
            "</article>"
          );
        }
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

      function say(node, ok) {
        var was = node.textContent;
        node.textContent = ok ? "Copied" : "Copy failed";
        node.classList.toggle("is-copied", ok);
        setTimeout(function () {
          node.textContent = was;
          node.classList.remove("is-copied");
        }, 1600);
      }

      function done(ok) {
        setText("copy-status", ok
          ? "Copied to clipboard"
          : "Copy failed. Select the text manually.");
        if (!isBlock) return say(control, ok);
        control.classList.toggle("is-copied", ok);
        setTimeout(function () {
          // Fade it out while it still reads "copied": swapping the word back
          // first is what made it flash. Only once it is invisible does the
          // label revert, and it stays hidden until the pointer leaves.
          control.classList.add("is-quiet");
          setTimeout(function () {
            control.classList.remove("is-copied");
          }, 260);
        }, 1600);
        // Clicking the block copies, but the block cannot say so without
        // rewriting the command. The button next to it says it instead.
        // A block that is itself a button -- the hero's endpoint -- is its own
        // match here, and saying it on itself replaces the URL with the word
        // "Copied" in the wrong typeface. Its own ::after label already says
        // it, which is what the label is for.
        var partner = document.querySelector(
          'button[data-copy-target="' + control.getAttribute("data-copy-target") + '"]'
        );
        if (partner && partner !== control) say(partner, ok);
      }

      function unquiet() { control.classList.remove("is-quiet"); }
      control.addEventListener("mouseleave", unquiet);
      control.addEventListener("blur", unquiet);

      function copy() {
        var source = el(control.getAttribute("data-copy-target"));
        if (!source) return;
        copyText(source.textContent).then(
          function () { done(true); },
          function () { done(false); }
        );
      }

      control.addEventListener("click", function (event) {
        copy();
        // A card shows its command while something inside it has focus, and
        // clicking the command focuses it -- so the card stayed open after a
        // copy, with the pointer somewhere else entirely. A click with a
        // pointer behind it hands the focus back; a keyboard press
        // (event.detail === 0) keeps it, because that is how you got here.
        if (event.detail > 0) control.blur();
      });
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
      // the network page shows verdicts too: what nothing fixes is as much
      // knowledge as what something does; the homepage keeps to fixes
      var params = el("services-body") ? "?limit=6&include_skip=1" : "?limit=3";
      wants.push(getJSON("/v1/recovery-intelligence" + params).then(renderRecovery));
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

  // -- top bar ------------------------------------------------------------
  // Black while you are at the top, white once you are not. Thresholded with
  // hysteresis: a bar that repaints twice per pixel around a single boundary
  // flickers on a trackpad.
  function wireTopbar() {
    var bar = document.querySelector(".topbar");
    if (!bar) return;
    var stuck = false;
    function check() {
      var y = window.pageYOffset;
      if (!stuck && y > 24) stuck = true;
      else if (stuck && y < 8) stuck = false;
      else return;
      bar.classList.toggle("is-stuck", stuck);
    }
    window.addEventListener("scroll", check, { passive: true });
    check();
  }

  // -- the menu that pulls the bar down --------------------------------------
  // The panels are fixed to the bottom edge of the bar, and the bar's height
  // changes when its own logo grows, so the offset is measured rather than
  // guessed. Hover and focus both open it; the scrim behind takes the page
  // out of focus while it is open.
  function wireMenus() {
    var bar = document.querySelector(".topbar");
    var items = document.querySelectorAll(".navitem");
    if (!bar || !items.length) return;

    var scrim = document.createElement("div");
    scrim.className = "scrim";
    scrim.setAttribute("aria-hidden", "true");
    document.body.appendChild(scrim);

    // Both heights, up front. The panel has to leave the bottom edge of the
    // bar at the same moment the bar starts moving, so measuring after the
    // transition is too late -- the panel would sit at the closed height for
    // 220ms and then snap. The measuring flash is done with transitions off.
    function measure() {
      bar.classList.add("is-measuring");
      var wasOpen = bar.classList.contains("is-open");
      bar.classList.remove("is-open");
      bar.style.setProperty("--bar-h", bar.offsetHeight + "px");
      bar.classList.add("is-open");
      bar.style.setProperty("--bar-h-open", bar.offsetHeight + "px");
      bar.classList.toggle("is-open", wasOpen);
      // Read once more so the browser cannot batch the class changes past
      // the point where turning transitions back on would animate them.
      void bar.offsetHeight;
      bar.classList.remove("is-measuring");
    }

    function open(on) {
      bar.classList.toggle("is-open", on);
      scrim.classList.toggle("is-open", on);
    }

    // Opening is per item; closing is not. Closing on the item's own
    // mouseleave meant that crossing the bar from Network to Developers, or
    // sliding off onto the bar's own background, closed the menu -- and the
    // bar is 25px shorter closed, so the pointer that had just left an item
    // was suddenly back on it. Open, close, open, for as long as you held
    // still. The bar as a whole is the thing you are either inside or not.
    Array.prototype.forEach.call(items, function (item) {
      item.addEventListener("mouseenter", function () { open(true); });
      item.addEventListener("focusin", function () { open(true); });
    });

    bar.addEventListener("mouseleave", function () { open(false); });
    // The panel is fixed, so it is outside the bar's own box even though it
    // is inside it in the markup.
    Array.prototype.forEach.call(document.querySelectorAll(".navpanel"),
      function (panel) {
        panel.addEventListener("mouseenter", function () { open(true); });
        panel.addEventListener("mouseleave", function (event) {
          // Back up into the bar is not leaving: the panel is a descendant
          // of the bar in the markup even though it is outside its box.
          if (bar.contains(event.relatedTarget)) return;
          open(false);
        });
      });
    scrim.addEventListener("mouseenter", function () { open(false); });

    bar.addEventListener("focusout", function () {
      // Focus moving within the bar is not leaving it.
      window.setTimeout(function () {
        if (!bar.contains(document.activeElement)) open(false);
      }, 0);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") open(false);
    });
    // Back-navigation restores the page exactly as it was, hover state and
    // all -- except there is no pointer on the bar any more, so nothing ever
    // closes it. It arrives shut instead.
    // Coming back to a page restores it as it was, and the pointer is
    // wherever the pointer is. Forcing the menu shut was wrong in one
    // direction and leaving it open was wrong in the other, so this asks the
    // document which item is actually under the pointer and agrees with it.
    function sync() {
      open(!!document.querySelector(".navitem:hover, .navpanel:hover"));
    }
    window.addEventListener("pageshow", function () {
      sync();
      window.setTimeout(sync, 0);
    });

    // The net under all of it. Whatever left the menu open -- a restored
    // page, a mouseleave that never fired, a pointer that jumped -- the next
    // movement outside the bar closes it. The panel is inside the bar in the
    // markup, so pointing at it still counts as being in.
    document.addEventListener("mousemove", function (event) {
      if (!bar.classList.contains("is-open")) return;
      if (bar.contains(event.target)) return;
      open(false);
    }, { passive: true });
    window.addEventListener("blur", function () { open(false); });
    window.addEventListener("resize", measure);
    measure();
  }

  // -- the demo terminal ----------------------------------------------------
  // The transcript is in the markup, so it is there for a reader without a
  // script and for anyone who would rather read than watch. With a script it
  // becomes something you can press Run on: the same lines, arriving at
  // roughly the speed the script printed them.
  function wireRunner() {
    var out = el("run-output");
    var button = el("run-demo");
    if (!out || !button) return;

    // Captured before anything touches it. Lines only: no span in this
    // transcript crosses a newline, so splitting the markup is safe.
    var lines = out.innerHTML.split("\n");
    var timer = null;
    var reduced = window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) return;

    button.hidden = false;

    function stop() {
      if (timer) window.clearTimeout(timer);
      timer = null;
      out.innerHTML = lines.join("\n");
      out.classList.remove("is-running");
      button.textContent = "Play again";
      button.disabled = false;
    }

    function play() {
      button.disabled = true;
      button.textContent = "Playing";
      out.classList.add("is-running");
      var at = 0;

      function next() {
        out.innerHTML = lines.slice(0, at).join("\n");
        // A blank line is where the script waited on the network, so it is
        // where the playback waits too. Everything else is one tick.
        var pause = lines[at] === "" ? 260 : 55;
        at += 1;
        if (at > lines.length) return stop();
        timer = window.setTimeout(next, pause);
      }

      next();
    }

    button.addEventListener("click", function () {
      if (button.disabled) return;
      play();
    });
  }

  // A card opens while anything inside it has focus, and so does a menu.
  // Back-navigation restores the page with its focus, which is how both came
  // back open with no pointer near them -- closing the menu was not enough,
  // because the restored focus reopened it through :focus-within. Whatever
  // held the focus lets go on the way in.
  function dropStaleFocus() {
    var active = document.activeElement;
    if (active && active !== document.body && active.closest
        && active.closest(".way, .topbar")) {
      active.blur();
    }
  }

  window.addEventListener("pageshow", function () {
    dropStaleFocus();
    // Some navigations restore the focus after this event rather than before.
    window.setTimeout(dropStaleFocus, 0);
  });

  // -- the row of ways ------------------------------------------------------
  // The two-phase rearrange is in the stylesheet; this only says *when*.
  //
  // CSS can start it on hover and cannot start it on un-hover: a selector
  // that has stopped matching cannot run an animation, and the widths change
  // back when you leave the row exactly as much as they changed when you
  // arrived. So the class is put on for every change of which card is
  // current -- including the change to none -- and taken off when the
  // animation has finished, so the next one can restart it.
  function wireWays() {
    var ways = document.querySelector(".ways");
    if (!ways) return;

    var SHIFT_MS = 500; // the 0.48s settle, plus a frame
    var current = null;
    var timer = null;

    function shiftTo(card) {
      if (card === current) return;
      current = card;
      ways.classList.remove("is-shifting");
      // Reading a layout property between the two makes the browser treat
      // this as a stop and a start rather than as nothing happening.
      void ways.offsetWidth;
      ways.classList.add("is-shifting");
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        ways.classList.remove("is-shifting");
      }, SHIFT_MS);
    }

    ways.addEventListener("mouseover", function (event) {
      var card = event.target.closest && event.target.closest(".way");
      if (card) shiftTo(card);
    });
    ways.addEventListener("mouseleave", function () { shiftTo(null); });
    ways.addEventListener("focusin", function (event) {
      var card = event.target.closest && event.target.closest(".way");
      if (card) shiftTo(card);
    });
    ways.addEventListener("focusout", function () {
      window.setTimeout(function () {
        if (!ways.contains(document.activeElement)) shiftTo(null);
      }, 0);
    });
  }

  addCopyButtons();
  wireCopyButtons();
  wireRail();
  wireTopbar();
  wireMenus();
  wireWays();
  wireRunner();
  if (el("stat-real-24h")) {
    refresh();
    if (!document.hidden) startPolling();
  }
})();
