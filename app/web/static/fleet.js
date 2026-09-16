/* The fleet scoreboard. Reads /fleet.json, which the scheduler rewrites after
   every run. No framework, no build step: the page is a table of numbers. */
(function () {
  "use strict";
  function el(id) { return document.getElementById(id); }
  function td(v, cls) { var c = document.createElement("td"); if (cls) c.className = cls; c.textContent = v == null ? "-" : v; return c; }
  function row(cells) { var r = document.createElement("tr"); cells.forEach(function (c) { r.appendChild(c); }); return r; }
  function fill(tableId, rows) {
    var body = el(tableId).querySelector("tbody"); body.innerHTML = "";
    if (!rows.length) { body.appendChild(row([td("nothing yet")])); return; }
    rows.forEach(function (r) { body.appendChild(row(r)); });
  }
  function card(k, v, n) {
    var d = document.createElement("div"); d.className = "fleet-card";
    d.innerHTML = '<div class="k"></div><div class="v"></div><div class="n"></div>';
    d.children[0].textContent = k; d.children[1].textContent = v; d.children[2].textContent = n || "";
    return d;
  }
  function render(d) {
    el("fleet-updated").textContent = "Updated " + (d.generated_at || "").replace("T", " ").slice(0, 19) + " UTC.";
    var cards = el("fleet-cards"); cards.innerHTML = "";
    var t = d.totals || {};
    cards.appendChild(card("runs", t.runs || 0, (t.personas || 0) + " personas"));
    cards.appendChild(card("tool calls", t.tool_calls || 0, (t.failures || 0) + " failed"));
    cards.appendChild(card("repeated across reporters", t.cross_reporter_fingerprints || 0, "fingerprints with 2+ reporters"));
    cards.appendChild(card("cross-agent help", t.cross_agent_help || 0, "recommendations built on someone else's evidence"));
    cards.appendChild(card("recovery outcomes", t.recovery_outcomes || 0, (t.decaying || 0) + " decaying, " + (t.related_pairs || 0) + " related pairs"));

    fill("fleet-cohorts", (d.cohorts || []).map(function (c) {
      return [td(c.cohort), td(c.runs, "num"), td(c.failures, "num"),
              td(c.attempts_per_failure == null ? "-" : c.attempts_per_failure.toFixed(2), "num"),
              td(c.recovered, "num"), td(c.asked, "num"), td(c.recommended, "num")];
    }));
    var runsBy = {}; (d.cohorts || []).forEach(function (c) { runsBy[c.cohort] = c.runs; });
    fill("fleet-build", (d.build || []).map(function (b) {
      return [td(b.cohort), td(runsBy[b.cohort] || 0, "num"), td(b.vm_runs, "num"), td(b.tasks_done, "num"),
              td(b.local, "num"), td(b.shared, "num"),
              td(b.shared_share == null ? "-" : Math.round(b.shared_share * 100) + "%", "num")];
    }));
    fill("fleet-repeats", (d.repeats || []).map(function (r) {
      return [td(r.service), td(r.operation), td(r.error_type + (r.error_code ? "/" + r.error_code : "")),
              td(r.reporters, "num"), td(r.observations, "num"), td(r.fixes || "-")];
    }));
    fill("fleet-naming", (d.naming || []).map(function (n) {
      return [td(n.service), td(n.operations.join(", ")), td(n.fingerprints, "num " + (n.fingerprints > n.expected ? "warn" : "ok")), td(n.paths.join(", "))];
    }));
    var o = d.onboard, ot = (o && o.totals) || {};
    el("fleet-onboard-when").textContent = o ? (ot.graded + " graded runs, " + ot.passed + " passed; " +
      ot.other_preserved + " kept another server intact, " + ot.other_clobbered + " clobbered it; " +
      ot.said_restart + " said a restart is needed.") : "Not run yet.";
    var scenes = { clean: "clean project", other: "another server already there", present: "FailEcho already there",
                   home: "home directory, many projects", readonly: "project cannot be written" };
    fill("fleet-onboard-scenarios", ((o && o.scenarios) || []).map(function (b) {
      return [td(scenes[b.scenario] || b.scenario), td(b.graded, "num"),
              td(b.graded ? b.passed + " / " + b.graded : "-", "num " + (b.graded && b.passed === b.graded ? "ok" : b.graded ? "warn" : "")),
              td(b.worst || "-")];
    }));
    fill("fleet-onboard", ((o && o.models) || []).map(function (m) {
      return [td(m.model + " (" + m.provider + ")"), td(m.runs, "num"),
              td(m.graded ? m.passed + " / " + m.graded : "-", "num " + (m.graded && m.passed === m.graded ? "ok" : m.graded ? "warn" : "")),
              td(m.provider_failed, "num"), td(m.worst || "-"), td((m.last || "").replace("T", " ").slice(0, 16))];
    }));
    var c = d.canary;
    el("fleet-canary-when").textContent = c ? ("Last run " + (c.at || "").replace("T", " ").slice(0, 16) + " UTC: " +
      (c.ok ? "every path worked." : "A PATH IS BROKEN.") + (c.error ? " " + c.error : "")) : "Not run yet.";
    fill("fleet-canary", ((c && c.steps) || []).filter(function (s) { return s.name !== "wrapper_version"; }).map(function (s) {
      return [td(s.name), td(s.ok ? "ok" : "FAILED", s.ok ? "ok" : "warn"), td(s.seconds, "num"), td(s.detail || "")];
    }));
    fill("fleet-personas", (d.personas || []).map(function (p) {
      return [td(p.reporter), td(p.path), td(p.provider), td(p.asks ? "yes" : "no"),
              td(p.runs, "num"), td(p.tool_calls, "num"), td(p.failures, "num"), td((p.last || "").replace("T", " ").slice(0, 16))];
    }));
  }
  function load() {
    fetch("/fleet.json", { cache: "no-store" }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) render(d); else el("fleet-updated").textContent = "No runs yet."; })
      .catch(function () {});
  }
  load(); setInterval(load, 60000);
})();
