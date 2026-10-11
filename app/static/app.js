/* Forge AI operating console — plain browser JavaScript.
 *
 * Two rules this file obeys without exception:
 *
 * 1. NO LOCAL TRUTH. Every status, count and label rendered here was produced by
 *    the server and is shown as received. There is no optimistic update, no
 *    client-side status inference, and no code path that turns a missing value
 *    into a success. If the server says UNKNOWN or INDETERMINATE, the page says
 *    the same thing.
 * 2. NO AUTHORITY. No control here grants an approval, sets an outcome, or
 *    chooses a command. Buttons call routes; the routes decide.
 *
 * Every request has a timeout, and every failure mode has its own visible state:
 * loading, empty, unavailable, refused, timeout, indeterminate.
 */
(function () {
  "use strict";

  var TIMEOUT_MS = 45000;

  // The project boundary. Set from the server on the first call; never guessed.
  var authoritativeProjectId = "";

  var el = function (id) { return document.getElementById(id); };

  function setFoot(text) {
    var node = el("foot-status");
    if (node) { node.textContent = text; }
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function render(target, html) {
    if (typeof target === "string") { target = el(target); }
    if (target) { target.innerHTML = html; }
  }

  function stateHtml(kind, message, extra) {
    var cls = {
      loading: "state-loading",
      empty: "state-empty",
      error: "state-error",
      refused: "state-refused",
      unavailable: "state-unavailable",
      indeterminate: "state-indeterminate"
    }[kind] || "state-empty";
    var out = '<p class="' + cls + '">' + escapeHtml(message) + "</p>";
    if (extra) { out += extra; }
    return out;
  }

  function retryButton(label, handlerId) {
    return '<button type="button" class="btn" data-retry="' + escapeHtml(handlerId) +
      '">' + escapeHtml(label) + "</button>";
  }

  /* ---------------------------------------------------------------- *
   * request: one place that turns any failure into a visible state.
   * ---------------------------------------------------------------- */
  function request(path, options) {
    options = options || {};
    var controller = new AbortController();
    var timedOut = false;
    var timer = setTimeout(function () {
      timedOut = true;
      controller.abort();
    }, options.timeout || TIMEOUT_MS);

    var init = {
      method: options.method || "GET",
      headers: { "Accept": "application/json" },
      signal: controller.signal
    };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }

    return fetch(path, init).then(function (response) {
      return response.text().then(function (text) {
        var payload = null;
        if (text) {
          try { payload = JSON.parse(text); } catch (err) { payload = null; }
        }
        return { status: response.status, ok: response.ok, payload: payload, raw: text };
      });
    }).then(function (result) {
      clearTimeout(timer);
      if (result.ok) { return result.payload; }

      // A refusal is a real answer from an authoritative server: it is NOT a
      // transport failure and must not be presented as one, and it must never be
      // retried automatically -- an UNKNOWN or refused result is not permission
      // to try again.
      var detail = result.payload && result.payload.detail !== undefined
        ? result.payload.detail : result.payload;
      var code = "";
      var message = "";
      if (detail && typeof detail === "object") {
        code = detail.code || "";
        message = detail.error || detail.code || "";
      } else if (typeof detail === "string") {
        message = detail;
      }

      var error = new Error(message || ("HTTP " + result.status));
      error.httpStatus = result.status;
      error.code = code || ("http_" + result.status);
      error.refused = result.status === 403 || result.status === 409;
      throw error;
    }).catch(function (error) {
      clearTimeout(timer);
      if (error && error.name === "AbortError") {
        var timeoutError = new Error(
          "The server did not answer within " + Math.round((options.timeout || TIMEOUT_MS) / 1000) + "s."
        );
        timeoutError.timedOut = true;
        timeoutError.code = "timeout";
        throw timeoutError;
      }
      if (error && (error.httpStatus || error.timedOut)) { throw error; }
      var networkError = new Error(
        "Cannot reach the Forge API. It may not be running, or it may have stopped."
      );
      networkError.unavailable = true;
      networkError.code = "unavailable";
      throw networkError;
    });
  }

  /* Render one failure into a target, with a retry where retrying is safe. */
  function renderFailure(target, error, retryId) {
    var retry = retryId ? retryButton("Retry", retryId) : "";
    if (error && error.timedOut) {
      render(target, stateHtml(
        "indeterminate",
        "Timed out: the outcome is unknown. An unknown result is not permission to " +
        "re-run a dangerous action — check the run record before retrying.",
        "<div class=\"row\">" + retry + "</div>"
      ));
      return;
    }
    if (error && error.unavailable) {
      render(target, stateHtml(
        "unavailable",
        "Service unavailable: " + error.message,
        "<div class=\"row\">" + retry + "</div>"
      ));
      return;
    }
    if (error && error.refused) {
      render(target, stateHtml(
        "refused",
        "Refused by the server" + (error.code ? " (" + error.code + ")" : "") + ": " + error.message,
        "<div class=\"row\">" + retry + "</div>"
      ));
      return;
    }
    render(target, stateHtml(
      "error",
      "Request failed" + (error && error.code ? " (" + error.code + ")" : "") + ": " +
        (error ? error.message : "unknown error"),
      "<div class=\"row\">" + retry + "</div>"
    ));
  }

  function pillFor(label) {
    var value = String(label || "").toUpperCase();
    var kind = "pill-unknown";
    // TERMINAL_* IS THE REAL VOCABULARY. `/api/product/runs` publishes the
    // classification under `outcome`, and `RunRecordClassification` names it
    // TERMINAL_SUCCESS / TERMINAL_FAILURE / TERMINAL_TIMEOUT / TERMINAL_CANCELLED /
    // TERMINAL_REFUSED / WAITING / INDETERMINATE / UNKNOWN. Without these entries a
    // proven success was drawn with the neutral "unknown" pill, so the colour
    // contradicted the verdict the server had actually recorded. The plain labels
    // stay listed because run `state`/`status` values use them.
    if (["SUCCESS", "COMPLETED", "SUCCEEDED", "VERIFIED", "ACCEPTED", "PASS", "PASSED", "CURRENT", "TERMINAL_SUCCESS"]
        .indexOf(value) >= 0) { kind = "pill-ok"; }
    else if (["FAILED", "REFUSED", "ERROR", "BLOCKED", "CANCELLED", "REJECTED", "INVALIDATED", "TERMINAL_FAILURE", "TERMINAL_TIMEOUT", "TERMINAL_CANCELLED", "TERMINAL_REFUSED"]
        .indexOf(value) >= 0) { kind = "pill-bad"; }
    else if (["WAITING_APPROVAL", "PENDING", "INCOMPLETE", "STALE", "NEEDS_OWNER", "RUNNING", "WAITING"]
        .indexOf(value) >= 0) { kind = "pill-warn"; }
    // UNKNOWN and INDETERMINATE keep the neutral colour on purpose: they are
    // neither success nor failure, and styling them as either would be a lie.
    return '<span class="pill ' + kind + '">' + escapeHtml(value || "UNKNOWN") + "</span>";
  }

  function kv(pairs) {
    var out = '<dl class="kv">';
    pairs.forEach(function (pair) {
      out += "<dt>" + escapeHtml(pair[0]) + "</dt><dd>" + pair[1] + "</dd>";
    });
    return out + "</dl>";
  }

  function table(headers, rows) {
    if (!rows.length) { return ""; }
    var out = "<table><thead><tr>";
    headers.forEach(function (h) { out += "<th>" + escapeHtml(h) + "</th>"; });
    out += "</tr></thead><tbody>";
    rows.forEach(function (row) {
      out += "<tr>";
      row.forEach(function (cell, index) {
        out += "<td" + (index === 0 ? ' class="mono"' : "") + ">" + cell + "</td>";
      });
      out += "</tr>";
    });
    return out + "</tbody></table>";
  }

  /* ---------------------------------------------------------------- *
   * 1. project + stage
   * ---------------------------------------------------------------- */
  function loadOnboarding() {
    render("project-body", stateHtml("loading", "Loading project…"));
    return request("/api/product/onboarding").then(function (data) {
      authoritativeProjectId = data.project_id || authoritativeProjectId;
      var caps = data.capabilities || {};
      var stage = data.stage || "INITIAL";
      var html = kv([
        ["Project", escapeHtml(data.project_id)],
        ["Stage", pillFor(stage)],
        ["Recorded runs", escapeHtml((data.counts || {}).recorded_runs)],
        ["Declared actions", escapeHtml((data.counts || {}).declarations)],
        ["Acceptance specs", escapeHtml((data.counts || {}).acceptance_specs)],
        ["Memory (current / stale)",
          escapeHtml((data.counts || {}).memory_current) + " / " +
          escapeHtml((data.counts || {}).memory_stale)]
      ]);

      if (data.latest_classification) {
        html += '<h3 class="sec">Latest run</h3>' + kv([
          ["Run", '<span class="mono">' + escapeHtml(data.latest_run_id) + "</span>"],
          ["Classification", pillFor(data.latest_classification.outcome)],
          ["Reason", escapeHtml(data.latest_classification.reason || "")]
        ]);
      } else {
        html += stateHtml("empty", "No run recorded yet.");
      }

      var unavailable = [];
      if (!caps.step_graph) { unavailable.push("step graph"); }
      if (!caps.accept_cycle) { unavailable.push("accept cycle"); }
      if (!caps.declared_verification) { unavailable.push("declared verification"); }
      if (!caps.recovery) { unavailable.push("recovery"); }
      if (unavailable.length) {
        html += stateHtml(
          "unavailable",
          "Not composed in this build: " + unavailable.join(", ") +
          ". The matching controls are disabled rather than shown as working."
        );
        ["graph-start", "graph-resume", "graph-refresh", "verify-run", "accept-run"]
          .forEach(function (id) {
            var node = el(id);
            if (node) { node.disabled = true; }
          });
      }
      render("project-body", html);
      el("conn").className = "pill pill-ok";
      el("conn").textContent = "connected";

      // The declaration selects are filled from the server's own list of
      // operator-declared actions. A value not in this list cannot be typed in.
      fillDeclarationSelects();
      return data;
    }).catch(function (error) {
      el("conn").className = "pill pill-bad";
      el("conn").textContent = "unreachable";
      renderFailure("project-body", error, "onboarding");
      throw error;
    });
  }

  function fillDeclarationSelects() {
    return request("/api/product/requirements").then(function (data) {
      var options = (data.requirements || []).map(function (entry) {
        return '<option value="' + escapeHtml(entry.requirement_id) + '">' +
          escapeHtml(entry.requirement_id) + "</option>";
      }).join("");
      ["decl-verify", "decl-accept"].forEach(function (id) {
        var node = el(id);
        if (node) { node.innerHTML = options; }
      });
      render("requirements-body", requirementsHtml(data));
    }).catch(function (error) {
      renderFailure("requirements-body", error, "requirements");
    });
  }

  function requirementsHtml(data) {
    var entries = data.requirements || [];
    if (!entries.length) {
      return stateHtml(
        "empty",
        "No operator-declared actions, so there is nothing to run and no criteria to check."
      );
    }
    var html = '<h3 class="sec">Requirements and acceptance criteria</h3>';
    entries.forEach(function (entry) {
      html += "<p><strong>" + escapeHtml(entry.requirement_id) + "</strong>";
      if (!entry.has_declared_criteria) {
        html += ' <span class="tag tag-warn">no declared criteria</span>';
        html += '<br><span class="hint">Acceptance cannot be evaluated for this action; ' +
          "the accept cycle will refuse it rather than treat silence as a pass.</span>";
      }
      html += "</p>";
      if (entry.criteria && entry.criteria.length) {
        html += table(["Criterion", "Description", "Expectation"], entry.criteria.map(function (c) {
          var expectation = c.expectation
            ? '<span class="mono">' + escapeHtml(c.expectation.path) + "</span> " +
              (c.expectation.must_exist ? "(must exist)" : "(must not exist)")
            : '<span class="tag tag-warn">not evaluable</span>';
          return [escapeHtml(c.criterion_id), escapeHtml(c.description), expectation];
        }));
      }
      html += '<p class="hint mono">' + escapeHtml(entry.executable || "") +
        (entry.working_directory ? " · cwd " + escapeHtml(entry.working_directory) : "") + "</p>";
    });
    return html;
  }

  /* ---------------------------------------------------------------- *
   * 2. goal
   * ---------------------------------------------------------------- */
  function submitGoal() {
    var goal = el("goal-input").value.trim();
    var status = el("goal-status");
    if (!goal) {
      status.textContent = "Enter a goal first.";
      return Promise.resolve();
    }
    status.textContent = "Recording…";
    return request("/api/product/goal", { method: "POST", body: { goal: goal } })
      .then(function (data) {
        status.innerHTML = "Recorded as <strong>" + escapeHtml(data.kind) +
          "</strong> at trust <strong>" + escapeHtml(data.trust_level) +
          "</strong> — confirmed fact: " + (data.confirmed_fact ? "yes" : "no");
        loadMemory();
        loadPlan();
      })
      .catch(function (error) {
        status.textContent = "";
        var box = el("requirements-body");
        renderFailure(box, error, "requirements");
      });
  }

  /* ---------------------------------------------------------------- *
   * 3. plan
   * ---------------------------------------------------------------- */
  function loadPlan() {
    render("plan-body", stateHtml("loading", "Loading plan…"));
    return request("/api/product/plan").then(function (data) {
      var html = kv([
        ["Profile", '<span class="mono">' + escapeHtml(data.profile_id) + "</span>"],
        ["Allowed commands", '<span class="mono">' +
          escapeHtml((data.allowed_commands || []).join(" ") || "(none)") + "</span>"],
        ["Network access", data.network_access ? "declared" : "not declared"],
        ["Declared actions", escapeHtml(data.declared_action_count)],
        ["Registry consistent", data.registry_consistent ? "yes" : "no"]
      ]);
      if (!data.registry_consistent) {
        html += stateHtml(
          "refused",
          "The declaration registry does not match the execution profile, so no " +
          "declared action can be admitted."
        );
      }
      if (!data.steps || !data.steps.length) {
        html += stateHtml(
          "empty",
          "No step/dependency list has been submitted. Steps are only reported " +
          "after they are checked against the operator's declaration registry."
        );
      } else {
        html += '<h3 class="sec">Steps</h3>' + table(
          ["Step", "Depends on"],
          data.steps.map(function (step) {
            return [
              escapeHtml(step.step_id),
              escapeHtml((step.depends_on || []).join(", ") || "—")
            ];
          })
        );
      }
      render("plan-body", html);
    }).catch(function (error) {
      renderFailure("plan-body", error, "plan");
    });
  }

  /* ---------------------------------------------------------------- *
   * 4. understanding
   * ---------------------------------------------------------------- */
  function loadUnderstanding() {
    render("understanding-body", stateHtml("loading", "Scanning the workspace…"));
    return request("/api/product/understanding", { timeout: 120000 }).then(function (data) {
      var html = kv([
        ["Snapshot", '<span class="mono">' + escapeHtml(data.snapshot_id) + "</span>"],
        ["Files", escapeHtml(data.file_count)],
        ["Structural facts", escapeHtml(data.fact_count)],
        ["Manifests", escapeHtml(data.manifest_count)],
        ["Topology", escapeHtml(data.topology.nodes) + " nodes / " +
          escapeHtml(data.topology.edges) + " edges"],
        ["Fingerprint", '<span class="mono">' +
          escapeHtml(String(data.workspace_fingerprint || "").slice(0, 16)) + "…</span>"]
      ]);
      if (data.warnings && data.warnings.length) {
        html += '<h3 class="sec">Scanner warnings</h3>' + table(
          ["Type", "Message"],
          data.warnings.map(function (w) {
            return [escapeHtml(w.warning_type), escapeHtml(w.message)];
          })
        );
      }
      if (data.manifests && data.manifests.length) {
        html += '<h3 class="sec">Manifests</h3>' + table(
          ["Path", "Type"],
          data.manifests.map(function (m) {
            return [escapeHtml(m.relative_path), escapeHtml(m.manifest_type)];
          })
        );
      }
      if (!data.facts || !data.facts.length) {
        html += stateHtml("empty", "The scanner extracted no structural facts.");
      } else {
        html += '<h3 class="sec">Structural facts</h3>' + table(
          ["Fact", "Type", "Source"],
          data.facts.slice(0, 60).map(function (fact) {
            return [
              escapeHtml(fact.fact_id),
              escapeHtml(fact.fact_type),
              '<span class="mono">' + escapeHtml(fact.source_path) + ":" +
                escapeHtml(fact.line_start) + "</span>"
            ];
          })
        );
        if (data.facts_truncated) {
          html += '<p class="hint">Showing the first 60 facts of ' +
            escapeHtml(data.fact_count) + ".</p>";
        }
      }
      html += '<p class="hint">' + escapeHtml(data.note) + "</p>";
      render("understanding-body", html);
    }).catch(function (error) {
      renderFailure("understanding-body", error, "understanding");
    });
  }

  /* ---------------------------------------------------------------- *
   * 5. execution / step graph
   * ---------------------------------------------------------------- */
  function currentGraphRunId() {
    var value = (el("graph-run-id").value || "").trim();
    return value;
  }

  function startGraph() {
    var runId = currentGraphRunId();
    var statusNode = el("graph-status-text");
    if (!runId) { statusNode.textContent = "Enter a graph run id first."; return Promise.resolve(); }
    var steps;
    try {
      steps = JSON.parse(el("graph-steps").value || "[]");
    } catch (err) {
      statusNode.textContent = "Steps are not valid JSON.";
      return Promise.resolve();
    }
    if (!Array.isArray(steps) || !steps.length) {
      statusNode.textContent = "Provide at least one step.";
      return Promise.resolve();
    }
    // Every execution control is disabled while a request is in flight, so a
    // double click cannot become a second request.
    setGraphBusy(true);
    statusNode.textContent = "Starting…";
    return request("/api/product/graph", {
      method: "POST",
      body: { graph_run_id: runId, steps: steps, graph_id: "console-plan" },
      timeout: 300000
    }).then(function (data) {
      statusNode.textContent = data.replayed
        ? "A recorded attempt already existed — nothing was executed again."
        : "Attempt finished.";
      renderGraphResult(data.result);
      loadApprovals();
      loadRuns();
    }).catch(function (error) {
      statusNode.textContent = "";
      renderFailure("graph-body", error, "graph");
    }).then(function () {
      setGraphBusy(false);
    });
  }

  function setGraphBusy(busy) {
    ["graph-start", "graph-resume", "graph-refresh"].forEach(function (id) {
      var node = el(id);
      if (node) { node.disabled = busy; }
    });
  }

  function loadGraphStatus() {
    var runId = currentGraphRunId();
    if (!runId) { render("graph-body", stateHtml("empty", "Enter a graph run id.")); return Promise.resolve(); }
    render("graph-body", stateHtml("loading", "Reading the recorded attempt…"));
    return request("/api/product/graph/" + encodeURIComponent(runId)).then(function (data) {
      if (!data.found) {
        render("graph-body", stateHtml(
          "empty",
          "No attempt is recorded under this id. Starting it is a separate, explicit action."
        ));
        render("approvals-body", stateHtml("empty", "No recorded attempt to approve."));
        return;
      }
      renderGraphResult(data);
      loadApprovals();
    }).catch(function (error) {
      renderFailure("graph-body", error, "graph");
    });
  }

  function renderGraphResult(result) {
    if (!result) { render("graph-body", stateHtml("empty", "No result.")); return; }
    var html = kv([
      ["Graph", '<span class="mono">' + escapeHtml(result.graph_id) + "</span>"],
      ["Run", '<span class="mono">' + escapeHtml(result.graph_run_id) + "</span>"],
      ["Status", pillFor(result.status)],
      ["Executed steps", escapeHtml(result.executed_steps)],
      ["Stopped reason", escapeHtml(result.stopped_reason || "—")],
      ["Idempotency degraded", result.idempotency_degraded ? "yes" : "no"]
    ]);
    if (result.status === "INDETERMINATE" || result.status === "INCOMPLETE") {
      html += stateHtml(
        "indeterminate",
        "This attempt is not proven finished. Nothing here is a success, and a " +
        "resume is an explicit decision — not an automatic retry."
      );
    }
    var steps = result.steps || [];
    if (!steps.length) {
      html += stateHtml("empty", "No step records were produced.");
    } else {
      html += '<h3 class="sec">Steps</h3>' + table(
        ["Step", "Status", "Verification", "Evidence"],
        steps.map(function (step) {
          var verification = step.verification_status || "NOT_RUN";
          var vtag = verification === "PASSED" ? "tag-ok"
            : verification === "FAILED" ? "tag-bad"
            : verification === "ERROR" ? "tag-warn" : "tag-unknown";
          return [
            escapeHtml(step.step_id),
            pillFor(step.status),
            '<span class="tag ' + vtag + '">' + escapeHtml(verification) + "</span>",
            '<span class="mono">' + escapeHtml((step.evidence || []).join(", ") || "—") + "</span>"
          ];
        })
      );
      steps.forEach(function (step) {
        if (step.execution_error) {
          html += stateHtml("error", step.step_id + " execution error: " + step.execution_error);
        }
        if (step.verification_error) {
          html += stateHtml("indeterminate",
            step.step_id + " verification could not be evaluated: " + step.verification_error);
        }
      });
    }
    render("graph-body", html);
  }

  function resumeGraph() {
    var runId = currentGraphRunId();
    var statusNode = el("graph-status-text");
    if (!runId) { statusNode.textContent = "Enter a graph run id first."; return Promise.resolve(); }
    var steps = null;
    try {
      var parsed = JSON.parse(el("graph-steps").value || "[]");
      if (Array.isArray(parsed) && parsed.length) { steps = parsed; }
    } catch (err) { steps = null; }
    setGraphBusy(true);
    statusNode.textContent = "Resuming the recorded attempt…";
    var body = { graph_id: "console-plan" };
    if (steps) { body.steps = steps; }
    return request("/api/product/graph/" + encodeURIComponent(runId) + "/resume", {
      method: "POST", body: body, timeout: 300000
    }).then(function (data) {
      statusNode.textContent = "Resume finished. Steps with a recorded verdict were not repeated.";
      renderGraphResult(data.result);
      loadApprovals();
      loadRuns();
    }).catch(function (error) {
      statusNode.textContent = "";
      renderFailure("graph-body", error, "graph");
    }).then(function () {
      setGraphBusy(false);
    });
  }

  /* ---------------------------------------------------------------- *
   * 6. approvals
   * ---------------------------------------------------------------- */
  function loadApprovals() {
    var runId = currentGraphRunId();
    if (!runId) {
      render("approvals-body", stateHtml("empty", "Enter a graph run id above."));
      return Promise.resolve();
    }
    render("approvals-body", stateHtml("loading", "Reading approval requests…"));
    return request("/api/product/approvals/" + encodeURIComponent(runId)).then(function (data) {
      if (!data.requests || !data.requests.length) {
        render("approvals-body", stateHtml(
          "empty", "No step is waiting for approval in this attempt."
        ));
        return;
      }
      var html = stateHtml("indeterminate",
        data.awaiting + " step(s) are waiting for approval. The server has not granted " +
        "anything: approval is the sole decision of the server-side ApprovalPolicy and " +
        "its resolver. This page cannot approve."
      );
      data.requests.forEach(function (req) {
        html += "<h3 class=\"sec\">" + escapeHtml(req.step_id) + "</h3>" + kv([
          ["Declaration", '<span class="mono">' + escapeHtml(req.declaration_id) + "</span>"],
          ["Status", pillFor(req.status)],
          ["Executable", '<span class="mono">' +
            escapeHtml((req.consequences || {}).executable) + "</span>"],
          ["Working directory", '<span class="mono">' +
            escapeHtml((req.consequences || {}).working_directory) + "</span>"],
          ["Verification expected", escapeHtml((req.consequences || {}).verification_expected || "—")],
          ["Intent fingerprint", '<span class="mono">' +
            escapeHtml(String(req.intent_fingerprint || "").slice(0, 24) || "—") + "</span>"],
          ["Granted by server", req.granted_by_server ? "yes" : "no"]
        ]);
        var params = req.parameters || [];
        if (params.length) {
          html += table(["Parameter", "Value"], params.map(function (p) {
            return [escapeHtml(p.name), escapeHtml(p.value)];
          }));
        }
      });
      html += '<p class="hint">' + escapeHtml(data.note) + "</p>";
      render("approvals-body", html);
    }).catch(function (error) {
      renderFailure("approvals-body", error, "graph");
    });
  }

  /* ---------------------------------------------------------------- *
   * 7. declared verification and the accept cycle
   * ---------------------------------------------------------------- */
  function runDeclaredVerification() {
    var declarationId = el("decl-verify").value;
    var key = el("key-verify").value.trim();
    var box = el("declared-body");
    if (!declarationId) { render(box, stateHtml("empty", "Choose a declaration first.")); return Promise.resolve(); }
    if (!key) { render(box, stateHtml("refused", "An idempotency key is required: a repeat must be refusable.")); return Promise.resolve(); }
    render(box, stateHtml("loading", "Running the declared verification…"));
    return request("/api/product/verify", {
      method: "POST",
      body: { declaration_id: declarationId, idempotency_key: key },
      timeout: 300000
    }).then(function (data) {
      render(box, resultHtml("Declared verification", data));
      loadRuns();
    }).catch(function (error) {
      renderFailure(box, error, "declared");
    });
  }

  function runAcceptCycle() {
    var declarationId = el("decl-accept").value;
    var key = el("key-accept").value.trim();
    var box = el("declared-body");
    if (!declarationId) { render(box, stateHtml("empty", "Choose a declaration first.")); return Promise.resolve(); }
    if (!key) { render(box, stateHtml("refused", "An idempotency key is required: a repeat must be refusable.")); return Promise.resolve(); }
    render(box, stateHtml("loading", "Running the accept cycle…"));
    return request("/api/product/accept", {
      method: "POST",
      body: { declaration_id: declarationId, idempotency_key: key },
      timeout: 300000
    }).then(function (data) {
      render(box, resultHtml("Accept cycle", data));
      loadRuns();
    }).catch(function (error) {
      renderFailure(box, error, "declared");
    });
  }

  /* A run result is rendered field by field. `success` is reported, never
     computed, and is never conflated with the acceptance verdict. */
  function resultHtml(title, data) {
    var html = '<h3 class="sec">' + escapeHtml(title) + "</h3>" + kv([
      ["Run", '<span class="mono">' + escapeHtml(data.run_id) + "</span>"],
      ["State", pillFor(data.state)],
      ["Reported success", data.success ? '<span class="tag tag-ok">true</span>'
        : '<span class="tag tag-bad">false</span>'],
      ["Provider used", escapeHtml(data.provider_used || "(none)")],
      ["Tokens", escapeHtml(data.tokens)],
      ["Duration", escapeHtml(data.duration_seconds) + "s"]
    ]);
    if (data.error) {
      html += stateHtml("error", "Reported error: " + data.error);
    }
    if (data.output) {
      html += '<h3 class="sec">Output</h3><pre class="mono">' +
        escapeHtml(data.output) + "</pre>";
    }
    html += '<p class="hint">`success` means the action ran and its verification ' +
      "passed; the acceptance verdict is recorded on the run record, not here.</p>";
    return html;
  }

  /* ---------------------------------------------------------------- *
   * 8. memory
   * ---------------------------------------------------------------- */
  function loadMemory() {
    render("memory-body", stateHtml("loading", "Loading Project Memory…"));
    return request("/api/product/memory").then(function (data) {
      render("memory-body", memoryHtml(data));
    }).catch(function (error) {
      renderFailure("memory-body", error, "memory");
    });
  }

  function memoryHtml(data) {
    var buckets = data.buckets || {};
    var html = "";
    function bucket(title, key, cls, note) {
      var items = buckets[key] || [];
      var out = '<h3 class="sec">' + escapeHtml(title) + " (" + items.length + ")</h3>";
      if (!items.length) {
        return out + stateHtml("empty", note || "Nothing here.");
      }
      out += table(["Id", "Statement", "Trust", "Freshness", ""], items.map(function (entry) {
        // The invalidate route is reachable from here, on a real entry id. Without
        // this control `POST /api/product/memory/{memory_id}/invalidate` would be an
        // API-only route -- a working capability with no way to reach it from the
        // console -- and the completeness check that forbids that would be asserting
        // something untrue.
        var invalidate = entry.status === "INVALIDATED"
          ? '<span class="tag tag-bad">invalidated</span>'
          : '<button type="button" class="btn btn-quiet" data-invalidate="' +
            escapeHtml(entry.memory_id) + '">Invalidate</button>';
        return [
          escapeHtml(entry.memory_id),
          escapeHtml(entry.statement),
          '<span class="tag ' + cls + '">' + escapeHtml(entry.trust_level) + "</span>",
          entry.freshness
            ? '<span class="tag ' + (entry.freshness.outcome === "UNCHANGED" ? "tag-ok"
                : entry.freshness.outcome === "UNPINNED" ? "tag-unknown" : "tag-warn") + '">' +
              escapeHtml(entry.freshness.outcome) + "</span>"
            : '<span class="tag tag-unknown">—</span>',
          invalidate
        ];
      }));
      var withHistory = items.filter(function (entry) {
        return (entry.history || []).length;
      });
      if (withHistory.length) {
        out += "<details><summary>History</summary>";
        withHistory.forEach(function (entry) {
          out += '<p class="mono">' + escapeHtml(entry.memory_id) + ": " +
            escapeHtml((entry.history || []).map(function (h) {
              return h.action + (h.detail ? " (" + h.detail + ")" : "");
            }).join(" → ")) + "</p>";
        });
        out += "</details>";
      }
      return out;
    }

    html += bucket("Confirmed facts", "confirmed_facts", "tag-ok",
      "No fact has been confirmed against structural evidence yet.");
    html += bucket("Verified conclusions", "verified_conclusions", "tag-unknown",
      "No inference has been drawn yet.");
    html += bucket("Execution results", "execution_results", "tag-unknown",
      "No execution result has been recorded yet.");
    html += bucket("Hypotheses (not facts)", "hypotheses", "tag-warn",
      "No hypothesis has been recorded yet.");
    html += bucket("Unknown data", "unknown_data", "tag-unknown",
      "Nothing has been marked as unknown.");
    html += bucket("Stale / invalidated", "stale", "tag-bad",
      "Nothing is stale.");

    html += '<p class="hint">' + escapeHtml(
      "Distinctions: confirmed facts " + (data.distinctions || {}).confirmed_facts +
      "; verified conclusions " + (data.distinctions || {}).verified_conclusions +
      "; hypotheses " + (data.distinctions || {}).hypotheses + "."
    ) + "</p>";
    return html;
  }

  function submitMemory() {
    var statement = el("mem-statement").value.trim();
    var status = el("mem-status");
    if (!statement) { status.textContent = "Enter a statement first."; return Promise.resolve(); }
    status.textContent = "Recording…";
    return request("/api/product/memory", {
      method: "POST",
      body: {
        statement: statement,
        kind: el("mem-kind").value,
        subject: el("mem-subject").value.trim()
      }
    }).then(function (data) {
      status.textContent = "Recorded as " + data.kind + " (confirmed fact: " +
        (data.confirmed_fact ? "yes" : "no") + ").";
      el("mem-statement").value = "";
      loadMemory();
    }).catch(function (error) {
      status.textContent = "";
      var box = el("memory-body");
      renderFailure(box, error, "memory");
    });
  }

  /* Weaken one entry explicitly. The route keeps the whole history and can only
     move a claim to invalidated -- it can never promote or confirm one. */
  function invalidateMemory(memoryId) {
    if (!memoryId) { return Promise.resolve(); }
    var box = el("memory-body");
    render(box, stateHtml("loading", "Invalidating " + memoryId + "…"));
    return request(
      "/api/product/memory/" + encodeURIComponent(memoryId) + "/invalidate",
      { method: "POST", body: { reason: "invalidated from the operating console" } }
    ).then(function (data) {
      render(box, stateHtml("empty",
        "Invalidated " + memoryId + " (status " + (data.status || "INVALIDATED") +
        "). Its history is kept; nothing was deleted."));
      loadMemory();
    }).catch(function (error) {
      renderFailure(box, error, "memory");
    });
  }

  function refreshMemory() {
    render("memory-body", stateHtml("loading", "Re-checking every pinned entry…"));
    return request("/api/product/memory/refresh", { method: "POST", body: {}, timeout: 120000 })
      .then(function (data) {
        var refresh = data.refresh || {};
        var html = stateHtml("empty",
          "Checked " + refresh.checked + " · unchanged " + refresh.unchanged +
          " · stale " + refresh.stale + " · renamed " + refresh.renamed +
          " · deleted " + refresh.deleted + " · contradictions " +
          (data.contradictions_added || 0) +
          (data.wrote ? "" : " — nothing changed, so nothing was written"));
        render("memory-body", html + memoryHtml(data.memory || { buckets: {}, distinctions: {} }));
      }).catch(function (error) {
        renderFailure("memory-body", error, "memory");
      });
  }

  /* ---------------------------------------------------------------- *
   * 9. change impact
   * ---------------------------------------------------------------- */
  function setBaseline() {
    render("impact-body", stateHtml("loading", "Capturing the workspace as the baseline…"));
    return request("/api/product/baseline", { method: "POST", body: {}, timeout: 120000 })
      .then(function (data) {
        render("impact-body", stateHtml("empty",
          "Baseline captured at " + data.created_at + " (" + data.file_count +
          " files, " + data.fact_count + " structural facts). Compute impact after " +
          "the workspace changes."));
        loadRuns();
      }).catch(function (error) {
        renderFailure("impact-body", error, "impact");
      });
  }

  /* Record the stale facts and re-evaluate every file-pinned memory entry against
     the workspace as it is now. This is the one write on this panel: the existing
     ProjectMemory.refresh moves a changed, deleted or renamed source to STALE and
     appends history. Nothing is deleted and nothing is promoted. */
  function invalidateStaleFacts() {
    render("impact-body", stateHtml("loading", "Re-evaluating pinned memory entries…"));
    return request("/api/product/stale-facts",
      { method: "POST", body: { invalidate: true }, timeout: 120000 })
      .then(function (data) {
        var html = kv([
          ["Stale facts", escapeHtml((data.stale_fact_ids || []).length)],
          ["Invalidation performed", data.invalidation_performed ? "yes" : "no"],
          ["Memory entries moved to STALE", escapeHtml((data.invalidated_memory_ids || []).length)],
          ["History retained", escapeHtml((data.history_retained || []).length) + " ids"]
        ]);
        if (!(data.stale_facts || []).length) {
          html += stateHtml("empty",
            "No structural fact stopped being current, so nothing was invalidated.");
        } else {
          html += '<h3 class="sec">Facts no longer current</h3>' + table(
            ["Fact", "Type", "Source", "Reason"],
            data.stale_facts.map(function (item) {
              return [
                '<span class="mono">' + escapeHtml(String(item.fact_id).slice(0, 16)) + "</span>",
                escapeHtml(item.fact_type),
                escapeHtml(item.source_path),
                '<span class="tag tag-warn">' + escapeHtml(item.reason) + "</span>"
              ];
            })
          );
        }
        if ((data.dependent_conclusions || []).length) {
          html += '<h3 class="sec">Conclusions resting on evidence that moved</h3>' + table(
            ["Entry", "Status", "Stale evidence"],
            data.dependent_conclusions.map(function (item) {
              return [
                escapeHtml(item.memory_id),
                '<span class="tag tag-unknown">' + escapeHtml(item.status) + "</span>",
                escapeHtml((item.stale_evidence || []).join(", "))
              ];
            })
          );
        }
        html += '<p class="hint">' + escapeHtml(data.note) + "</p>";
        render("impact-body", html);
        loadMemory();
      }).catch(function (error) {
        renderFailure("impact-body", error, "impact");
      });
  }

  function loadImpact() {
    render("impact-body", stateHtml("loading", "Comparing the workspace with the baseline…"));
    return request("/api/product/change-impact", { timeout: 120000 }).then(function (data) {
      var counts = data.classification_counts || {};
      var plan = data.verification_plan || {};
      var html = kv([
        ["Changed files", escapeHtml(data.changed_count)],
        ["CONFIRMED findings", '<span class="tag tag-ok">' + escapeHtml(counts.confirmed || 0) + "</span>"],
        ["PROBABLE findings", '<span class="tag tag-warn">' + escapeHtml(counts.probable || 0) + "</span>"],
        ["UNKNOWN findings", '<span class="tag tag-unknown">' + escapeHtml(counts.unknown || 0) + "</span>"],
        ["Stale facts", escapeHtml(counts.stale_facts || 0)],
        ["Dependent conclusions", escapeHtml(counts.dependent_conclusions || 0)],
        ["Ambiguous moves", escapeHtml(counts.ambiguous_moves || 0)],
        ["Planned checks", escapeHtml((plan.checks || []).length)],
        ["Coverage gaps", escapeHtml((plan.coverage_gaps || []).length)],
        ["Baseline", '<span class="mono">' +
          escapeHtml(String(data.baseline_snapshot_id || "").slice(0, 24)) + "</span>"],
        ["Baseline was", data.baseline_seeded_now
          ? "just seeded — no earlier reference point existed"
          : "an earlier reference point"]
      ]);
      if (data.baseline_seeded_now) {
        html += stateHtml("empty",
          "No earlier reference point existed, so this baseline was seeded from the " +
          "current state. Change it, then compute impact again for a real comparison.");
      }
      if (!data.changes || !data.changes.length) {
        html += stateHtml("empty", "No file differs from the baseline.");
      } else {
        html += '<h3 class="sec">Observed changes</h3>' + table(
          ["Path", "Kind", "Classification", "Stale", "Facts", "Deps", "Requirements", "Tests"],
          data.changes.map(function (change) {
            var kind = change.kind === "DELETED" ? "tag-bad"
              : change.kind === "ADDED" ? "tag-unknown" : "tag-warn";
            var rename = change.probable_rename_to
              ? ' <span class="tag tag-unknown">probable rename → ' +
                escapeHtml(change.probable_rename_to) + "</span>"
              : "";
            if (change.ambiguous_move) {
              rename += ' <span class="tag tag-bad">ambiguous move — NOT resolved (' +
                escapeHtml((change.move_candidates || []).length) + " candidates)</span>";
            }
            return [
              escapeHtml(change.path) + rename,
              '<span class="tag ' + kind + '">' + escapeHtml(change.kind) + "</span>",
              classificationTag(change.classification),
              change.stale ? "yes" : "no",
              escapeHtml((change.affected_fact_ids || []).length),
              escapeHtml((change.dependent_nodes || []).length) +
                (change.dependent_nodes_truncated ? " (truncated)" : ""),
              escapeHtml((change.affected_requirements || []).length),
              escapeHtml((change.affected_tests || []).length)
            ];
          })
        );

        /* The basis of each finding, so a reader can audit the classification
           instead of trusting the label. UNKNOWN means the basis was empty. */
        html += '<h3 class="sec">Why each finding is classified as it is</h3>';
        data.changes.forEach(function (change) {
          html += '<h4 class="sec">' + escapeHtml(change.path) + " " +
            classificationTag(change.classification) + "</h4>";
          if (change.basis_is_empty) {
            html += stateHtml("indeterminate",
              "The basis for this finding is EMPTY. An empty basis is classified " +
              "UNKNOWN and is never reported as a confirmed fact.");
          } else {
            html += basisHtml(change.basis);
          }
          if (change.ambiguous_move) {
            html += stateHtml("refused",
              "Ambiguous move: " + (change.move_candidates || []).length +
              " candidate path(s) carry identical bytes (" +
              escapeHtml((change.move_candidates || []).join(", ")) +
              "). No candidate was chosen and none may be treated as the destination.");
          }
          if ((change.affected_requirements || []).length) {
            html += table(["Requirement", "Criterion", "Expectation path", "Action"],
              change.affected_requirements.map(function (item) {
                return [
                  escapeHtml(item.requirement_id),
                  escapeHtml(item.criterion_id),
                  '<span class="mono">' + escapeHtml(item.expectation_path) + "</span>",
                  escapeHtml(item.action)
                ];
              }));
          }
          if ((change.affected_tests || []).length) {
            html += '<p class="hint">Related tests (by path relation, NOT proof of ' +
              "coverage): " + escapeHtml(change.affected_tests.map(function (test) {
                return test.path;
              }).join(", ")) + "</p>";
          }
          html += '<h4 class="sec">Dependencies reached</h4>' + basisHtml(
            (change.dependent_nodes || []).reduce(function (rows, node) {
              return rows.concat(node.basis || []);
            }, [])
          );
        });
      }

      if ((data.stale_facts || []).length) {
        html += '<h3 class="sec">Structural facts no longer current</h3>' + table(
          ["Fact", "Type", "Source", "Reason"],
          data.stale_facts.map(function (item) {
            return [
              '<span class="mono">' + escapeHtml(String(item.fact_id).slice(0, 16)) + "</span>",
              escapeHtml(item.fact_type),
              escapeHtml(item.source_path),
              '<span class="tag tag-warn">' + escapeHtml(item.reason) + "</span>"
            ];
          })
        );
      } else {
        html += stateHtml("empty",
          "No structural fact stopped being current. That is a statement about the " +
          "facts compared, not evidence that nothing changed.");
      }

      if ((data.dependent_conclusions || []).length) {
        html += '<h3 class="sec">Conclusions resting on evidence that moved</h3>' + table(
          ["Entry", "Kind", "Status", "Stale evidence"],
          data.dependent_conclusions.map(function (item) {
            return [
              escapeHtml(item.memory_id),
              escapeHtml(item.kind),
              '<span class="tag tag-unknown">' + escapeHtml(item.status) + "</span>",
              escapeHtml((item.stale_evidence || []).join(", "))
            ];
          })
        );
        html += '<p class="hint">These entries are NAMED, not rewritten: nothing here ' +
          "edits them or promotes them. Re-establishing one is an explicit act.</p>";
      }

      if ((plan.checks || []).length) {
        html += '<h3 class="sec">Checks this change set implies</h3>' + table(
          ["Check", "State", "Criticality", "Reason"],
          plan.checks.map(function (check) {
            return [
              escapeHtml(check.check_id),
              pillFor(check.state),
              escapeHtml(check.criticality),
              escapeHtml(check.reason)
            ];
          })
        );
      }
      if ((plan.coverage_gaps || []).length) {
        html += stateHtml("unavailable",
          "Coverage gaps: " + plan.coverage_gaps.join(" | "));
      }

      (data.analysis_notes || []).forEach(function (note) {
        html += '<p class="hint">' + escapeHtml(note) + "</p>";
      });

      if (data.criteria_touched && data.criteria_touched.length) {
        html += '<h3 class="sec">Checks to re-run</h3>' + table(
          ["Criterion", "Requirement", "Expectation path", "Action"],
          data.criteria_touched.map(function (c) {
            return [
              escapeHtml(c.criterion_id),
              escapeHtml(c.requirement_id),
              '<span class="mono">' + escapeHtml(c.expectation_path) + "</span>",
              escapeHtml(c.action)
            ];
          })
        );
      } else {
        html += stateHtml("empty",
          "No declared acceptance expectation names a changed path, so no check is " +
          "linked here. Nothing is reported rather than guessed.");
      }

      if (data.unlinked_requirements && data.unlinked_requirements.length) {
        html += stateHtml("unavailable",
          "Requirements with no established link to any changed file: " +
          data.unlinked_requirements.join(", ") +
          ". Traceability is not claimed for these.");
      }

      if (data.memory_entries_pinned_to_changes && data.memory_entries_pinned_to_changes.length) {
        html += '<h3 class="sec">Memory pinned to a changed file</h3>' + table(
          ["Id", "Statement", "Freshness"],
          data.memory_entries_pinned_to_changes.map(function (entry) {
            return [
              escapeHtml(entry.memory_id),
              escapeHtml(entry.statement),
              escapeHtml((entry.freshness || {}).outcome || "—")
            ];
          })
        );
        html += '<div class="row"><button type="button" class="btn" data-retry="memrefresh">' +
          "Re-check memory freshness</button></div>";
      } else {
        html += stateHtml("empty", "No memory entry is pinned to a changed file.");
      }

      var claims = data.claims || {};
      html += '<h3 class="sec">What this report proves, and what it does not</h3>';
      html += "<p><strong>Proved</strong></p><ul>" +
        (claims.proved || []).map(function (c) { return "<li>" + escapeHtml(c) + "</li>"; }).join("") +
        "</ul><p><strong>Not proved</strong></p><ul>" +
        (claims.not_proved || []).map(function (c) { return "<li>" + escapeHtml(c) + "</li>"; }).join("") +
        "</ul>";
      html += '<p class="hint">' + escapeHtml(data.note) + "</p>";
      render("impact-body", html);
    }).catch(function (error) {
      renderFailure("impact-body", error, "impact");
    });
  }

  /* ---------------------------------------------------------------- *
   * 9b. Block #9: evidence graph, verification plan, repair decision
   *
   * Each of these renders ONLY what a route returned. A state the server did
   * not publish is never inferred here: a check the server reported as SKIPPED
   * or UNKNOWN is drawn as SKIPPED or UNKNOWN, and there is no code path in
   * this file that promotes one to PASSED.
   * ---------------------------------------------------------------- */

  function classificationTag(label) {
    var value = String(label || "").toUpperCase();
    var cls = value === "CONFIRMED" ? "tag-ok"
      : value === "PROBABLE" ? "tag-warn" : "tag-unknown";
    return '<span class="tag ' + cls + '">' + escapeHtml(value || "UNKNOWN") + "</span>";
  }

  function basisHtml(basis) {
    if (!basis || !basis.length) {
      return '<p class="state-indeterminate">No basis was recorded for this item, ' +
        "so it is reported as UNKNOWN. An empty basis is never a confirmed fact.</p>";
    }
    return table(["Basis", "Ref", "Detail"], basis.map(function (item) {
      return [
        escapeHtml(item.kind) + (item.structural
          ? ' <span class="tag tag-ok">structural</span>'
          : ' <span class="tag tag-unknown">weak</span>'),
        escapeHtml(item.ref),
        escapeHtml(item.detail)
      ];
    }));
  }

  function loadEvidence() {
    var runId = el("evidence-run-id") ? el("evidence-run-id").value.trim() : "";
    render("evidence-body", stateHtml("loading", "Assembling the traceability chain…"));
    var path = runId
      ? "/api/product/evidence-graph?graph_run_id=" + encodeURIComponent(runId)
      : "/api/product/evidence-graph";
    return request(path).then(function (data) {
      var html = kv([
        ["Attempt", escapeHtml(data.graph_run_id || "— none recorded —")],
        ["Attempt recorded", data.attempt_recorded ? "yes" : "no"],
        ["Graph status reported", pillFor(data.graph_status_reported || "UNKNOWN")],
        ["Final status", pillFor(data.final_status)],
        ["Acceptance refused", data.acceptance_refused ? "yes" : "no"],
        ["Missing links", escapeHtml(data.missing_link_count)]
      ]);
      if (data.acceptance_refused) {
        html += stateHtml("refused",
          "Acceptance refused: " + (data.acceptance_refusal_reason || "") +
          " Missing mandatory link kinds: " +
          (data.mandatory_missing_kinds || []).join(", "));
      }
      html += '<p class="hint">' + escapeHtml(data.verdict_source || "") + "</p>";

      if (!data.links || !data.links.length) {
        html += stateHtml("empty",
          "No transition is recorded, so the chain has no links to show.");
        render("evidence-body", html);
        return;
      }
      html += '<h3 class="sec">Transitions</h3>' + table(
        ["From → To", "Status", "Source", "Target", "Missing code", "Evidence"],
        data.links.map(function (link) {
          var status = link.status === "MISSING"
            ? '<span class="tag tag-bad">MISSING</span>'
            : link.status === "REFUSED"
              ? '<span class="tag tag-warn">REFUSED</span>'
              : '<span class="tag tag-ok">ESTABLISHED</span>';
          return [
            escapeHtml(link.from + " → " + link.to),
            status,
            escapeHtml(link.source_ref || "—"),
            escapeHtml(link.target_ref || "—"),
            link.missing_code
              ? '<span class="tag tag-unknown">' + escapeHtml(link.missing_code) + "</span>"
              : "—",
            escapeHtml((link.evidence || []).join(" · "))
          ];
        })
      );
      if (data.missing_links && data.missing_links.length) {
        html += '<h3 class="sec">Missing links, named explicitly</h3>' + table(
          ["Transition", "Missing code", "Detail"],
          data.missing_links.map(function (link) {
            return [
              escapeHtml(link.from + " → " + link.to),
              escapeHtml(link.missing_code),
              escapeHtml(link.detail)
            ];
          })
        );
      }
      var verdicts = data.requirement_verdicts || {};
      var verdictKeys = Object.keys(verdicts);
      if (verdictKeys.length) {
        html += '<h3 class="sec">Requirement verdicts (computed)</h3>' + table(
          ["Requirement", "Verdict"],
          verdictKeys.map(function (key) { return [escapeHtml(key), pillFor(verdicts[key])]; })
        );
      }
      html += '<h3 class="sec">Agent claims</h3>';
      if (data.claimed_by_agent && data.claimed_by_agent.length) {
        html += table(["Source", "Claim"], data.claimed_by_agent.map(function (item) {
          return [escapeHtml(item.source || ""), escapeHtml(item.claim || "")];
        }));
        html += '<p class="hint">Claims are recorded for comparison only. The final ' +
          "status above is computed from the recorded verification observations and " +
          "does not read this compartment.</p>";
      } else {
        html += stateHtml("empty",
          "No agent claim is recorded in this build: the execution path makes no " +
          "generative dispatch. The compartment is shown empty rather than omitted, " +
          "so “no claim was made” is distinguishable from “claims are not collected”.");
      }
      (data.notes || []).forEach(function (note) {
        html += '<p class="hint">' + escapeHtml(note) + "</p>";
      });
      render("evidence-body", html);
    }).catch(function (error) {
      renderFailure("evidence-body", error, "evidence");
    });
  }

  function loadVerificationPlan() {
    render("verification-body", stateHtml("loading", "Planning checks from changes and the dependency graph…"));
    return request("/api/product/verification-plan", { timeout: 120000 }).then(function (data) {
      var counts = data.state_counts || {};
      var html = kv([
        ["NOT_RUN", escapeHtml(counts.NOT_RUN || 0)],
        ["RUNNING", escapeHtml(counts.RUNNING || 0)],
        ["PASSED", '<span class="tag tag-ok">' + escapeHtml(counts.PASSED || 0) + "</span>"],
        ["FAILED", '<span class="tag tag-bad">' + escapeHtml(counts.FAILED || 0) + "</span>"],
        ["SKIPPED", '<span class="tag tag-warn">' + escapeHtml(counts.SKIPPED || 0) + "</span>"],
        ["UNKNOWN", '<span class="tag tag-unknown">' + escapeHtml(counts.UNKNOWN || 0) + "</span>"]
      ]);
      html += '<p class="hint">Only PASSED counts as a pass. SKIPPED and UNKNOWN are ' +
        "shown as themselves and are never counted as passed.</p>";

      if (!data.checks || !data.checks.length) {
        html += stateHtml("empty",
          "No check could be planned: the composition declares no acceptance criterion " +
          "and no changed path was reported.");
      } else {
        html += '<h3 class="sec">Planned checks</h3>' + table(
          ["Check", "State", "Criticality", "Why it was chosen", "Requirement", "Evidence"],
          data.checks.map(function (check) {
            return [
              escapeHtml(check.check_id),
              pillFor(check.state),
              escapeHtml(check.criticality),
              escapeHtml(check.reason) +
                (check.driven_by && check.driven_by.length
                  ? ' <span class="tag tag-warn">driven by ' +
                    escapeHtml(check.driven_by.join(", ")) + "</span>"
                  : ""),
              escapeHtml((check.requirements || []).join(", ") || "—"),
              escapeHtml((check.evidence || []).join(" · ") || "—")
            ];
          })
        );
      }

      if (data.coverage_gaps && data.coverage_gaps.length) {
        html += stateHtml("unavailable",
          "Coverage gaps, named explicitly: " + data.coverage_gaps.join(" | "));
      }
      if (data.requirements_without_checks && data.requirements_without_checks.length) {
        html += stateHtml("unavailable",
          "Requirements with no check at all: " +
          data.requirements_without_checks.join(", "));
      }
      (data.notes || []).forEach(function (note) {
        html += '<p class="hint">' + escapeHtml(note) + "</p>";
      });
      render("verification-body", html);
    }).catch(function (error) {
      renderFailure("verification-body", error, "verification");
    });
  }

  function loadRepairPlan() {
    var runId = el("repair-run-id").value.trim();
    if (!runId) {
      render("repair-body", stateHtml("empty", "Enter a graph run id."));
      return Promise.resolve();
    }
    render("repair-body", stateHtml("loading", "Deciding within the budget…"));
    var body = { graph_run_id: runId };
    var previous = el("repair-previous-marker").value.trim();
    if (previous) { body.previous_progress_marker = previous; }
    return request("/api/product/repair-plan", { method: "POST", body: body })
      .then(function (data) {
        var html = kv([
          ["Outcome", pillFor(data.outcome)],
          ["Stop rule", escapeHtml(data.stop_rule)],
          ["Attempts", escapeHtml(data.budget.attempts_used + " / " + data.budget.attempt_cap +
            " (" + data.budget.attempts_remaining + " left)")],
          ["Steps targeted", escapeHtml(data.budget.steps_targeted + " / " + data.budget.step_budget)],
          ["Seconds elapsed", escapeHtml(Math.round(data.budget.seconds_elapsed) + " / " +
            data.budget.seconds_budget)],
          ["Dispatch performed", data.dispatch_performed ? "yes" : "no"],
          ["Progress marker", '<span class="mono">' + escapeHtml(data.progress_marker) + "</span>"]
        ]);
        html += stateHtml("empty",
          "This panel plans and refuses. It performs no repair: " +
          escapeHtml(data.dispatch_path || ""));
        if (data.steps && data.steps.length) {
          html += '<h3 class="sec">Per-step decisions</h3>' + table(
            ["Step", "Cause", "Confidence", "Repeat guarantee", "Admissible", "Refusal"],
            data.steps.map(function (step) {
              return [
                escapeHtml(step.step_id),
                escapeHtml(step.probable_cause.code),
                '<span class="tag tag-unknown">' +
                  escapeHtml(step.probable_cause.confidence) + "</span>",
                escapeHtml(step.repeat_guarantee),
                step.authorized_to_dispatch ? "yes" : "no",
                escapeHtml(step.refusal_code || "—")
              ];
            })
          );
          data.steps.forEach(function (step) {
            html += '<h4 class="sec">' + escapeHtml(step.step_id) + "</h4>" + kv([
              ["Probable cause", escapeHtml(step.probable_cause.statement)],
              ["Confidence basis", escapeHtml((step.probable_cause.basis || []).join(" · ") || "—")],
              ["Limitations", escapeHtml(step.probable_cause.limitations || "—")],
              ["Targeted condition", escapeHtml(step.targeted_condition)],
              ["Proposed action", escapeHtml(step.proposed_action)],
              ["Approval required", step.approval_required ? "yes" : "no"],
              ["Approval resolved", step.approval_resolved ? "yes" : "no"]
            ]);
          });
        }
        (data.notes || []).forEach(function (note) {
          html += '<p class="hint">' + escapeHtml(note) + "</p>";
        });
        render("repair-body", html);
      }).catch(function (error) {
        renderFailure("repair-body", error, "repair");
      });
  }

  /* ---------------------------------------------------------------- *
   * 10. recovery
   * ---------------------------------------------------------------- */
  function loadRecovery() {
    render("recovery-body", stateHtml("loading", "Scanning for unfinished attempts…"));
    return request("/api/product/recovery").then(function (data) {
      var findings = data.unfinished || [];
      var html = "";
      if (!findings.length) {
        html += stateHtml("empty", "No unfinished attempt is recorded. Nothing needs recovery.");
      } else {
        html += table(["Attempt", "Classification"], findings.map(function (finding) {
          return [
            escapeHtml(finding.graph_run_id),
            pillFor(finding.classification)
          ];
        }));
      }
      (data.plans || []).forEach(function (plan) {
        if (plan.plan_error) {
          html += stateHtml("error", plan.graph_run_id + ": plan unavailable (" +
            escapeHtml(plan.plan_error) + ")");
          return;
        }
        html += '<h3 class="sec">' + escapeHtml(plan.graph_run_id) + "</h3>" + kv([
          ["Recorded status", pillFor(plan.recorded_status)],
          ["Recoverable", plan.recoverable ? "yes" : "no"],
          ["Blocked reason", escapeHtml(plan.blocked_reason || "—")]
        ]);
        if (plan.steps && plan.steps.length) {
          html += table(["Step", "Status", "Decision", "Reason"], plan.steps.map(function (step) {
            return [
              escapeHtml(step.step_id),
              pillFor(step.status),
              pillFor(step.decision),
              escapeHtml(step.detail || step.reason)
            ];
          }));
        }
      });
      html += '<p class="hint">' + escapeHtml(data.note) +
        " Continuing an attempt requires a claim token, which this console does not issue.</p>";
      render("recovery-body", html);
    }).catch(function (error) {
      renderFailure("recovery-body", error, "recovery");
    });
  }

  /* ---------------------------------------------------------------- *
   * 11. run history and one run's full record
   * ---------------------------------------------------------------- */
  function loadRuns() {
    render("runs-body", stateHtml("loading", "Loading run history…"));
    return request("/api/product/runs").then(function (data) {
      if (!data.runs || !data.runs.length) {
        render("runs-body", stateHtml("empty", "No run has been recorded yet."));
        return;
      }
      var html = table(["Run", "State", "Classification", "Events"], data.runs.map(function (run) {
        var classification = run.classification || {};
        // Each id is a button: the two run routes must be reachable from the page,
        // not merely present on the API.
        return [
          '<button type="button" class="btn btn-quiet" data-run="' +
            escapeHtml(run.run_id) + '">' + escapeHtml(run.run_id) + "</button>",
          pillFor(run.state || run.status),
          // `outcome` is the field the server actually publishes. Reading the old
          // nested key yielded `undefined` for every real run, so the column always
          // rendered the literal "UNKNOWN" instead of the recorded verdict.
          pillFor(classification.outcome || "UNKNOWN"),
          escapeHtml(run.event_count)
        ];
      }));
      if (data.truncated) {
        html += stateHtml("empty", "Showing the newest " + data.runs.length +
          " of " + data.total_recorded + " recorded runs.");
      }
      render("runs-body", html);
    }).catch(function (error) {
      renderFailure("runs-body", error, "runs");
    });
  }

  function loadRunDetail(runId) {
    if (!runId) {
      render("run-detail-body", stateHtml("empty", "Enter a run id."));
      return Promise.resolve();
    }
    render("run-detail-body", stateHtml("loading", "Reading the durable record…"));
    return request("/api/product/runs/" + encodeURIComponent(runId))
      .then(function (record) {
        var html = kv([
          ["Run", '<span class="mono">' + escapeHtml(record.run_id) + "</span>"],
          ["State", pillFor(record.state || record.status)],
          ["Task", '<span class="mono">' + escapeHtml(record.task_id || "—") + "</span>"],
          ["Attempt", escapeHtml(record.attempt_number)],
          ["Events", escapeHtml(record.event_count)]
        ]);
        if (record.classification) {
          html += kv([
            ["Classification", pillFor(record.classification.outcome)],
            ["Reason", escapeHtml(record.classification.reason || "—")]
          ]);
        }
        // The record is shown as stored. Nothing here computes a verdict.
        html += '<details><summary>Raw record</summary><pre class="mono">' +
          escapeHtml(JSON.stringify(record, null, 2)) + "</pre></details>";
        render("run-detail-body", html);
      })
      .catch(function (error) {
        renderFailure("run-detail-body", error, "runs");
      });
  }

  /* ---------------------------------------------------------------- *
   * 12. recovery of one attempt
   * ---------------------------------------------------------------- */
  function loadRecoveryPlan(runId) {
    if (!runId) {
      render("recovery-body", stateHtml("empty", "Enter an attempt id."));
      return Promise.resolve();
    }
    render("recovery-body", stateHtml("loading", "Planning this attempt…"));
    return request("/api/product/recovery/" + encodeURIComponent(runId))
      .then(function (plan) {
        render("recovery-body", recoveryPlanHtml(plan));
      })
      .catch(function (error) {
        renderFailure("recovery-body", error, "recovery");
      });
  }

  function recoveryPlanHtml(plan) {
    var html = kv([
      ["Attempt", '<span class="mono">' + escapeHtml(plan.graph_run_id) + "</span>"],
      ["Recorded status", pillFor(plan.recorded_status)],
      ["Record classification", pillFor(plan.record_classification)],
      ["Recoverable", plan.recoverable ? "yes" : "no"],
      ["Blocked reason", escapeHtml(plan.blocked_reason || "—")]
    ]);
    if (!plan.recoverable) {
      html += stateHtml("indeterminate",
        "This attempt is not recoverable automatically. Nothing will be continued " +
        "from this page: continuing requires a claim token the console does not issue.");
    }
    if (plan.steps && plan.steps.length) {
      html += '<h3 class="sec">Per-step decisions</h3>' + table(
        ["Step", "Status", "Decision", "Reason"],
        plan.steps.map(function (step) {
          return [
            escapeHtml(step.step_id),
            pillFor(step.status),
            pillFor(step.decision),
            escapeHtml(step.detail || step.reason)
          ];
        })
      );
    }
    return html;
  }

  /* ---------------------------------------------------------------- *
   * wiring
   * ---------------------------------------------------------------- */
  var reloaders = {
    onboarding: loadOnboarding,
    requirements: fillDeclarationSelects,
    plan: loadPlan,
    understanding: loadUnderstanding,
    memory: loadMemory,
    graph: function () { return loadGraphStatus().then(loadApprovals); },
    approvals: loadApprovals,
    recovery: loadRecovery,
    runs: loadRuns,
    impact: loadImpact,
    evidence: loadEvidence,
    verification: loadVerificationPlan,
    repair: loadRepairPlan,
    declared: fillDeclarationSelects,
    memrefresh: refreshMemory,
    invalidate: loadMemory
  };

  function reloadAll() {
    setFoot("Reloading…");
    loadOnboarding()
      .then(function () {
        return Promise.all([
          loadPlan(), loadUnderstanding(), loadMemory(), loadRecovery(), loadRuns(),
          loadVerificationPlan()
        ]);
      })
      .then(function () { setFoot("Loaded at " + new Date().toLocaleTimeString()); })
      .catch(function () { setFoot("Some panels could not be loaded — see the messages above."); });
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.dataset) { return; }
    if (target.dataset.reload) {
      var fn = reloaders[target.dataset.reload];
      if (fn) { fn(); }
      return;
    }
    if (target.dataset.retry) {
      var retryFn = reloaders[target.dataset.retry];
      if (retryFn) { retryFn(); }
      return;
    }
    if (target.dataset.invalidate) {
      invalidateMemory(target.dataset.invalidate);
      return;
    }
    if (target.dataset.run) {
      // A run id clicked in the history opens its full record, and offers the same
      // id to the recovery, evidence-graph and repair panels, so every
      // parameterised run route is reachable from a real client action.
      el("detail-run-id").value = target.dataset.run;
      el("recovery-run-id").value = target.dataset.run;
      el("evidence-run-id").value = target.dataset.run;
      el("repair-run-id").value = target.dataset.run;
      loadRunDetail(target.dataset.run);
    }
  });

  el("reload").addEventListener("click", reloadAll);
  el("goal-submit").addEventListener("click", submitGoal);
  el("graph-start").addEventListener("click", startGraph);
  el("graph-refresh").addEventListener("click", function () {
    loadGraphStatus().then(loadApprovals);
  });
  el("graph-resume").addEventListener("click", resumeGraph);
  el("verify-run").addEventListener("click", runDeclaredVerification);
  el("accept-run").addEventListener("click", runAcceptCycle);
  el("memory-refresh").addEventListener("click", refreshMemory);
  el("mem-submit").addEventListener("click", submitMemory);
  el("baseline-set").addEventListener("click", setBaseline);
  el("impact-load").addEventListener("click", loadImpact);
  el("stale-facts").addEventListener("click", invalidateStaleFacts);
  el("detail-load").addEventListener("click", function () {
    loadRunDetail(el("detail-run-id").value.trim());
  });
  el("evidence-load").addEventListener("click", loadEvidence);
  el("repair-load").addEventListener("click", loadRepairPlan);
  el("recovery-plan-load").addEventListener("click", function () {
    loadRecoveryPlan(el("recovery-run-id").value.trim());
  });
  el("graph-run-id").addEventListener("change", function () {
    loadGraphStatus().then(loadApprovals);
  });

  // A brand-new session has no baseline. Saying so is more honest than showing an
  // empty change report that looks like "nothing changed".
  render("impact-body", stateHtml(
    "empty",
    "No baseline has been captured in this session. Nothing is known about what " +
    "changed, which is not the same as nothing having changed."
  ));

  reloadAll();
}());
