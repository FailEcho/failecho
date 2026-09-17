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

    var vb = el("fleet-versus").querySelector("tbody"); vb.innerHTML = "";
    (d.versus || []).forEach(function (g) {
      var h = document.createElement("tr"); h.className = "group";
      var th = document.createElement("th"); th.colSpan = 3;
      th.textContent = g.label + " (" + g.runs_ask + " / " + g.runs_blind + " runs)"; h.appendChild(th); vb.appendChild(h);
      g.rows.forEach(function (r) {
        function cell(v, side) {
          var c = td(v == null ? "-" : (r.unit === "%" ? v + "%" : v), "num" + (r.better === side ? " win" : ""));
          return c;
        }
        vb.appendChild(row([td(r.metric), cell(r.ask, "ask"), cell(r.blind, "blind")]));
      });
    });
    if (!(d.versus || []).length) vb.appendChild(row([td("nothing yet")]));
    fill("fleet-cohorts", (d.cohorts || []).map(function (c) {
      return [td(c.cohort), td(c.runs, "num"), td(c.failures, "num"),
              td(c.attempts_per_failure == null ? "-" : c.attempts_per_failure.toFixed(2), "num"),
              td(c.recovered, "num"), td(c.asked, "num"), td(c.recommended, "num"), td(c.skipped || 0, "num")];
    }));
    fill("fleet-builds", (d.recent_builds || []).map(function (b) {
      var task = (b.task || "").replace(/ Run it\.$/, "");
      return [td((b.at || "").replace("T", " ").slice(5, 16)), td(task.length > 90 ? task.slice(0, 88) + "…" : task),
              td(b.vm_runs, "num"), td(b.local, "num " + (b.local ? "warn" : "")), td(b.shared, "num " + (b.shared ? "warn" : "")),
              td(b.done ? "yes" : "no", b.done ? "ok" : "warn"), td(b.traffic + " / " + b.observed, "num"), td(b.seconds, "num")];
    }));
    fill("fleet-real", (d.real_targets || []).map(function (r) {
      return [td(r.service), td(r.cohort), td(r.failures, "num"),
              td(r.attempts_per_failure == null ? "-" : r.attempts_per_failure.toFixed(2), "num"),
              td(r.recovered, "num"), td(r.skipped, "num"), td(r.seconds, "num")];
    }));
    fill("fleet-provider", (d.cohorts || []).filter(function (c) { return c.cohort.indexOf("test") !== 0 && c.cohort.indexOf("build") !== 0; }).map(function (c) {
      var pf = c.provider_failures || 0, pr = c.provider_recovered || 0;
      return [td(c.cohort), td(pf, "num"), td(pr, "num"), td(pf ? Math.round(100 * pr / pf) + "%" : "-", "num"), td(c.explored || 0, "num")];
    }));
    var runsBy = {}; (d.cohorts || []).forEach(function (c) { runsBy[c.cohort] = c.runs; });
    fill("fleet-costs-outcome", (d.costs || []).map(function (c) {
      return [td(c.cohort), td(c.runs, "num"), td(Math.round(c.completed_rate * 100) + "%", "num"),
              td(c.calls_first_try, "num"), td(c.calls_recovered, "num"), td(c.calls_failed, "num"),
              td(c.model_retries_after_skip || 0, "num")];
    }));
    fill("fleet-costs", (d.costs || []).map(function (c) {
      return [td(c.cohort), td(c.tokens_per_run, "num"), td(c.tokens_per_completed == null ? "-" : c.tokens_per_completed, "num"),
              td(c.model_calls_per_run, "num"), td(c.tool_calls_per_run, "num"), td(c.seconds_per_run, "num"),
              td(c.asks_per_run, "num"), td(c.ask_seconds_per_run, "num"), td(c.wait_seconds_per_run, "num")];
    }));
    var cov = (d.build || []).reduce(function (acc, b) {
      acc.t += b.traffic_runs || 0; acc.u += b.unobserved_runs || 0; acc.c += b.connections || 0; acc.o += b.observed_calls || 0; return acc;
    }, { t: 0, u: 0, c: 0, o: 0 });
    var missed = {}; (d.build || []).forEach(function (b) { Object.keys(b.missed_libs || {}).forEach(function (k) { missed[k] = (missed[k] || 0) + b.missed_libs[k]; }); });
    var missedText = Object.keys(missed).length ? " Unobserved programs imported: " + Object.keys(missed).map(function (k) { return k + " (" + missed[k] + ")"; }).join(", ") + "." : "";
    el("fleet-coverage").textContent = cov.t ? ("So far: " + cov.t + " runs with traffic, " + cov.u + " where the wrapper saw nothing; " +
      cov.c + " connections opened, " + cov.o + " calls observed." + missedText) : "No runs with traffic measured yet.";
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
