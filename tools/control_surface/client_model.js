"use strict";

(function exposeClientModel(root, factory) {
  const model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  if (root) root.TheTowerControlClientModel = model;
}(typeof globalThis === "object" ? globalThis : this, () => {
  const captureRank = {
    requested: 0,
    acknowledged: 1,
    capturing: 2,
    ready: 3,
    saved: 4,
    cancelled: 4,
    unavailable: 4,
    interrupted: 4,
    failed: 4,
  };

  const workflowLabels = {
    requested: "Requested",
    pending: "Pending acknowledgement",
    acknowledged: "Acknowledged",
    action_dispatched: "Action dispatched",
    validating_save: "Validating fresh save",
    ready: "Ready",
    completed: "Completed",
    no_op: "No change needed",
    stale: "Stale",
    rejected: "Rejected",
    unavailable: "Unavailable",
    interrupted: "Interrupted",
    failed: "Failed",
    cancelled: "Cancelled",
  };

  function timestamp(value) {
    const parsed = Date.parse(value || "");
    return Number.isFinite(parsed) ? parsed : -1;
  }

  function chooseLatestCapture(statusCapture, catalogCapture) {
    if (!statusCapture) return catalogCapture || null;
    if (!catalogCapture) return statusCapture;
    const statusTime = timestamp(statusCapture.updated_at);
    const catalogTime = timestamp(catalogCapture.updated_at);
    if (statusTime !== catalogTime) {
      return statusTime > catalogTime ? statusCapture : catalogCapture;
    }
    if (statusCapture.request_id !== catalogCapture.request_id) {
      return statusCapture;
    }
    const statusRank = captureRank[statusCapture.status] ?? -1;
    const catalogRank = captureRank[catalogCapture.status] ?? -1;
    if (statusRank !== catalogRank) {
      return statusRank > catalogRank ? statusCapture : catalogCapture;
    }
    if (
      statusCapture.preview_fingerprint
      && !catalogCapture.preview_fingerprint
    ) return statusCapture;
    return catalogCapture;
  }

  function captureCatalogMatches(statusCapture, catalogCapture) {
    if (!statusCapture || !catalogCapture) return false;
    return statusCapture.request_id === catalogCapture.request_id
      && statusCapture.status === catalogCapture.status
      && (statusCapture.preview_fingerprint || "")
        === (catalogCapture.preview_fingerprint || "")
      && (statusCapture.updated_at || "") === (catalogCapture.updated_at || "");
  }

  function captureIsTerminal(capture) {
    return [
      "saved",
      "cancelled",
      "unavailable",
      "interrupted",
      "failed",
    ].includes(String(capture?.status || "").toLowerCase());
  }

  function setupCaptureOpenAction(capture, availability) {
    const status = String(capture?.status || "").toLowerCase();
    if (status === "ready") return "review";
    if (["requested", "acknowledged", "capturing"].includes(status)) {
      return "progress";
    }
    if (captureIsTerminal(capture)) return "inspect";
    return availability?.available === true ? "request" : "unavailable";
  }

  function workflowPresentation(status) {
    const normalized = String(status || "unknown").trim().toLowerCase();
    return {
      status: normalized,
      label: workflowLabels[normalized]
        || normalized.replaceAll("_", " "),
      pending: [
        "requested",
        "pending",
        "acknowledged",
        "action_dispatched",
        "validating_save",
      ].includes(normalized),
      terminal: [
        "completed",
        "no_op",
        "stale",
        "rejected",
        "unavailable",
        "interrupted",
        "failed",
        "cancelled",
      ].includes(normalized),
    };
  }

  return {
    captureCatalogMatches,
    captureIsTerminal,
    chooseLatestCapture,
    setupCaptureOpenAction,
    workflowPresentation,
  };
}));
