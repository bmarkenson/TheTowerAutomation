"use strict";

const state = {
  token: sessionStorage.getItem("thetowerControlToken") || "",
  refreshing: false,
  timer: null,
  lastStatus: null,
  lastGatePrompted: null,
  lastTournamentLaunchPrompted: null,
  captureCatalog: null,
  captureCatalogLoading: false,
  captureReview: null,
  captureReviewInput: null,
  saveMappingCatalog: null,
  saveMappingReview: null,
  saveMappingResult: null,
  saveMappingBusy: false,
  saveMappingSelectionGeneration: 0,
};

const byId = (id) => document.getElementById(id);
const dash = "—";
const BETTER_CONTROL_MINIMUM_REVISION = 30;
const BETTER_CONTROL_CAPABILITY = "better_control_model_v2";
const SETUP_CAPTURE_CAPABILITY = "save_backed_setup_capture_v2";
const clientModel = globalThis.TheTowerControlClientModel;

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
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.code = payload.code || "";
    error.details = payload.details || null;
    error.status = response.status;
    throw error;
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
  const controlModel = payload.control_model || {};
  const directive = control.state || "UNKNOWN";

  renderConfirmedLocalMapping(payload.confirmed_local_mappings);

  setText("directiveState", directive);
  byId("directiveState").className = `state-pill ${directive.toLowerCase()}`;
  let directiveDetail = control.updated_at ? `Updated ${formatDate(control.updated_at)}` : "No persisted update time";
  if (directive === "PAUSED") directiveDetail = `Paused ${formatRemaining(control.remaining_seconds)}`;
  if (control.error) directiveDetail = control.error;
  setText("directiveDetail", directiveDetail);
  setText("currentMode", formatTerminalPolicy(control.mode));
  setText("controlUpdated", formatDate(control.updated_at));
  byId("modeSelect").value = ["NEXT_BATTLE", "WAIT", "HOME"].includes(control.mode)
    ? control.mode
    : "NEXT_BATTLE";
  const gameSpeedTarget = Number(control.game_speed_target);
  const normalizedGameSpeedTarget = Number.isFinite(gameSpeedTarget)
    ? gameSpeedTarget
    : 6.3;
  const rawObservedGameSpeed = observation?.game_speed;
  const observedGameSpeed = Number(rawObservedGameSpeed);
  const hasObservedGameSpeed =
    rawObservedGameSpeed != null && Number.isFinite(observedGameSpeed);
  byId("gameSpeedTargetSelect").value = normalizedGameSpeedTarget.toFixed(1);
  const gameSpeedWarning = byId("gameSpeedTargetWarning");
  gameSpeedWarning.hidden = normalizedGameSpeedTarget >= 6.3;
  gameSpeedWarning.textContent = normalizedGameSpeedTarget < 6.3
    ? hasObservedGameSpeed
      ? `Target x${normalizedGameSpeedTarget.toFixed(1)} · observed x${observedGameSpeed.toFixed(1)}.`
      : `Target x${normalizedGameSpeedTarget.toFixed(1)}; awaiting a status-frame observation.`
    : "";
  setText(
    "gameSpeedObserved",
    hasObservedGameSpeed
      ? `The latest status frame read x${observedGameSpeed.toFixed(1)}. The selected target is enforced during Running.`
      : "Waiting for an observed speed from the next Running status frame.",
  );

  if (observation) {
    setText(
      "observedState",
      controlModel.observation?.available
        ? humanize(controlModel.observation.game_state)
        : observation.state_label,
    );
    setText("observedWave", observation.wave);
    setText("observedCoins", observation.coins_per_minute);
    setText(
      "observedSpeed",
      hasObservedGameSpeed ? `x${observedGameSpeed.toFixed(1)}` : dash,
    );
    setText("lastObserved", `${formatDate(observation.observed_at)} · ${formatAge(observation.age_seconds)}`);
    setText("observedMenu", observation.menu);
    setText("observedSecondary", observation.secondary?.join(", ") || dash);
    setText("observedOverlays", observation.overlays?.join(", ") || dash);
    setText("priorTransition", formatPriorTransition(payload.prior_transition));
    setBadge(byId("heartbeatBadge"), observation.stale ? "Stale" : "Fresh", observation.stale ? "warn" : "good");
    byId("staleWarning").hidden = !observation.stale;
  } else {
    ["observedState", "observedWave", "observedCoins", "observedSpeed", "lastObserved", "observedMenu", "observedSecondary", "observedOverlays", "priorTransition"].forEach((id) => setText(id, dash));
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
  const betterControlCompatible =
    payload.api_version === 1
    && Number(payload.server_revision) >= BETTER_CONTROL_MINIMUM_REVISION
    && (payload.capabilities || []).includes(BETTER_CONTROL_CAPABILITY);
  const setupCaptureCompatible = betterControlCompatible
    && (payload.capabilities || []).includes(SETUP_CAPTURE_CAPABILITY);
  const processActive = Boolean(runtime.active || processService?.active);
  document.querySelectorAll("[data-process-action]").forEach((button) => {
    const action = button.dataset.processAction;
    const unavailable = Boolean(control.error) || !processService?.available;
    button.disabled = unavailable
      || (action === "start" && !betterControlCompatible)
      || (action === "start" && processActive)
      || (action === "stop" && !processService?.active);
    if (action === "start" && !betterControlCompatible) {
      button.title = "Linux API revision 30 with better_control_model_v2 is required.";
    }
  });
  renderBetterControlModel(
    controlModel,
    betterControlCompatible,
    setupCaptureCompatible,
    control.error || "",
  );
  renderRunConfiguration(
    control,
    processActive,
  );
  renderExclusiveValidation(control);
  renderGateDecision(control.gate_decision);
}

function renderConfirmedLocalMapping(status) {
  const presentation = clientModel.confirmedLocalMappingPresentation(status);
  const alert = byId("confirmedLocalMappingAlert");
  alert.hidden = !presentation.visible;
  alert.className = `persistent-alert ${presentation.severity}`;
  const signature = JSON.stringify([
    presentation.visible,
    presentation.severity,
    presentation.title,
    presentation.detail,
  ]);
  if (alert.dataset.presentationSignature !== signature) {
    alert.dataset.presentationSignature = signature;
    setText("confirmedLocalMappingTitle", presentation.title);
    setText("confirmedLocalMappingDetail", presentation.detail);
  }
  const reviewButton = byId("reviewSaveMappingsButton");
  const compatible = clientModel.saveMappingIntegrationCompatible(
    state.lastStatus,
  );
  reviewButton.hidden = !presentation.visible;
  reviewButton.disabled = !compatible;
  reviewButton.title = compatible
    ? "Review exact canonical proposals and prepare one in a selected feature worktree."
    : "Linux API revision 35 with save_mapping_integration_v1 is required.";
}

function saveMappingCandidate() {
  const recordId = byId("saveMappingCandidateSelect").value;
  return (state.saveMappingCatalog?.items || []).find(
    (item) => item.record_id === recordId,
  ) || null;
}

function saveMappingWorkspace() {
  const workspaceId = byId("saveMappingWorkspaceSelect").value;
  return (state.saveMappingCatalog?.workspaces || []).find(
    (item) => item.workspace_id === workspaceId,
  ) || null;
}

function saveMappingSelection() {
  return {
    candidateRecordId: byId("saveMappingCandidateSelect").value,
    workspaceId: byId("saveMappingWorkspaceSelect").value,
    generation: state.saveMappingSelectionGeneration,
  };
}

function saveMappingSelectionStillCurrent(selection) {
  const current = saveMappingSelection();
  return current.generation === selection.generation
    && current.candidateRecordId === selection.candidateRecordId
    && current.workspaceId === selection.workspaceId;
}

function updateSaveMappingControls() {
  const item = saveMappingCandidate();
  const workspace = saveMappingWorkspace();
  const reviewButton = byId("reviewSaveMappingProposalButton");
  const prepareButton = byId("prepareSaveMappingButton");
  reviewButton.disabled = state.saveMappingBusy
    || !item
    || !workspace
    || item.review_available !== true;
  const availability = clientModel.saveMappingPrepareAvailability(
    state.saveMappingReview,
    byId("saveMappingCandidateSelect").value,
    byId("saveMappingWorkspaceSelect").value,
  );
  prepareButton.disabled = state.saveMappingBusy
    || state.saveMappingResult != null
    || !availability.available;
}

function setSaveMappingBusy(busy) {
  state.saveMappingBusy = busy;
  byId("saveMappingCandidateSelect").disabled = busy;
  byId("saveMappingWorkspaceSelect").disabled = busy;
  byId("refreshSaveMappingCatalogButton").disabled = busy;
  document.querySelectorAll(
    '[data-close-dialog="saveMappingIntegrationDialog"]',
  ).forEach((button) => {
    button.disabled = busy;
  });
  updateSaveMappingControls();
}

function clearSaveMappingReview(message = "Review an exact proposal before preparation.") {
  state.saveMappingReview = null;
  state.saveMappingResult = null;
  byId("saveMappingProposal").replaceChildren();
  byId("saveMappingProposal").className = "mapping-proposal muted";
  byId("saveMappingProposal").textContent = message;
  byId("saveMappingResult").hidden = true;
  byId("saveMappingResult").replaceChildren();
  byId("prepareSaveMappingButton").disabled = true;
}

function saveMappingCandidateLabel(item) {
  const scope = item?.scope?.slot_key ? ` · ${item.scope.slot_key}` : "";
  const value = item?.raw_value == null
    ? ""
    : ` · ${item.raw_value} → ${item.semantic_value || "unknown"}`;
  return `${humanize(item?.check_id || "save mapping")}${scope}${value}`;
}

function renderSaveMappingCatalog(catalog) {
  state.saveMappingSelectionGeneration += 1;
  state.saveMappingCatalog = catalog;
  const candidateSelect = byId("saveMappingCandidateSelect");
  const workspaceSelect = byId("saveMappingWorkspaceSelect");
  candidateSelect.replaceChildren(new Option("Select a mapping observation…", ""));
  workspaceSelect.replaceChildren(new Option("Select an owned feature worktree…", ""));
  for (const item of catalog.items || []) {
    const option = new Option(saveMappingCandidateLabel(item), item.record_id);
    option.title = item.reason || "";
    candidateSelect.append(option);
  }
  for (const workspace of catalog.workspaces || []) {
    const suffix = workspace.available
      ? "ready"
      : humanize(workspace.code || "unavailable");
    const option = new Option(
      `${workspace.branch} · ${workspace.head_commit?.slice(0, 12) || "unknown"} · ${suffix}`,
      workspace.workspace_id,
    );
    option.title = `${workspace.path_display}\n${workspace.reason || "Clean linked feature worktree"}`;
    workspaceSelect.append(option);
  }
  const availableWorkspaces = (catalog.workspaces || []).filter(
    (workspace) => workspace.available,
  );
  if (availableWorkspaces.length === 1) {
    workspaceSelect.value = availableWorkspaces[0].workspace_id;
  }
  setText(
    "saveMappingCatalogStatus",
    catalog.available === false
      ? catalog.reason || "Save-mapping integration catalog is unavailable."
      : `${(catalog.items || []).length} observation(s) · ${(catalog.workspaces || []).length} linked feature worktree(s)`,
  );
  renderSaveMappingSelection();
}

function renderSaveMappingSelection() {
  const item = saveMappingCandidate();
  const workspace = saveMappingWorkspace();
  clearSaveMappingReview();
  setText(
    "saveMappingCandidateDetail",
    item
      ? `${item.mapping_id} · ${item.state} · ${item.reason || "Review pending"}`
      : "Choose one durable observation.",
  );
  setText(
    "saveMappingWorkspaceDetail",
    workspace
      ? `${workspace.path_display}\n${workspace.available ? "Eligible for review and preparation." : workspace.reason || "Unavailable."}`
      : "Choose the feature worktree owned by this outcome.",
  );
  const reviewButton = byId("reviewSaveMappingProposalButton");
  reviewButton.title = item?.review_available === false
    ? item.review_reason || "This observation is not reviewable."
    : "Review the exact server-generated proposal.";
  updateSaveMappingControls();
}

function mappingProposalTargets(proposal) {
  if (proposal?.schema_version === 2) return proposal.targets || [];
  return proposal?.target
    ? [{ ...proposal.target, operations: proposal.operations || [] }]
    : [];
}

function mappingProposalRow(title, detail) {
  const row = document.createElement("div");
  row.className = "mapping-proposal-row";
  const heading = document.createElement("strong");
  const body = document.createElement("pre");
  heading.textContent = title;
  body.textContent = detail;
  row.append(heading, body);
  return row;
}

function renderSaveMappingReview(review) {
  const container = byId("saveMappingProposal");
  container.replaceChildren();
  container.className = "mapping-proposal";
  container.append(mappingProposalRow(
    "Reviewed proposal fingerprint",
    review.reviewed_proposal_fingerprint,
  ));
  container.append(mappingProposalRow(
    "Repository snapshot",
    `main ${review.repository?.main_commit}\ndevelop ${review.repository?.develop_commit}\nfeature ${review.workspace?.head_commit}`,
  ));
  for (const target of mappingProposalTargets(review.proposal)) {
    const operations = (target.operations || []).map(
      (operation) => `${operation.op} ${operation.path}\n${JSON.stringify(operation.value, null, 2)}`,
    ).join("\n\n") || "Already present; no operation for this target.";
    container.append(mappingProposalRow(
      `${target.mapping_id} · ${target.path}`,
      `base ${target.expected_sha256}\nstate ${target.state || "pending"}\n${operations}`,
    ));
  }
  const availability = clientModel.saveMappingPrepareAvailability(
    review,
    byId("saveMappingCandidateSelect").value,
    byId("saveMappingWorkspaceSelect").value,
  );
  setText(
    "saveMappingPrepareStatus",
    availability.available
      ? "Ready to prepare tracked JSON in the selected feature worktree."
      : availability.reason || "Preparation is unavailable.",
  );
  if (review.recovery_required === true) {
    const result = byId("saveMappingResult");
    result.replaceChildren();
    result.hidden = false;
    result.className = "callout warning";
    const title = document.createElement("strong");
    const detail = document.createElement("p");
    title.textContent = "Interrupted preparation requires recovery";
    detail.textContent = availability.reason
      || "Inspect the selected feature worktree before another action.";
    result.append(title, detail);
  } else if (review.prepared === true && review.prepared_result == null) {
    renderSaveMappingFailure(
      {
        code: "prepared_result_invalid",
        message: "The server reported prepared state without its exact result.",
      },
      true,
    );
  } else if (review.prepared_result != null) {
    state.saveMappingResult = review.prepared_result;
    const presentation = renderSaveMappingResult(
      review.prepared_result,
      review.candidate_record_id,
      review.workspace?.workspace_id,
      review.reviewed_proposal_fingerprint,
    );
    setText(
      "saveMappingPrepareStatus",
      presentation.success
        ? "Already prepared — validation, commit, and promotion remain required."
        : presentation.detail,
    );
  }
  updateSaveMappingControls();
}

function renderSaveMappingResult(
  result,
  candidateRecordId,
  workspaceId,
  reviewedProposalFingerprint,
) {
  const presentation = clientModel.saveMappingPreparedPresentation(
    result,
    candidateRecordId,
    workspaceId,
    reviewedProposalFingerprint,
  );
  const container = byId("saveMappingResult");
  container.replaceChildren();
  container.hidden = false;
  container.className = `callout ${presentation.success ? "success" : "warning"}`;
  const title = document.createElement("strong");
  const detail = document.createElement("p");
  title.textContent = presentation.title;
  detail.textContent = presentation.detail;
  container.append(title, detail);
  if (presentation.success) {
    container.append(mappingProposalRow(
      "Lifecycle state",
      `committed: ${String(result.committed)}\npromoted: ${String(result.promoted)}\nvalidation: ${result.validation_status}`,
    ));
    for (const target of result.targets) {
      container.append(mappingProposalRow(
        `${target.mapping_id} · ${target.path}`,
        `${target.before_sha256}\n→ ${target.after_sha256}`,
      ));
    }
    if (result.validation.length) {
      container.append(mappingProposalRow(
        "Validation still required",
        result.validation.join("\n"),
      ));
    }
    if (result.warning) {
      container.append(mappingProposalRow("Audit warning", result.warning));
    }
  }
  return presentation;
}

function renderSaveMappingFailure(error, prepareRequest) {
  const presentation = clientModel.saveMappingFailurePresentation(
    error,
    prepareRequest,
  );
  const container = byId("saveMappingResult");
  container.replaceChildren();
  container.hidden = false;
  container.className = `callout ${presentation.uncertain ? "warning" : "info"}`;
  const title = document.createElement("strong");
  const detail = document.createElement("p");
  title.textContent = presentation.title;
  detail.textContent = presentation.detail;
  container.append(title, detail);
  return presentation;
}

async function loadSaveMappingIntegrationCatalog() {
  if (state.saveMappingBusy) return;
  setSaveMappingBusy(true);
  clearSaveMappingReview("Loading save-mapping integration catalog…");
  try {
    const catalog = await api("/api/v1/save-mapping-integration");
    if (
      catalog?.schema_version !== 1
      || catalog?.capability !== "save_mapping_integration_v1"
    ) {
      const error = new Error(
        "The server returned an incompatible save-mapping catalog.",
      );
      error.code = "catalog_contract_invalid";
      throw error;
    }
    renderSaveMappingCatalog(catalog);
  } catch (error) {
    setText("saveMappingCatalogStatus", error.message);
    clearSaveMappingReview("The catalog could not be loaded.");
    renderSaveMappingFailure(error, false);
    toast(error.message, true);
  } finally {
    setSaveMappingBusy(false);
  }
}

async function openSaveMappingIntegration() {
  const dialog = byId("saveMappingIntegrationDialog");
  if (!dialog.open) dialog.showModal();
  await loadSaveMappingIntegrationCatalog();
}

async function reviewSaveMappingProposal() {
  if (state.saveMappingBusy) return;
  const selection = saveMappingSelection();
  const { candidateRecordId, workspaceId } = selection;
  if (!candidateRecordId || !workspaceId) return;
  setSaveMappingBusy(true);
  clearSaveMappingReview("Reviewing exact proposal…");
  try {
    const review = await api("/api/v1/save-mapping-integration", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        operation: "review",
        candidate_record_id: candidateRecordId,
        workspace_id: workspaceId,
      }),
    });
    if (
      !saveMappingSelectionStillCurrent(selection)
      || !clientModel.saveMappingReviewIsCurrent(
        review,
        candidateRecordId,
        workspaceId,
      )
    ) {
      const error = new Error(
        "The returned review does not match the current exact selection.",
      );
      error.code = "review_contract_invalid";
      throw error;
    }
    state.saveMappingReview = review;
    renderSaveMappingReview(review);
  } catch (error) {
    clearSaveMappingReview("Review unavailable. Refresh and review again.");
    const presentation = renderSaveMappingFailure(error, false);
    setText("saveMappingPrepareStatus", presentation.detail);
    toast(error.message, true);
  } finally {
    setSaveMappingBusy(false);
  }
}

async function prepareSaveMappingProposal() {
  if (state.saveMappingBusy) return;
  const review = state.saveMappingReview;
  const selection = saveMappingSelection();
  const { candidateRecordId, workspaceId } = selection;
  const availability = clientModel.saveMappingPrepareAvailability(
    review,
    candidateRecordId,
    workspaceId,
  );
  if (!availability.available) return;
  const targets = mappingProposalTargets(review.proposal);
  const confirmation = [
    `Prepare this exact proposal in ${review.workspace.branch}?`,
    review.workspace.path_display,
    `Fingerprint: ${review.reviewed_proposal_fingerprint}`,
    `Targets: ${targets.length}`,
    "",
    "This makes tracked JSON dirty in the feature worktree. It does not test, commit, merge, promote, restart anything, send device input, or change the current battle.",
  ].join("\n");
  if (!window.confirm(confirmation)) return;
  setSaveMappingBusy(true);
  try {
    const result = await api("/api/v1/save-mapping-integration", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        operation: "prepare",
        candidate_record_id: candidateRecordId,
        workspace_id: workspaceId,
        reviewed_proposal_fingerprint: review.reviewed_proposal_fingerprint,
      }),
    });
    if (!saveMappingSelectionStillCurrent(selection)) {
      const error = new Error(
        "The GUI selection changed while preparation was in flight.",
      );
      error.code = "selection_changed_during_prepare";
      throw error;
    }
    const validation = clientModel.saveMappingPreparedResultValidation(
      result,
      candidateRecordId,
      workspaceId,
      review.reviewed_proposal_fingerprint,
    );
    if (!validation.valid) {
      const error = new Error(validation.reason);
      error.code = validation.code;
      throw error;
    }
    state.saveMappingResult = result;
    const presentation = renderSaveMappingResult(
      result,
      candidateRecordId,
      workspaceId,
      review.reviewed_proposal_fingerprint,
    );
    setText(
      "saveMappingPrepareStatus",
      presentation.detail,
    );
    toast("Canonical mapping prepared in the feature worktree; it is not promoted");
    if (result.warning) toast(result.warning, true);
    await refresh();
  } catch (error) {
    clearSaveMappingReview(
      "The reviewed proposal was invalidated. Refresh and review again.",
    );
    const presentation = renderSaveMappingFailure(error, true);
    setText("saveMappingPrepareStatus", presentation.detail);
    toast(error.message, true);
  } finally {
    setSaveMappingBusy(false);
  }
}

function renderBetterControlModel(model, compatible, captureCompatible, controlError) {
  const actions = model.actions || {};
  const pause = actions.pause || {};
  document.querySelectorAll('[data-control-action="pause"], #customPauseForm button').forEach((button) => {
    button.disabled = !compatible || pause.available !== true;
    button.title = compatible
      ? pause.reason || ""
      : "Linux API revision 30 with better_control_model_v2 is required.";
  });
  for (const [id, action] of [
    ["startBattleButton", "start_battle"],
    ["attachBattleButton", "attach_battle"],
    ["takeManualControlButton", "take_manual_control"],
    ["returnControlButton", "return_control"],
  ]) {
    const button = byId(id);
    const availability = actions[action] || {};
    button.disabled = !compatible || availability.available !== true;
    button.title = compatible
      ? availability.reason || ""
      : "Linux API revision 30 with better_control_model_v2 is required.";
  }
  const enable = document.querySelector('[data-control-action="enable"]');
  if (enable) {
    enable.disabled = !compatible || actions.enable?.available !== true;
    enable.title = compatible
      ? actions.enable?.reason || ""
      : "Linux API revision 30 with better_control_model_v2 is required.";
  }
  const applyTerminalPolicy = byId("applyModeButton");
  applyTerminalPolicy.disabled = !compatible || Boolean(controlError);
  applyTerminalPolicy.title = controlError
    || (compatible
      ? "Set future terminal behavior without dispatching an immediate battle action."
      : "Linux API revision 30 with better_control_model_v2 is required.");
  const workflow = model.battle_workflow;
  const workflowStatus = clientModel.workflowPresentation(workflow?.status);
  setText(
    "battleWorkflowSummary",
    workflow
      ? `${humanize(workflow.intent)} · ${workflowStatus.label}${workflow.reason ? ` — ${workflow.reason}` : ""}`
      : `${humanize(model.observation?.game_state || "unknown")} — choose only an available matching intent.`,
  );
  const manual = model.manual_control;
  setText(
    "manualControlSummary",
    manual
      ? `${humanize(manual.status)}${manual.detail ? ` — ${manual.detail}` : ""}`
        + (manual.surrender_collection
          ? ` · manual Surrender collection: ${humanize(manual.surrender_collection)}`
          : "")
      : "Automation retains control.",
  );
  const terminalPolicy = model.when_battle_ends || {};
  setText(
    "terminalPolicyStatus",
    terminalPolicy.reason
      ? `${humanize(terminalPolicy.status || "selected")} — ${terminalPolicy.reason}`
      : "Future terminal policy status is unavailable.",
  );
  renderSetupCapture(
    model.setup_capture,
    actions.capture_current_setup || {},
    captureCompatible,
  );
}

function renderSetupCapture(capture, availability, compatible) {
  const button = byId("captureSetupButton");
  const status = capture?.status || "";
  const reviewable = status === "ready";
  const inProgress = ["requested", "acknowledged", "capturing"].includes(status);
  const openAction = clientModel.setupCaptureOpenAction(capture, availability);
  button.textContent = reviewable
    ? "Review captured setup…"
    : inProgress
      ? "Capturing current setup…"
      : openAction === "inspect"
        ? "View capture result…"
      : "Capture current setup as…";
  button.disabled = !compatible || openAction === "unavailable";
  button.title = compatible
    ? reviewable
      ? "Review the fresh save-backed capture and save a new inactive artifact."
      : availability.reason || ""
    : "Linux API revision 30 with save_backed_setup_capture_v2 is required.";

  const labels = {
    requested: "Capture requested; waiting for the exact runtime owner.",
    acknowledged: "Runtime acknowledged the capture request.",
    capturing: "Requesting and restoring a newly serialized save.",
    ready: "Fresh save capture is ready for review.",
    saved: "Capture saved as a new inactive artifact.",
    cancelled: "Capture review was cancelled.",
    unavailable: "Capture could not obtain fresh evidence.",
    interrupted: "Capture was interrupted without consuming cached evidence.",
    failed: "Capture failed without changing runtime selection.",
  };
  setText(
    "captureSetupSummary",
    capture
      ? `${labels[status] || humanize(status)}${capture.reason ? ` — ${capture.reason}` : ""}`
      : availability.reason || "No setup capture is active.",
  );
  const dialog = byId("captureSetupDialog");
  if (dialog.open) renderCaptureDialog(capture, state.captureCatalog);
  if (
    reviewable
    && !clientModel.captureCatalogMatches(
      capture,
      state.captureCatalog?.capture,
    )
    && !state.captureCatalogLoading
  ) {
    loadSetupCaptureCatalog().catch((error) => toast(error.message, true));
  }
}

function captureFromState() {
  return clientModel.chooseLatestCapture(
    state.lastStatus?.control_model?.setup_capture,
    state.captureCatalog?.capture,
  );
}

function captureValue(value) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function captureRow(titleText, detailText) {
  const row = document.createElement("div");
  row.className = "capture-row";
  const title = document.createElement("strong");
  const detail = document.createElement("span");
  title.textContent = titleText;
  detail.textContent = detailText;
  row.append(title, detail);
  return row;
}

function populateCaptureList(container, rows, emptyText) {
  container.replaceChildren();
  if (!rows.length) {
    container.append(captureRow("None", emptyText));
    return;
  }
  rows.forEach((row) => container.append(row));
}

function populateCaptureBases(catalog) {
  const select = byId("captureBase");
  const selected = select.value;
  select.replaceChildren();
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "No Base (compare with an empty draft)";
  select.append(none);
  for (const base of catalog?.bases?.items || []) {
    for (const revision of base.revisions || []) {
      const option = document.createElement("option");
      option.value = `${base.id}@${revision.revision}`;
      option.textContent = `${base.display_name || base.id} · revision ${revision.revision}`;
      select.append(option);
    }
  }
  if (Array.from(select.options).some((option) => option.value === selected)) {
    select.value = selected;
  }
}

function renderCaptureDialog(capture, catalog) {
  const preview = capture?.preview;
  setText("captureStatus", capture?.status ? humanize(capture.status) : dash);
  setText(
    "captureEvidenceSource",
    capture?.acquisition_source === "retained_return_control_refresh"
      ? "Exact retained Return Control forced save · no new device input"
      : capture?.acquisition_source === "new_setup_capture_refresh"
        ? "New setup-capture forced save"
        : dash,
  );
  setText("captureTimestamp", formatDate(preview?.captured_at));
  setText(
    "captureMapping",
    preview?.mapping_id
      ? `${preview.mapping_id} · ${humanize(preview.mapping_maturity || "unknown")}`
      : dash,
  );
  setText(
    "captureFieldCount",
    preview?.settings ? Object.keys(preview.settings).length : dash,
  );
  setText(
    "captureDialogStatus",
    capture?.reason
      ? `${humanize(capture.status)} — ${capture.reason}`
        + (capture.authority_outcome
          ? ` · authority: ${humanize(capture.authority_outcome)}`
          : "")
      : capture?.status === "ready"
        ? "Review the exact forced-save projection. Unresolved rows remain unresolved."
        : "Waiting for the runtime-owned forced serialization and restoration.",
  );
  const settingRows = Object.entries(preview?.settings || {}).map(
    ([settingId, value]) => captureRow(humanize(settingId), captureValue(value)),
  );
  populateCaptureList(
    byId("captureSettings"),
    settingRows,
    "The fresh save contained no representable authoring values.",
  );
  const unresolvedRows = (preview?.unresolved || []).map((item) =>
    captureRow(
      item.display_name || humanize(item.setting_id || "unknown"),
      `${humanize(item.status || "unresolved")} — ${item.reason || "reason unavailable"}`
        + (Object.hasOwn(item, "observed_value")
          ? `\nObserved: ${captureValue(item.observed_value)}`
          : ""),
    ));
  populateCaptureList(
    byId("captureUnresolved"),
    unresolvedRows,
    "Every captured value is representable by existing authoring owners.",
  );
  populateCaptureBases(catalog);
  byId("captureSetupForm").hidden = capture?.status !== "ready";
  const retry = byId("retryCaptureButton");
  retry.hidden = !clientModel.captureIsTerminal(capture)
    || catalog?.availability?.available !== true;
  retry.disabled = retry.hidden;
  byId("cancelCaptureButton").disabled = !capture
    || ["capturing", "saved", "cancelled", "failed", "interrupted", "unavailable"].includes(capture.status);
  updateCaptureFormState();
}

function selectedCaptureBase() {
  const selected = byId("captureBase").value;
  if (!selected) return null;
  const [id, revisionText] = selected.split("@");
  return { id, revision: Number(revisionText) };
}

function captureStrategyFields() {
  const fields = {
    kind: "strategy_draft",
    id: byId("captureId").value.trim(),
    display_name: byId("captureDisplayName").value.trim(),
    tier: Number(byId("captureTier").value),
  };
  const base = selectedCaptureBase();
  if (base) fields.base = base;
  return fields;
}

function captureReviewSignature() {
  return JSON.stringify(captureStrategyFields());
}

function clearCaptureReview() {
  state.captureReview = null;
  state.captureReviewInput = null;
  const review = byId("captureDifferenceReview");
  review.hidden = true;
  review.replaceChildren();
}

function updateCaptureFormState() {
  const strategy = byId("captureKind").value === "strategy_draft";
  byId("captureTierLabel").hidden = !strategy;
  byId("captureBaseLabel").hidden = !strategy;
  byId("captureTier").required = strategy;
  byId("reviewCaptureButton").hidden = !strategy;
  const ready = captureFromState()?.status === "ready";
  const reviewed = strategy
    && state.captureReview
    && state.captureReviewInput === captureReviewSignature();
  const modulesAvailable = Boolean(
    captureFromState()?.preview?.settings?.modules?.local,
  );
  byId("saveCaptureButton").disabled = !ready
    || (strategy ? !reviewed : !modulesAvailable);
  byId("saveCaptureButton").title = !strategy && !modulesAvailable
    ? "This capture has no complete local Module loadout to save."
    : strategy && !reviewed
      ? "Review captured-versus-Base differences before saving."
      : "Saving creates a new inactive artifact only.";
}

function renderCaptureDifference(review) {
  const container = byId("captureDifferenceReview");
  container.replaceChildren();
  const difference = review?.captured_vs_base || {};
  const base = difference.base;
  container.append(captureRow(
    base ? `Compared with ${base.id} revision ${base.revision}` : "Compared with an empty draft",
    `${difference.change_count || 0} effective difference(s); ${(difference.provenance_changed || []).length} provenance-only difference(s).`,
  ));
  for (const item of difference.changed || []) {
    container.append(captureRow(
      item.display_name || humanize(item.setting_id),
      `Base: ${captureValue(item.before)}\nCaptured: ${captureValue(item.after)}`,
    ));
  }
  container.append(captureRow(
    "Unresolved capture fields",
    `${(review?.unresolved || []).length} field(s) remain explicit and are not inherited from the comparison Base.`,
  ));
  container.hidden = false;
}

async function loadSetupCaptureCatalog() {
  if (state.captureCatalogLoading) return state.captureCatalog;
  state.captureCatalogLoading = true;
  try {
    state.captureCatalog = await api("/api/v1/setup-capture");
    renderCaptureDialog(state.captureCatalog.capture, state.captureCatalog);
    return state.captureCatalog;
  } finally {
    state.captureCatalogLoading = false;
  }
}

async function openSetupCapture() {
  const dialog = byId("captureSetupDialog");
  if (!dialog.open) dialog.showModal();
  clearCaptureReview();
  try {
    state.captureCatalog = await api("/api/v1/setup-capture");
    const capture = captureFromState();
    const openAction = clientModel.setupCaptureOpenAction(
      capture,
      state.captureCatalog.availability,
    );
    if (openAction !== "request") {
      renderCaptureDialog(capture, state.captureCatalog);
      return;
    }
    state.captureCatalog = await api("/api/v1/setup-capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation: "request" }),
    });
    renderCaptureDialog(state.captureCatalog.capture, state.captureCatalog);
    toast("Fresh save-backed setup capture requested");
    await refresh();
  } catch (error) {
    toast(error.message, true);
    dialog.close();
  }
}

async function retrySetupCapture() {
  try {
    state.captureCatalog = await api("/api/v1/setup-capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation: "request" }),
    });
    clearCaptureReview();
    renderCaptureDialog(state.captureCatalog.capture, state.captureCatalog);
    toast("Fresh save-backed setup capture requested");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

async function reviewSetupCapture() {
  const capture = captureFromState();
  if (capture?.status !== "ready") return;
  try {
    const fields = captureStrategyFields();
    const response = await api("/api/v1/setup-capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        operation: "review",
        request_id: capture.request_id,
        expected_preview_fingerprint: capture.preview_fingerprint,
        ...fields,
      }),
    });
    state.captureCatalog = response;
    state.captureReview = response.review;
    state.captureReviewInput = JSON.stringify(fields);
    renderCaptureDifference(response.review);
    updateCaptureFormState();
    toast("Captured-versus-Base differences are ready for review");
  } catch (error) {
    clearCaptureReview();
    updateCaptureFormState();
    toast(error.message, true);
  }
}

async function saveSetupCapture() {
  const capture = captureFromState();
  if (capture?.status !== "ready") return;
  const strategy = byId("captureKind").value === "strategy_draft";
  const fields = strategy
    ? captureStrategyFields()
    : {
        kind: "module_preset",
        id: byId("captureId").value.trim(),
        display_name: byId("captureDisplayName").value.trim(),
      };
  const payload = {
    operation: "save",
    request_id: capture.request_id,
    expected_preview_fingerprint: capture.preview_fingerprint,
    ...fields,
  };
  if (strategy) payload.expected_review_fingerprint = state.captureReview?.review_fingerprint;
  try {
    const response = await api("/api/v1/setup-capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.captureCatalog = response;
    renderCaptureDialog(response.capture, response);
    const saved = response.request?.saved_result || {};
    toast(`Saved ${saved.display_name || saved.id}; nothing was selected or applied`);
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
}

async function cancelSetupCapture() {
  const capture = captureFromState();
  if (!capture?.request_id) return;
  try {
    state.captureCatalog = await api("/api/v1/setup-capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ operation: "cancel", request_id: capture.request_id }),
    });
    byId("captureSetupDialog").close();
    clearCaptureReview();
    toast("Setup capture review cancelled");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
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
    message.textContent = entry.display_message || entry.message;
    if (entry.display_message) {
      message.title = entry.message;
    }
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
  document.querySelectorAll("[data-control-action], [data-process-action], #applyModeButton, #customPauseForm button, #configureRunButton, #captureSetupButton").forEach((button) => {
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

function formatTerminalPolicy(value) {
  if (value === "NEXT_BATTLE") return "Continue automatically";
  if (value === "HOME") return "Return to / stay Home";
  return humanize(value || dash);
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
    const payload = { action };
    sendProcess(
      payload,
      action === "stop"
        ? "Automation service stopped"
        : "Automation service started Paused",
    );
    return;
  }
  const control = event.target.closest("[data-control-action]");
  if (control) {
    const action = control.dataset.controlAction;
    if (action === "stop" && !window.confirm("Persist STOPPED for the automation runtime?")) return;
    if (action === "take_manual_control") {
      const dialog = byId("manualControlDialog");
      if (!dialog.open) dialog.showModal();
      return;
    }
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
  if (close) {
    if (
      close.dataset.closeDialog === "saveMappingIntegrationDialog"
      && state.saveMappingBusy
    ) return;
    byId(close.dataset.closeDialog).close();
  }
});

byId("customPauseForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const minutes = Number(byId("customPauseMinutes").value);
  sendControl({ action: "pause", minutes }, `Paused for ${minutes} minutes`);
});

byId("manualControlForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const selected = event.currentTarget.querySelector(
    'input[name="manualSurrenderCollection"]:checked',
  );
  if (!selected) return;
  byId("manualControlDialog").close();
  sendControl(
    {
      action: "take_manual_control",
      manual_surrender_collection: selected.value,
    },
    `Manual Control requested; manual Surrender collection is ${humanize(selected.value)}`,
  );
});

byId("applyModeButton").addEventListener("click", () => {
  const mode = byId("modeSelect").value;
  sendControl(
    { action: "terminal_policy", policy: mode },
    `When this battle ends: ${formatTerminalPolicy(mode)}`,
  );
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
byId("reviewSaveMappingsButton").addEventListener(
  "click",
  openSaveMappingIntegration,
);
byId("refreshSaveMappingCatalogButton").addEventListener(
  "click",
  loadSaveMappingIntegrationCatalog,
);
byId("saveMappingCandidateSelect").addEventListener(
  "change",
  () => {
    if (state.saveMappingBusy) return;
    state.saveMappingSelectionGeneration += 1;
    renderSaveMappingSelection();
  },
);
byId("saveMappingWorkspaceSelect").addEventListener(
  "change",
  () => {
    if (state.saveMappingBusy) return;
    state.saveMappingSelectionGeneration += 1;
    renderSaveMappingSelection();
  },
);
byId("saveMappingIntegrationDialog").addEventListener("cancel", (event) => {
  if (state.saveMappingBusy) event.preventDefault();
});
byId("reviewSaveMappingProposalButton").addEventListener(
  "click",
  reviewSaveMappingProposal,
);
byId("prepareSaveMappingButton").addEventListener(
  "click",
  prepareSaveMappingProposal,
);
byId("configureRunButton").addEventListener("click", openRunConfiguration);
byId("captureSetupButton").addEventListener("click", openSetupCapture);
byId("retryCaptureButton").addEventListener("click", retrySetupCapture);
byId("reviewCaptureButton").addEventListener("click", reviewSetupCapture);
byId("cancelCaptureButton").addEventListener("click", cancelSetupCapture);
byId("captureSetupForm").addEventListener("submit", (event) => {
  event.preventDefault();
  saveSetupCapture();
});
byId("captureSetupForm").addEventListener("input", () => {
  clearCaptureReview();
  updateCaptureFormState();
});
byId("captureSetupForm").addEventListener("change", () => {
  clearCaptureReview();
  updateCaptureFormState();
});
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
