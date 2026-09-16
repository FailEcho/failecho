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
