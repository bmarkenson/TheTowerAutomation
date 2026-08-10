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

  function confirmedLocalMappingPresentation(status) {
    if (!status || typeof status !== "object") {
      return {
        visible: true,
        severity: "danger",
        title: "Local save mapping status is unavailable",
        detail: "The connected API does not publish the required mapping-status contract. Update or restart the Linux API; automation authority is unchanged.",
        items: [],
      };
    }
    const items = Array.isArray(status?.items) ? status.items : [];
    if (status?.available === false) {
      return {
        visible: true,
        severity: "danger",
        title: "Local save mapping status is unavailable",
        detail: status?.reason
          || "The local confirmation store could not be read. Affected checks continue through UI fallback.",
        items: [],
      };
    }
    const statePriority = {
      canonical_conflict: 0,
      identity_conflict: 0,
      invalid_local_store: 0,
      reconfirmation_required: 0,
      evidence_ambiguous: 0,
      authority_pending: 1,
      active_local: 1,
      review_required: 1,
      more_evidence_required: 1,
      mirror_pending: 2,
    };
    const visibleItems = items.filter((item) => [
      "active_local",
      "authority_pending",
      "mirror_pending",
      "canonical_conflict",
      "reconfirmation_required",
      "invalid_local_store",
      "identity_conflict",
      "review_required",
      "more_evidence_required",
      "evidence_ambiguous",
    ].includes(String(item?.state || ""))).sort((left, right) => (
      (statePriority[left?.state] ?? 99) - (statePriority[right?.state] ?? 99)
    ));
    if (!visibleItems.length) {
      return { visible: false, severity: "neutral", title: "", detail: "", items: [] };
    }
    const states = new Set(visibleItems.map((item) => item.state));
    const dangerous = [
      "canonical_conflict",
      "reconfirmation_required",
      "invalid_local_store",
      "identity_conflict",
      "evidence_ambiguous",
    ].some((value) => states.has(value));
    const authorityPending = states.has("authority_pending");
    const active = states.has("active_local");
    const review = states.has("review_required") || states.has("more_evidence_required");
    const severity = dangerous ? "danger" : (authorityPending || active || review) ? "warning" : "info";
    const title = dangerous
      ? "A local save mapping needs attention"
      : authorityPending
        ? "Canonical save-mapping authority is still pending"
        : active
          ? "A locally confirmed save mapping needs integration"
          : review
            ? "A save mapping observation needs review"
          : "Exact-version save-mapping mirror is pending";
    const first = visibleItems[0];
    const scope = first?.scope?.slot_key ? ` ${first.scope.slot_key}` : "";
    const mapping = first?.mapping_id ? ` for ${first.mapping_id}` : "";
    const value = first?.raw_value == null
      ? ""
      : `: save value ${first.raw_value} = ${first.semantic_value || "unknown"}`;
    const count = visibleItems.length > 1
      ? ` ${visibleItems.length} records require review.`
      : "";
    const check = String(first?.check_id || "save mapping")
      .replaceAll("_", " ");
    const subject = first?.check_id === "modules"
      ? `Module${scope}`
      : `${check}${scope}`;
    return {
      visible: true,
      severity,
      title,
      detail: `${subject}${value}${mapping}. ${first?.reason || "Canonical integration is pending."}${count}`,
      items: visibleItems,
    };
  }

  return {
    captureCatalogMatches,
    captureIsTerminal,
    chooseLatestCapture,
    setupCaptureOpenAction,
    workflowPresentation,
    confirmedLocalMappingPresentation,
  };
}));
