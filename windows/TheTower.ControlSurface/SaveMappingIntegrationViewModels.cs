using System.Globalization;
using System.Text;
using System.Text.Json;

namespace TheTower.ControlSurface;

internal sealed record SaveMappingPreparePresentation(
    bool Available,
    string Code,
    string Reason);

internal sealed record SaveMappingResultPresentation(
    bool Success,
    string Title,
    string Detail,
    string Code);

internal sealed record SaveMappingPreparedResultValidation(
    bool Valid,
    string Code,
    string Reason);

internal sealed record SaveMappingFailurePresentation(
    bool Uncertain,
    string Title,
    string Detail);

internal static class SaveMappingIntegrationViewModels
{
    public static string CandidateLabel(SaveMappingIntegrationItem item)
    {
        var slot = item.Scope.TryGetValue("slot_key", out var slotKey)
            && !string.IsNullOrWhiteSpace(slotKey)
                ? $" · {slotKey}"
                : "";
        var value = item.RawValue is decimal rawValue
            ? $" · {rawValue.ToString(CultureInfo.InvariantCulture)} → "
                + (string.IsNullOrWhiteSpace(item.SemanticValue)
                    ? "unknown"
                    : item.SemanticValue)
            : "";
        return $"{Format(item.CheckId)}{slot}{value}";
    }

    public static string WorkspaceLabel(SaveMappingWorkspaceStatus workspace)
    {
        var head = workspace.HeadCommit.Length > 12
            ? workspace.HeadCommit[..12]
            : workspace.HeadCommit;
        var status = workspace.Available
            ? "ready"
            : Format(string.IsNullOrWhiteSpace(workspace.Code)
                ? "unavailable"
                : workspace.Code);
        return $"{workspace.Branch} · {head} · {status}";
    }

    public static bool ReviewMatches(
        SaveMappingIntegrationReview? review,
        string? candidateRecordId,
        string? workspaceId) =>
        review is not null
        && review.SchemaVersion == 1
        && review.Capability == "save_mapping_integration_v1"
        && review.Operation == "review"
        && string.Equals(
            review.CandidateRecordId,
            candidateRecordId,
            StringComparison.Ordinal)
        && string.Equals(
            review.Workspace.WorkspaceId,
            workspaceId,
            StringComparison.Ordinal);

    public static SaveMappingPreparePresentation PrepareAvailability(
        SaveMappingIntegrationReview? review,
        string? candidateRecordId,
        string? workspaceId)
    {
        if (!ReviewMatches(review, candidateRecordId, workspaceId))
        {
            return new(
                false,
                "review_stale",
                "Select the observation and owned feature worktree, then review the exact proposal.");
        }
        if (!IsLowerHex64(review!.ReviewedProposalFingerprint))
        {
            return new(
                false,
                "review_fingerprint_missing",
                "The reviewed proposal fingerprint is unavailable.");
        }
        return new(
            review.Prepare.Available,
            review.Prepare.Code ?? "",
            review.Prepare.Reason ?? "");
    }

    public static IReadOnlyList<SaveMappingProposalTarget> Targets(
        SaveMappingProposal proposal)
    {
        if (proposal.SchemaVersion == 2)
        {
            return proposal.Targets;
        }
        if (proposal.Target is null)
        {
            return [];
        }
        proposal.Target.Operations = proposal.Operations;
        return [proposal.Target];
    }

    public static string ProposalText(SaveMappingIntegrationReview review)
    {
        var text = new StringBuilder();
        text.AppendLine("REVIEWED PROPOSAL FINGERPRINT");
        text.AppendLine(review.ReviewedProposalFingerprint);
        text.AppendLine();
        text.AppendLine("REPOSITORY SNAPSHOT");
        text.AppendLine($"main    {review.Repository.MainCommit}");
        text.AppendLine($"develop {review.Repository.DevelopCommit}");
        text.AppendLine($"feature {review.Workspace.HeadCommit}");
        foreach (var target in Targets(review.Proposal))
        {
            text.AppendLine();
            text.AppendLine($"TARGET {target.MappingId} · {target.Path}");
            text.AppendLine($"base {target.ExpectedSha256}");
            text.AppendLine($"state {target.State}");
            if (target.Operations.Count == 0)
            {
                text.AppendLine("Already present; no operation for this target.");
                continue;
            }
            foreach (var operation in target.Operations)
            {
                text.AppendLine($"{operation.Operation} {operation.Path}");
                text.AppendLine(JsonSerializer.Serialize(
                    operation.Value,
                    new JsonSerializerOptions { WriteIndented = true }));
            }
        }
        return text.ToString().TrimEnd();
    }

    public static SaveMappingPreparedResultValidation ValidatePreparedResult(
        SaveMappingPreparedResult? result,
        string? candidateRecordId,
        string? workspaceId,
        string? reviewedProposalFingerprint)
    {
        var targets = result?.Targets;
        var validation = result?.Validation;
        var valid = result is not null
            && result.SchemaVersion == 1
            && result.Capability == "save_mapping_integration_v1"
            && result.Operation == "prepare"
            && result.Disposition == "prepared"
            && result.Idempotent.HasValue
            && !string.IsNullOrWhiteSpace(candidateRecordId)
            && result.CandidateRecordId == candidateRecordId
            && !string.IsNullOrWhiteSpace(workspaceId)
            && result.Workspace.WorkspaceId == workspaceId
            && IsLowerHex64(reviewedProposalFingerprint)
            && result.ReviewedProposalFingerprint == reviewedProposalFingerprint
            && result.Committed is false
            && result.Promoted is false
            && result.ValidationStatus == "pending"
            && targets is { Count: > 0 }
            && targets.All(target =>
                !string.IsNullOrWhiteSpace(target.Path)
                && !string.IsNullOrWhiteSpace(target.MappingId)
                && IsLowerHex64(target.BeforeSha256)
                && IsLowerHex64(target.AfterSha256)
                && target.Changed.HasValue)
            && targets.Any(target => target.Changed is true)
            && validation is not null
            && validation.All(item => item is not null);
        return new(
            valid,
            valid ? "" : "prepared_result_invalid",
            valid
                ? ""
                : "The server response did not prove this exact reviewed proposal was prepared.");
    }

    public static SaveMappingResultPresentation PreparedResult(
        SaveMappingPreparedResult? result,
        string? candidateRecordId,
        string? workspaceId,
        string? reviewedProposalFingerprint)
    {
        var validation = ValidatePreparedResult(
            result,
            candidateRecordId,
            workspaceId,
            reviewedProposalFingerprint);
        if (!validation.Valid)
        {
            return new(
                false,
                "Preparation outcome is unconfirmed",
                validation.Reason
                    + " Refresh the catalog before taking another action.",
                validation.Code);
        }
        var changed = result!.Targets!.Count(target => target.Changed is true);
        return new(
            true,
            result.Idempotent is true
                ? "Already prepared in feature worktree"
                : "Prepared in feature worktree",
            $"{changed} tracked mapping file{(changed == 1 ? "" : "s")} prepared. "
                + "Validation, commit, and promotion remain required.",
            "");
    }

    public static string PreparedResultText(
        SaveMappingPreparedResult result,
        string? candidateRecordId,
        string? workspaceId,
        string? reviewedProposalFingerprint)
    {
        var presentation = PreparedResult(
            result,
            candidateRecordId,
            workspaceId,
            reviewedProposalFingerprint);
        var text = new StringBuilder();
        text.AppendLine(presentation.Title);
        text.AppendLine(presentation.Detail);
        if (!presentation.Success)
        {
            return text.ToString().TrimEnd();
        }
        text.AppendLine();
        text.AppendLine($"committed: {Lower(result.Committed)}");
        text.AppendLine($"promoted: {Lower(result.Promoted)}");
        text.AppendLine($"validation: {result.ValidationStatus}");
        foreach (var target in result.Targets!)
        {
            text.AppendLine();
            text.AppendLine($"{target.MappingId} · {target.Path}");
            text.AppendLine(target.BeforeSha256);
            text.AppendLine($"→ {target.AfterSha256}");
        }
        if (result.Validation!.Count > 0)
        {
            text.AppendLine();
            text.AppendLine("VALIDATION STILL REQUIRED");
            foreach (var command in result.Validation)
            {
                text.AppendLine(command);
            }
        }
        if (!string.IsNullOrWhiteSpace(result.Warning))
        {
            text.AppendLine();
            text.AppendLine("AUDIT WARNING");
            text.AppendLine(result.Warning);
        }
        return text.ToString().TrimEnd();
    }

    public static SaveMappingFailurePresentation Failure(
        string? code,
        string message,
        bool prepareRequest)
    {
        if (code == "integration_busy")
        {
            return new(
                false,
                "Another preparation is in progress",
                message
                    + " This request did not acquire preparation authority. "
                    + "Refresh after the active request finishes; do not retry automatically.");
        }
        if (code == "mapping_prepare_write_failed")
        {
            return new(
                false,
                "Preparation rolled back",
                message
                    + " No prepared changes from this request remain. "
                    + "Refresh and review again.");
        }
        var safelyRejected = code is "reviewed_proposal_stale"
            or "workspace_dirty"
            or "proposal_base_changed"
            or "workspace_snapshot_stale";
        if (safelyRejected || !prepareRequest)
        {
            return new(
                false,
                prepareRequest ? "Preparation rejected" : "Review unavailable",
                message
                    + " Nothing was written by this request. Refresh and review again.");
        }
        return new(
            true,
            "Preparation outcome is unconfirmed",
            message
                + " Inspect or refresh the selected feature worktree before "
                + "another action; do not retry automatically.");
    }

    private static bool IsLowerHex64(string? value) =>
        value is { Length: 64 }
        && value.All(character =>
            character is >= '0' and <= '9'
            || character is >= 'a' and <= 'f');

    private static string Lower(bool? value) =>
        value?.ToString().ToLowerInvariant() ?? "missing";

    private static string Format(string? value) =>
        string.Join(
            " ",
            (value ?? "")
                .Split('_', StringSplitOptions.RemoveEmptyEntries)
                .Select(word => char.ToUpperInvariant(word[0]) + word[1..]));
}
