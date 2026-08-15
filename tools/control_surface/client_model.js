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
    if (!status || typeof status !== "object" || status.schema_version !== 2
      || !Array.isArray(status.items)) {
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
      integration_unconfirmed: 0,
      integration_recovery_required: 1,
      restaging_required: 1,
      promotion_pending: 2,
      automatic_integration_pending: 2,
      promotion_cleanup_pending: 3,
      remote_publication_pending: 3,
      production_validation_pending: 4,
      authority_pending: 4,
      active_local: 5,
      review_required: 6,
      more_evidence_required: 7,
      mirror_pending: 8,
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
      "integration_unconfirmed",
      "integration_recovery_required",
      "restaging_required",
      "promotion_pending",
      "automatic_integration_pending",
      "promotion_cleanup_pending",
      "remote_publication_pending",
      "production_validation_pending",
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
      "integration_unconfirmed",
    ].some((value) => states.has(value));
    const first = visibleItems[0];
    const winningState = String(first?.state || "");
    const severity = dangerous || ["integration_recovery_required", "restaging_required"].includes(winningState)
      ? (dangerous ? "danger" : "warning")
      : ["authority_pending", "active_local", "review_required", "more_evidence_required"].includes(winningState)
        ? "warning" : "info";
    const title = dangerous
      ? "A local save mapping needs attention"
      : winningState === "integration_recovery_required"
        ? "Save-mapping integration recovery requires direction"
      : winningState === "restaging_required"
        ? "Save mapping must be restaged on current main"
      : winningState === "promotion_pending"
        ? "Verified save mapping queued for automatic promotion"
      : winningState === "automatic_integration_pending"
        ? "Verified save mapping queued for automatic integration"
      : winningState === "promotion_cleanup_pending"
        ? "Published save mapping awaiting automatic cleanup"
      : winningState === "remote_publication_pending"
        ? "Save mapping awaiting automatic publication"
      : winningState === "production_validation_pending"
        ? "Deployed save mapping awaiting fresh validation"
      : winningState === "authority_pending"
        ? "Canonical Module identity owner is still pending"
        : winningState === "active_local"
          ? "A locally confirmed Module identity needs integration"
          : ["review_required", "more_evidence_required"].includes(winningState)
            ? "A save mapping observation needs review"
          : "Exact-version Module identity mirror is pending";
    const scope = first?.scope?.slot_key ? ` ${first.scope.slot_key}` : "";
    const mapping = first?.mapping_id ? ` for ${first.mapping_id}` : "";
    const value = first?.raw_value == null
      ? ""
      : `: save value ${first.raw_value} = ${first.semantic_value || "unknown"}`;
    const count = visibleItems.length > 1
      ? ` ${visibleItems.length} records remain pending.`
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

  const lowerHex64 = (value) => /^[0-9a-f]{64}$/.test(value || "");
  const gitObject = (value) => /^[0-9a-f]{40,64}$/.test(value || "");
  const mappingTargetPath = (value) => (
    /^config\/player_save_versions\/[^/]+\.json$/.test(value || "")
  );

  function reviewProposalTargets(proposal) {
    if (proposal?.schema_version === 2) {
      return Array.isArray(proposal.targets) ? proposal.targets : [];
    }
    if (proposal?.schema_version === 1 && proposal.target) {
      return [{
        ...proposal.target,
        operations: Array.isArray(proposal.operations) ? proposal.operations : [],
      }];
    }
    return [];
  }

  function saveMappingReviewIsCurrent(review, candidateRecordId) {
    if (!review || typeof review !== "object") return false;
    const candidate = String(candidateRecordId || "").trim();
    const repository = review.repository;
    const proposal = review.proposal;
    const proposalTargets = reviewProposalTargets(proposal);
    const renderedTargets = Array.isArray(review.rendered_targets)
      ? review.rendered_targets : [];
    const recovery = review.recovery_required === true;
    const validRepository = repository
      && typeof repository === "object"
      && gitObject(repository.main_commit)
      && typeof repository.staging_ref === "string"
      && repository.staging_ref.startsWith("refs/")
      && (repository.staged_commit == null
        || gitObject(repository.staged_commit))
      && (recovery
        ? repository.integration_available === false
          && repository.code === "transaction_recovery_required"
        : repository.integration_available === true
          && repository.production_clean === true
          && repository.staged_commit == null);
    const validProposalTarget = (target) => target
      && typeof target === "object"
      && mappingTargetPath(target.path)
      && typeof target.mapping_id === "string"
      && target.mapping_id.length > 0
      && lowerHex64(target.expected_sha256)
      && Array.isArray(target.operations)
      && target.operations.every((operation) => operation
        && typeof operation === "object"
        && typeof operation.op === "string"
        && operation.op.length > 0
        && typeof operation.path === "string"
        && operation.path.startsWith("/")
        && Object.prototype.hasOwnProperty.call(operation, "value"));
    const validRenderedTarget = (target) => target
      && typeof target === "object"
      && mappingTargetPath(target.path)
      && typeof target.mapping_id === "string"
      && target.mapping_id.length > 0
      && lowerHex64(target.before_sha256)
      && lowerHex64(target.after_sha256)
      && typeof target.changed === "boolean"
      && Number.isInteger(target.mode)
      && target.mode > 0
      && (target.mode & 0o111) === 0;
    const proposalKeys = new Set(
      proposalTargets.map((target) => `${target.path}\0${target.mapping_id}`),
    );
    const renderedKeys = new Set(
      renderedTargets.map((target) => `${target.path}\0${target.mapping_id}`),
    );
    const targetsCorrespond = proposalKeys.size === proposalTargets.length
      && renderedKeys.size === renderedTargets.length
      && proposalKeys.size === renderedKeys.size
      && [...proposalKeys].every((key) => renderedKeys.has(key))
      && renderedTargets.every((rendered) => proposalTargets.some(
        (target) => target.path === rendered.path
          && target.mapping_id === rendered.mapping_id
          && target.expected_sha256 === rendered.before_sha256,
      ));
    return review.schema_version === 3
      && review.capability === "save_mapping_staged_candidate_v1"
      && review.operation === "review"
      && lowerHex64(candidate)
      && review.candidate_record_id === candidate
      && lowerHex64(review.reviewed_proposal_fingerprint)
      && gitObject(review.reviewed_base_commit)
      && lowerHex64(review.canonical_mapping_fingerprint)
      && validRepository
      && proposal
      && proposal.record_id === candidate
      && proposalTargets.length > 0
      && proposalTargets.every(validProposalTarget)
      && renderedTargets.length > 0
      && renderedTargets.every(validRenderedTarget)
      && renderedTargets.some((target) => target.changed)
      && targetsCorrespond
      && (recovery || proposalTargets.some(
        (target) => target.operations.length > 0,
      ));
  }

  function saveMappingIntegrationCompatible(status) {
    return status?.api_version === 1
      && Number(status?.server_revision) >= 45
      && (status?.capabilities || []).includes(
        "save_mapping_staged_candidate_v1",
      )
      && (status?.capabilities || []).includes(
        "save_mapping_candidate_disposition_v1",
      )
      && (status?.capabilities || []).includes(
        "save_mapping_automatic_promotion_v1",
      )
      && (status?.capabilities || []).includes(
        "save_mapping_machine_verification_v1",
      );
  }

  function saveMappingDismissedResultValidation(result, candidateRecordId) {
    const candidate = String(candidateRecordId || "").trim();
    const recordedAt = Date.parse(String(result?.recorded_at || ""));
    const valid = result
      && typeof result === "object"
      && result.schema_version === 1
      && result.capability === "save_mapping_candidate_disposition_v1"
      && result.operation === "dismiss"
      && result.disposition === "dismissed"
      && lowerHex64(candidate)
      && result.candidate_record_id === candidate
      && lowerHex64(result.event_id)
      && Number.isFinite(recordedAt)
      && typeof result.changed === "boolean"
      && result.evidence_preserved === true
      && (result.warning === undefined || typeof result.warning === "string");
    return {
      valid: Boolean(valid),
      code: valid ? "" : "dismissal_result_invalid",
      reason: valid
        ? ""
        : "The server response did not prove that this exact observation was dismissed while preserving its evidence.",
    };
  }

  function saveMappingIntegrateAvailability(
    review,
    candidateRecordId,
  ) {
    if (!saveMappingReviewIsCurrent(review, candidateRecordId)) {
      return {
        available: false,
        code: "review_stale",
        reason: "Select the candidate, then review the exact proposal.",
      };
    }
    const staging = review.stage || {};
    return {
      available: staging.available === true,
      code: String(staging.code || ""),
      reason: String(staging.reason || ""),
    };
  }

  function saveMappingIntegratedResultValidation(
    result,
    review,
  ) {
    const expectedCandidate = String(review?.candidate_record_id || "").trim();
    const expectedFingerprint = String(
      review?.reviewed_proposal_fingerprint || "",
    ).trim();
    const targets = Array.isArray(result?.targets) ? result.targets : [];
    const reviewedTargets = Array.isArray(review?.rendered_targets)
      ? review.rendered_targets : [];
    const validTarget = (target) => target
      && typeof target === "object"
      && typeof target.path === "string"
      && target.path.length > 0
      && typeof target.mapping_id === "string"
      && target.mapping_id.length > 0
      && /^[0-9a-f]{64}$/.test(target.before_sha256 || "")
      && /^[0-9a-f]{64}$/.test(target.after_sha256 || "")
      && typeof target.changed === "boolean"
      && Number.isInteger(target.mode)
      && (target.mode & 0o111) === 0;
    const targetKeys = new Set(
      targets.map((target) => `${target.path}\0${target.mapping_id}`),
    );
    const reviewedTargetKeys = new Set(
      reviewedTargets.map((target) => `${target.path}\0${target.mapping_id}`),
    );
    const exactTargets = targetKeys.size === targets.length
      && reviewedTargetKeys.size === reviewedTargets.length
      && targetKeys.size === reviewedTargetKeys.size
      && [...targetKeys].every((key) => reviewedTargetKeys.has(key))
      && targets.length === reviewedTargets.length
      && targets.every((target) => reviewedTargets.some((reviewed) => (
        target.path === reviewed.path
        && target.mapping_id === reviewed.mapping_id
        && target.before_sha256 === reviewed.before_sha256
        && target.after_sha256 === reviewed.after_sha256
        && target.changed === reviewed.changed
        && target.mode === reviewed.mode
      )));
    const promoted = result?.disposition === "promoted"
      && result?.promoted === true
      && result?.published === true
      && result?.automatic_retry === false;
    const queued = result?.disposition === "promotion_queued"
      && typeof result?.promoted === "boolean"
      && typeof result?.published === "boolean"
      && (result.published === false || result.promoted === true)
      && result?.automatic_retry === true
      && typeof result?.code === "string"
      && result.code.length > 0
      && typeof result?.reason === "string"
      && result.reason.length > 0
      && typeof result?.agent_review_prompt === "string";
    const valid = result
      && typeof result === "object"
      && result.schema_version === 3
      && result.capability === "save_mapping_staged_candidate_v1"
      && result.operation === "integrate"
      && (promoted || queued)
      && typeof result.idempotent === "boolean"
      && expectedCandidate.length > 0
      && result.candidate_record_id === expectedCandidate
      && /^[0-9a-f]{64}$/.test(expectedFingerprint)
      && result.reviewed_proposal_fingerprint === expectedFingerprint
      && gitObject(result.base_commit)
      && result.staging_ref === review?.repository?.staging_ref
      && gitObject(result.staged_commit)
      && result.committed === true
      && result.staged === true
      && typeof result.promoted === "boolean"
      && typeof result.published === "boolean"
      && typeof result.agent_required === "boolean"
      && typeof result.next_action === "string"
      && result.next_action.length > 0
      && result.mapping_invariants === "passed"
      && result.promotion_validation === "pending"
      && targets.length > 0
      && targets.every(validTarget)
      && targets.some((target) => target.changed)
      && exactTargets
      && (result.warning === undefined || typeof result.warning === "string");
    return {
      valid: Boolean(valid),
      code: valid ? "" : "integrated_result_invalid",
      reason: valid
        ? ""
        : "The server response did not prove this exact reviewed proposal was promoted or durably queued for automatic promotion.",
    };
  }

  function saveMappingIntegratedPresentation(
    result,
    review,
  ) {
    const validation = saveMappingIntegratedResultValidation(
      result,
      review,
    );
    if (!validation.valid) {
      return {
        success: false,
        title: "Integration outcome is unconfirmed",
        detail: `${validation.reason} Refresh the catalog before taking another action.`,
        code: validation.code,
      };
    }
    const changed = result.targets.filter((target) => target.changed).length;
    if (result.disposition === "promotion_queued") {
      return {
        success: false,
        title: result.published
          ? "Mapping published; cleanup queued"
          : result.promoted
          ? "Mapping promoted; publication queued"
          : "Automatic promotion queued",
        detail: `${changed} canonical mapping file${changed === 1 ? "" : "s"} committed as ${result.staged_commit.slice(0, 12)}. ${result.reason} ${result.next_action}`,
        code: result.code,
      };
    }
    return {
      success: true,
      title: "Mapping integrated and published",
      detail: `${changed} canonical mapping file${changed === 1 ? "" : "s"} committed as ${result.staged_commit.slice(0, 12)}. Production and origin contain it; only a fresh stable decode remains pending.`,
      code: "",
    };
  }

  function saveMappingFailurePresentation(error, integrateRequest = true) {
    const code = String(error?.code || "");
    const message = String(error?.message || "Integration failed.");
    if (code === "integration_busy") {
      return {
        uncertain: false,
        title: "Another integration is in progress",
        detail: `${message} This request did not acquire integration authority. Refresh after the active request finishes; do not retry automatically.`,
      };
    }
    if (code === "staging_ref_update_failed") {
      return {
        uncertain: false,
        title: "Nothing was staged",
        detail: `${message} Refresh, verify the same review, and retry once only when directed.`,
      };
    }
    const safelyRejected = [
      "reviewed_proposal_stale",
      "production_worktree_dirty",
      "staging_ref_occupied",
      "proposal_base_changed",
    ].includes(code);
    if (safelyRejected || !integrateRequest) {
      return {
        uncertain: false,
        title: integrateRequest ? "Integration rejected" : "Review unavailable",
        detail: `${message} Nothing was written by this request. Refresh and review again.`,
      };
    }
    return {
      uncertain: true,
      title: "Integration outcome is unconfirmed",
      detail: `${message} Inspect main, the private staging ref, and the durable transaction before another action; do not retry automatically.`,
    };
  }

  return {
    captureCatalogMatches,
    captureIsTerminal,
    chooseLatestCapture,
    setupCaptureOpenAction,
    workflowPresentation,
    confirmedLocalMappingPresentation,
    saveMappingFailurePresentation,
    saveMappingDismissedResultValidation,
    saveMappingIntegrationCompatible,
    saveMappingIntegrateAvailability,
    saveMappingIntegratedPresentation,
    saveMappingIntegratedResultValidation,
    saveMappingReviewIsCurrent,
  };
}));
