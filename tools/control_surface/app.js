"use strict";

const state = {
  token: sessionStorage.getItem("thetowerControlToken") || "",
  refreshing: false,
  timer: null,
  lastStatus: null,
  lastGatePrompted: null,
  lastTournamentLaunchPrompted: null,
};

const byId = (id) => document.getElementById(id);
const dash = "—";

function authHeaders() {
  return state.token ? { Authorization: `Bearer ${state.token}` } : {};
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: {
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch (_error) {
    payload = { error: `Unexpected response (${response.status})` };
  }
  if (response.status === 401) {
    showAuthDialog();
    throw new Error("Access token required");
  }
  if (!response.ok) {
    throw new Error(payload.error || `Request failed (${response.status})`);
  }
  return payload;
}

function setText(id, value) {
  byId(id).textContent = value ?? dash;
}

function setBadge(element, text, kind = "neutral") {
  element.textContent = text;
  element.className = `mini-badge ${kind}`;
}

function formatDate(value) {
  if (!value) return dash;
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString();
}

function formatAge(seconds) {
  if (seconds == null) return "unknown age";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m ago`;
}

function formatRemaining(seconds) {
  if (seconds == null) return "indefinite";
  if (seconds <= 0) return "expiry pending";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours) return `${hours}h ${minutes}m remaining`;
  if (minutes) return `${minutes}m ${secs}s remaining`;
  return `${secs}s remaining`;
}

function formatPriorTransition(observation) {
  if (!observation) return "No earlier state transition in the current log tail";
  const wave = observation.wave == null ? "" : ` · wave ${observation.wave}`;
  return `${observation.state_label || dash}${wave} · ${formatDate(observation.observed_at)}`;
}

function renderStatus(payload) {
  state.lastStatus = payload;
  const control = payload.control || {};
  const observation = payload.observation;
  const runtime = payload.runtime || { instances: [] };
  const processService = payload.process_service;
  const directive = control.state || "UNKNOWN";

  setText("directiveState", directive);
  byId("directiveState").className = `state-pill ${directive.toLowerCase()}`;
  let directiveDetail = control.updated_at ? `Updated ${formatDate(control.updated_at)}` : "No persisted update time";
  if (directive === "PAUSED") directiveDetail = `Paused ${formatRemaining(control.remaining_seconds)}`;
  if (control.error) directiveDetail = control.error;
  setText("directiveDetail", directiveDetail);
  setText("currentMode", control.mode);
  setText("controlUpdated", formatDate(control.updated_at));
  byId("modeSelect").value = ["RETRY", "WAIT", "HOME"].includes(control.mode) ? control.mode : "RETRY";
  const gameSpeedTarget = Number(control.game_speed_target);
  const normalizedGameSpeedTarget = Number.isFinite(gameSpeedTarget)
    ? gameSpeedTarget
    : 6.3;
  byId("gameSpeedTargetSelect").value = normalizedGameSpeedTarget.toFixed(1);
  const gameSpeedWarning = byId("gameSpeedTargetWarning");
  gameSpeedWarning.hidden = normalizedGameSpeedTarget >= 6.3;
  gameSpeedWarning.textContent = normalizedGameSpeedTarget < 6.3
    ? `Exact x${normalizedGameSpeedTarget.toFixed(1)} persists across current and future runs.`
    : "";

  if (observation) {
    setText("observedState", observation.state_label);
    setText("observedWave", observation.wave);
    setText("observedCoins", observation.coins_per_minute);
    setText("lastObserved", `${formatDate(observation.observed_at)} · ${formatAge(observation.age_seconds)}`);
    setText("observedMenu", observation.menu);
    setText("observedSecondary", observation.secondary?.join(", ") || dash);
    setText("observedOverlays", observation.overlays?.join(", ") || dash);
    setText("priorTransition", formatPriorTransition(payload.prior_transition));
    setBadge(byId("heartbeatBadge"), observation.stale ? "Stale" : "Fresh", observation.stale ? "warn" : "good");
    byId("staleWarning").hidden = !observation.stale;
  } else {
    ["observedState", "observedWave", "observedCoins", "lastObserved", "observedMenu", "observedSecondary", "observedOverlays", "priorTransition"].forEach((id) => setText(id, dash));
    setBadge(byId("heartbeatBadge"), "No heartbeat", "bad");
    byId("staleWarning").hidden = false;
  }

  const active = (runtime.instances || []).find((instance) => instance.active) || (runtime.instances || [])[0];
  setText("runtimeTarget", active?.target);
  setText("runtimePid", active?.pid);
  setText("runtimeStarted", formatDate(active?.started_at));
  setText("processService", processService?.service);
  setText(
    "processState",
    processService?.available
      ? `${processService.active_state || dash} / ${processService.sub_state || dash}`
      : processService?.error || "Not configured",
  );
  if (runtime.active) setBadge(byId("runtimeBadge"), "Owner active", "good");
  else if (active) setBadge(byId("runtimeBadge"), "Stale lock", "warn");
  else setBadge(byId("runtimeBadge"), "No owner", "bad");

  const stateAck = payload.acknowledgements?.state;
  if (stateAck?.acknowledges_current) setBadge(byId("ackBadge"), "Applied by runtime", "good");
  else if (directive === "STOPPED" && !runtime.active) setBadge(byId("ackBadge"), "Runtime stopped", "good");
  else setBadge(byId("ackBadge"), "Awaiting runtime", "warn");

  const connection = byId("connectionBadge");
  connection.className = `connection-badge ${payload.healthy ? "is-healthy" : "is-warning"}`;
  setText("connectionText", payload.healthy ? "Linux host healthy" : "Host needs attention");

  document.querySelectorAll("[data-control-action]").forEach((button) => {
    button.disabled = Boolean(control.error);
  });
  const processActive = Boolean(runtime.active || processService?.active);
  document.querySelectorAll("[data-process-action]").forEach((button) => {
    const action = button.dataset.processAction;
    const unavailable = Boolean(control.error) || !processService?.available;
    button.disabled = unavailable
      || (action === "start" && processActive)
      || (action === "stop" && !processService?.active)
      || (action === "restart_attached" && (
        !processService?.active
        || (observation?.stale === false
          && !observation?.state_label?.startsWith("RUNNING"))
      ));
  });
  renderRunConfiguration(
    control,
    processActive,
  );
  renderExclusiveValidation(control);
  renderGateDecision(control.gate_decision);
}

function renderExclusiveValidation(control) {
  const ledger = control.exclusive_validation || {};
  const receipts = ledger.receipts || {};
  let receipt = Object.values(receipts).find((candidate) =>
    ["claimed", "running", "cleanup"].includes(candidate?.status));
  if (!receipt) {
    receipt = receipts[ledger.current_request_id];
  }
  if (!receipt) {
    setText("exclusiveValidationSummary", "No exclusive strategy validation request.");
    renderTournamentLaunch(null);
    return;
  }
  if (receipt.status === "result") {
    if (receipt.outcome === "ready") {
      const launchStatus = receipt.launch?.status;
      const labels = {
        awaiting_operator: "Tournament validation passed; waiting for Start Tournament or Cancel.",
        requested: "Tournament Start is authorized and waiting for the runtime.",
        claimed: "Tournament launch is in progress under the current runtime owner.",
        started: receipt.launch?.reason || "Tournament was started.",
        cancelled: receipt.launch?.reason || "Automatic Tournament launch was cancelled.",
        failed: `Tournament launch failed: ${receipt.launch?.reason || "reason unavailable"}`,
      };
      setText(
        "exclusiveValidationSummary",
        labels[launchStatus]
          || "Tournament validation passed before automatic launch confirmation was available; start manually.",
      );
    } else {
      setText(
        "exclusiveValidationSummary",
        `Tournament validation ${receipt.outcome || "failed"}: ${receipt.reason || "reason unavailable"}`,
      );
    }
    renderTournamentLaunch(receipt);
    return;
  }
  renderTournamentLaunch(null);
  const labels = {
    pending: "waiting for completed Home preflight",
    claimed: "ordinary New Battle ownership recorded",
    running: "checking Damage Slider and Ultimate Weapons",
    cleanup: "returning the owned validation battle to Home",
  };
  setText(
    "exclusiveValidationSummary",
    `Tournament validation: ${labels[receipt.status] || receipt.status || "unknown"}.`,
  );
}

function renderTournamentLaunch(receipt) {
  const launch = receipt?.outcome === "ready" ? receipt.launch : null;
  const waiting = launch?.status === "awaiting_operator";
  const reviewButton = byId("tournamentLaunchButton");
  reviewButton.hidden = !waiting;
  reviewButton.disabled = !waiting;
  const dialog = byId("tournamentLaunchDialog");
  if (!waiting) {
    if (dialog.open) dialog.close();
    return;
  }
  const policy = receipt.launch_policy || {};
  byId("tournamentLaunchTitle").textContent =
    policy.prompt_title || "Tournament validation passed";
  byId("tournamentLaunchMessage").textContent =
    policy.prompt_message || "Start the Tournament now?";
  byId("tournamentLaunchReminder").textContent =
    policy.reminder
    || "When the Tournament battle begins, set Target Priorities for the current Tournament Battle Conditions.";
  dialog.dataset.requestId = receipt.request_id;
  if (receipt.request_id === state.lastTournamentLaunchPrompted) return;
  state.lastTournamentLaunchPrompted = receipt.request_id;
  if (!dialog.open) dialog.showModal();
}

function openTournamentLaunch() {
  const dialog = byId("tournamentLaunchDialog");
  if (dialog.dataset.requestId && !dialog.open) dialog.showModal();
}

function resolveTournamentLaunch(decision) {
  const dialog = byId("tournamentLaunchDialog");
  const requestId = dialog.dataset.requestId;
  if (!requestId) return;
  dialog.close();
  sendControl(
    {
      action: "resolve_tournament_launch",
      request_id: requestId,
      decision,
    },
    decision === "start"
      ? "Tournament Start authorized"
      : "Automatic Tournament launch cancelled",
  );
}

function matchingRunSkips(control) {
  const context = control.startup_gate_context || { strategy: "none", checks: [] };
  const staged = control.startup_gate_waivers || {};
  return (context.checks || []).filter((check) => {
    const waiver = staged[check.id];
    return waiver && waiver.strategy === context.strategy;
  });
}

function renderRunConfiguration(control, processActive) {
  const context = control.startup_gate_context || { strategy: "none", checks: [] };
  const skips = matchingRunSkips(control);
  const canConfigure = !processActive || control.state === "PAUSED";
  byId("configureRunButton").disabled = !context.checks?.length || !canConfigure;
  byId("configureRunSummary").textContent = skips.length
    ? `Skip once: ${skips.map((check) => check.label).join(", ")}`
    : !canConfigure
      ? "Pause automation to configure one-run skips."
    : "Strategy defaults; no one-run skips staged.";
}

function openRunConfiguration() {
  const control = state.lastStatus?.control || {};
  const context = control.startup_gate_context || { strategy: "none", checks: [] };
  if (!context.checks?.length) return;
  const staged = control.startup_gate_waivers || {};
  byId("configureRunStrategy").textContent = `Strategy: ${context.strategy}`;
  const options = byId("configureRunOptions");
  options.replaceChildren();
  for (const check of context.checks) {
    const label = document.createElement("label");
    label.className = "gate-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "runSkip";
    checkbox.value = check.id;
    checkbox.checked = Boolean(
      staged[check.id] && staged[check.id].strategy === context.strategy
    );
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    const description = document.createElement("span");
    title.textContent = check.label;
    description.textContent = check.expected
      ? `Required by default: ${check.expected}`
      : "Uses the strategy's required value.";
    copy.append(title, description);
    label.append(checkbox, copy);
    options.append(label);
  }
  const dialog = byId("configureRunDialog");
  dialog.dataset.strategy = context.strategy;
  dialog.showModal();
}

function renderGateDecision(decision) {
  const dialog = byId("gateDialog");
  if (!decision || decision.status !== "pending") {
    if (dialog.open) dialog.close();
    return;
  }
  if (decision.request_id === state.lastGatePrompted) return;
  state.lastGatePrompted = decision.request_id;
  const advisory = decision.blocking === false;
  byId("gateEyebrow").textContent = advisory ? "Preflight warning" : "Startup gate";
  byId("gateTitle").textContent = advisory
    ? `${humanize(decision.check_id)} warning`
    : `${humanize(decision.check_id)} needs direction`;
  byId("gateDisposition").textContent = advisory
    ? "Closing leaves this warning pending; Tournament observation continues."
    : "Closing this dialog leaves automation blocked at this check.";
  byId("gateReason").textContent = decision.reason || "The requirement failed.";
  byId("gateExpected").textContent = decision.expected
    ? `Required: ${decision.expected}`
    : "";
  const choices = byId("gateOptions");
  choices.replaceChildren();
  for (const [index, option] of (decision.options || []).entries()) {
    const label = document.createElement("label");
    label.className = "gate-option";
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "gateDecision";
    radio.value = option.id;
    radio.checked = index === 0;
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    const description = document.createElement("span");
    title.textContent = option.label;
    description.textContent = option.description || "";
    copy.append(title, description);
    label.append(radio, copy);
    choices.append(label);
  }
  dialog.dataset.requestId = decision.request_id;
  if (!dialog.open) dialog.showModal();
}

function renderBattles(payload) {
  const tbody = byId("battleRows");
  tbody.replaceChildren();
  setBadge(byId("battleCount"), `${payload.total || 0} record${payload.total === 1 ? "" : "s"}`, payload.errors?.length ? "warn" : "neutral");
  if (!payload.items?.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 9;
    cell.className = "empty-state";
    cell.textContent = payload.errors?.length ? "Battle records could not be read." : "No completed battle records yet.";
    row.append(cell);
    tbody.append(row);
    return;
  }
  for (const battle of payload.items) {
    const row = document.createElement("tr");
    row.dataset.battleId = battle.battle_id;
    row.tabIndex = 0;
    row.title = "Open full battle record";
    const values = [
      formatDate(battle.captured_at),
      battle.battle_type_label || humanize(battle.battle_type || "unknown"),
      battle.strategy || battle.profile || dash,
      battle.tier ?? dash,
      battle.wave ?? dash,
      battle.real_time || dash,
      battle.coins_earned || dash,
      battle.cells_earned || dash,
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    const quality = document.createElement("td");
    quality.textContent = battle.quality?.valid ? "Valid" : "Review";
    quality.className = battle.quality?.valid ? "quality-good" : "quality-bad";
    row.append(quality);
    tbody.append(row);
  }
}

function renderActivity(payload) {
  const list = byId("activityList");
  list.replaceChildren();
  const items = payload.items || [];
  if (!items.length) {
    const item = document.createElement("li");
    item.className = "empty-state";
    item.textContent = "No recent activity is available.";
    list.append(item);
    return;
  }
  for (const entry of [...items].reverse()) {
    const item = document.createElement("li");
    const time = document.createElement("span");
    const level = document.createElement("span");
    const message = document.createElement("span");
    time.className = "activity-time";
    level.className = `activity-level ${entry.level}`;
    time.textContent = entry.timestamp;
    level.textContent = entry.level;
    message.textContent = entry.message;
    item.append(time, level, message);
    list.append(item);
  }
}

async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const [status, battles, activity] = await Promise.all([
      api("/api/v1/status"),
      api("/api/v1/battles?limit=30"),
      api("/api/v1/activity?limit=70&levels=ACTION,RESULT,WARN,ERROR,FAIL"),
    ]);
    renderStatus(status);
    renderBattles(battles);
    renderActivity(activity);
  } catch (error) {
    const connection = byId("connectionBadge");
    connection.className = "connection-badge is-error";
    setText("connectionText", error.message || "Connection failed");
  } finally {
    state.refreshing = false;
  }
}

async function sendControl(payload, successMessage) {
  setControlsBusy(true);
  try {
    const response = await api("/api/v1/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderStatus(response);
    toast(successMessage);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setControlsBusy(false);
  }
}

async function sendProcess(payload, successMessage) {
  setControlsBusy(true);
  try {
    const response = await api("/api/v1/process", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderStatus(response);
    toast(successMessage);
    await refresh();
  } catch (error) {
    toast(error.message, true);
    await refresh();
  } finally {
    setControlsBusy(false);
  }
}

function setControlsBusy(busy) {
  document.querySelectorAll("[data-control-action], [data-process-action], #applyModeButton, #customPauseForm button, #configureRunButton").forEach((button) => {
    button.disabled = busy;
  });
  if (!busy && state.lastStatus) renderStatus(state.lastStatus);
}

function createDetailCard(label, value) {
  const card = document.createElement("div");
  card.className = "detail-card";
  const name = document.createElement("span");
  const result = document.createElement("strong");
  name.textContent = label;
  result.textContent = value ?? dash;
  card.append(name, result);
  return card;
}

function rowsByKey(record) {
  const result = new Map();
  const stats = record.more_stats || record.detailed_stats;
  for (const section of stats?.sections || []) {
    for (const row of section.rows || []) result.set(`${section.key}.${row.key}`, row);
  }
  return result;
}

async function openBattle(battleId) {
  const dialog = byId("battleDialog");
  const body = byId("battleDialogBody");
  setText("battleDialogTitle", battleId);
  body.replaceChildren(createDetailCard("Loading", "Battle record…"));
  dialog.showModal();
  try {
    const record = await api(`/api/v1/battles/${encodeURIComponent(battleId)}`);
    renderBattleDetail(record);
  } catch (error) {
    body.replaceChildren(createDetailCard("Unable to load", error.message));
  }
}

function renderBattleDetail(record) {
  const body = byId("battleDialogBody");
  body.replaceChildren();
  const rows = rowsByKey(record);
  const value = (key) => rows.get(key)?.value_raw || dash;
  const cards = document.createElement("div");
  cards.className = "detail-card-grid";
  [
    ["Captured", formatDate(record.captured_at)],
    ["Type", record.battle_type_analysis?.label || humanize(record.battle_type || "unknown")],
    ["Strategy", record.strategy || record.run_configuration?.profile],
    ["Game speed mode", record.runtime?.game_speed_control?.mode],
    ["Tier", value("battle_report.tier")],
    ["Wave", value("battle_report.wave")],
    ["Real time", value("battle_report.real_time")],
    ["Coins", value("battle_report.coins_earned")],
    ["Coins/hour", value("battle_report.coins_per_hour")],
    ["Cells", value("battle_report.cells_earned")],
    ["Cells/hour", value("battle_report.cells_per_hour")],
    ["Capture", record.quality?.valid ? "Valid" : "Review needed"],
  ].forEach(([label, cardValue]) => cards.append(createDetailCard(label, cardValue)));
  body.append(cards);

  if (record.quality?.warnings?.length) {
    const warnings = document.createElement("ul");
    warnings.className = "warning-list";
    for (const warning of record.quality.warnings) {
      const item = document.createElement("li");
      item.textContent = warning;
      warnings.append(item);
    }
    body.append(warnings);
  }

  appendPerksSection(body, record.perks);
  appendStructuredSection(body, "Battle type analysis", record.battle_type_analysis, true);
  appendStructuredSection(body, "Run configuration", record.run_configuration, true);
  appendStructuredSection(
    body,
    "Verified preflight evidence",
    record.runtime?.session_preflight_evidence,
    false,
  );

  const derivedEntries = Object.entries(record.derived || {}).filter(([, entryValue]) => typeof entryValue !== "object");
  if (derivedEntries.length) {
    const details = document.createElement("details");
    details.className = "record-section";
    details.open = true;
    const summary = document.createElement("summary");
    summary.textContent = "Derived performance";
    const table = makeKeyValueTable(derivedEntries.map(([key, entryValue]) => [humanize(key), String(entryValue)]));
    details.append(summary, table);
    body.append(details);
  }

  const stats = record.more_stats || record.detailed_stats;
  for (const section of stats?.sections || []) {
    const details = document.createElement("details");
    details.className = "record-section";
    const summary = document.createElement("summary");
    summary.textContent = section.name || humanize(section.key);
    const pairs = (section.rows || []).map((row) => [row.label || humanize(row.key), row.value_raw ?? dash]);
    details.append(summary, makeKeyValueTable(pairs));
    body.append(details);
  }
}

function appendPerksSection(container, perks) {
  if (!perks) return;
  const details = document.createElement("details");
  details.className = "record-section";
  details.open = true;
  const summary = document.createElement("summary");
  const selected = perks.selected || [];
  summary.textContent = `Selected perks (${selected.length})`;
  const pairs = selected.map((perk) => [
    `#${perk.latest_selection_rank ?? dash} · ${perk.color || "unknown"}`,
    `${perk.display_text || dash}${perk.confidence != null ? ` (${Number(perk.confidence).toFixed(1)}%)` : ""}`,
  ]);
  if (!pairs.length) {
    pairs.push(["Capture", perks.quality?.source_reason || "No perks recognized"]);
  }
  details.append(summary, makeKeyValueTable(pairs));
  container.append(details);
}

function appendStructuredSection(container, title, value, open) {
  if (!value || typeof value !== "object" || !Object.keys(value).length) return;
  const pairs = [];
  flattenObject(value, "", pairs);
  if (!pairs.length) return;
  const details = document.createElement("details");
  details.className = "record-section";
  details.open = open;
  const summary = document.createElement("summary");
  summary.textContent = title;
  details.append(summary, makeKeyValueTable(pairs));
  container.append(details);
}

function flattenObject(value, prefix, pairs) {
  for (const [key, child] of Object.entries(value || {})) {
    if (["schema_version", "profile_version", "raw_text"].includes(key)) continue;
    const label = prefix ? `${prefix} / ${humanize(key)}` : humanize(key);
    if (Array.isArray(child)) {
      pairs.push([label, child.map((item) => typeof item === "object" ? JSON.stringify(item) : String(item)).join(" → ") || dash]);
    } else if (child && typeof child === "object") {
      flattenObject(child, label, pairs);
    } else {
      pairs.push([label, typeof child === "boolean" ? (child ? "Yes" : "No") : String(child ?? dash)]);
    }
  }
}

function makeKeyValueTable(pairs) {
  const table = document.createElement("table");
  const tbody = document.createElement("tbody");
  for (const [key, value] of pairs) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    const result = document.createElement("td");
    name.textContent = key;
    result.textContent = value;
    row.append(name, result);
    tbody.append(row);
  }
  table.append(tbody);
  return table;
}

function humanize(value) {
  return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function showAuthDialog() {
  const dialog = byId("authDialog");
  byId("tokenInput").value = state.token;
  if (!dialog.open) dialog.showModal();
}

let toastTimer;
function toast(message, isError = false) {
  const element = byId("toast");
  element.textContent = message;
  element.className = `toast${isError ? " error" : ""}`;
  element.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element.hidden = true; }, 4500);
}

document.addEventListener("click", (event) => {
  const process = event.target.closest("[data-process-action]");
  if (process) {
    const action = process.dataset.processAction;
    if (action === "stop" && !window.confirm("Persist STOPPED and stop the managed Linux automation service?")) return;
    if (
      action === "start"
      && Number(state.lastStatus?.control?.game_speed_target) < 6.3
      && !window.confirm(
        `A custom game-speed target is active. Start automation with speed held at x${Number(
          state.lastStatus.control.game_speed_target,
        ).toFixed(1)}?`,
      )
    ) return;
    if (action === "restart_attached" && !window.confirm(
      "Reload the main Python automation process for this battle? Automation will pause, verify the attached replacement, and restore the current control state.",
    )) return;
    const payload = { action };
    if (process.dataset.runState) payload.run_state = process.dataset.runState;
    sendProcess(
      payload,
      action === "stop"
        ? "Automation service stopped"
        : action === "restart_attached"
          ? "Automation reloaded for the current battle"
        : `Automation service started ${payload.run_state.toLowerCase()}`,
    );
    return;
  }
  const control = event.target.closest("[data-control-action]");
  if (control) {
    const action = control.dataset.controlAction;
    if (action === "stop" && !window.confirm("Persist STOPPED for the automation runtime?")) return;
    const payload = { action };
    if (control.dataset.minutes) payload.minutes = Number(control.dataset.minutes);
    if (control.dataset.mode) payload.mode = control.dataset.mode;
    const message = action === "pause"
      ? "Pause directive saved"
      : `${humanize(action)} directive saved`;
    sendControl(payload, message);
    return;
  }
  const close = event.target.closest("[data-close-dialog]");
  if (close) byId(close.dataset.closeDialog).close();
});

byId("customPauseForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const minutes = Number(byId("customPauseMinutes").value);
  sendControl({ action: "pause", minutes }, `Paused for ${minutes} minutes`);
});

byId("applyModeButton").addEventListener("click", () => {
  const mode = byId("modeSelect").value;
  sendControl({ action: "mode", mode }, `Mode set to ${mode}`);
});

byId("gameSpeedTargetSelect").addEventListener("change", () => {
  const target = Number(byId("gameSpeedTargetSelect").value);
  sendControl(
    { action: "game_speed", target },
    `Game speed target set to x${target.toFixed(1)}`,
  );
});

byId("battleRows").addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-battle-id]");
  if (row) openBattle(row.dataset.battleId);
});

byId("battleRows").addEventListener("keydown", (event) => {
  const row = event.target.closest("tr[data-battle-id]");
  if (row && (event.key === "Enter" || event.key === " ")) {
    event.preventDefault();
    openBattle(row.dataset.battleId);
  }
});

byId("authButton").addEventListener("click", showAuthDialog);
byId("configureRunButton").addEventListener("click", openRunConfiguration);
byId("tournamentLaunchButton").addEventListener("click", openTournamentLaunch);
byId("startTournamentLaunchButton").addEventListener(
  "click",
  () => resolveTournamentLaunch("start"),
);
byId("cancelTournamentLaunchButton").addEventListener(
  "click",
  () => resolveTournamentLaunch("cancel"),
);
byId("authForm").addEventListener("submit", (event) => {
  event.preventDefault();
  state.token = byId("tokenInput").value.trim();
  if (state.token) sessionStorage.setItem("thetowerControlToken", state.token);
  else sessionStorage.removeItem("thetowerControlToken");
  byId("authDialog").close();
  refresh();
});
byId("gateForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const dialog = byId("gateDialog");
  const selected = dialog.querySelector('input[name="gateDecision"]:checked');
  if (!selected) return;
  const requestId = dialog.dataset.requestId;
  dialog.close();
  sendControl(
    { action: "resolve_gate", request_id: requestId, decision_id: selected.value },
    `Preflight decision resolved with ${humanize(selected.value)}`,
  );
});
byId("configureRunForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const dialog = byId("configureRunDialog");
  const skipChecks = Array.from(
    dialog.querySelectorAll('input[name="runSkip"]:checked'),
    (input) => input.value,
  );
  dialog.close();
  sendControl(
    { action: "configure_run", skip_checks: skipChecks },
    skipChecks.length
      ? `Configured ${skipChecks.length} one-run skip${skipChecks.length === 1 ? "" : "s"}`
      : "Run restored to strategy defaults",
  );
});
byId("refreshButton").addEventListener("click", refresh);

refresh();
state.timer = window.setInterval(refresh, 5000);
