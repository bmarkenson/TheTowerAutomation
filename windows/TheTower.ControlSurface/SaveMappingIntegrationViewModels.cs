using System.Globalization;
using System.Text;
using System.Text.Json;

namespace TheTower.ControlSurface;

internal sealed record SaveMappingIntegratePresentation(
    bool Available,
    string Code,
    string Reason);

internal sealed record SaveMappingResultPresentation(
    bool Success,
    string Title,
    string Detail,
    string Code);

internal sealed record SaveMappingIntegratedResultValidation(
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

    public static bool ReviewMatches(
        SaveMappingIntegrationReview? review,
        string? candidateRecordId)
    {
        if (review is null
            || review.SchemaVersion != 3
            || review.Capability != "save_mapping_staged_candidate_v1"
            || review.Operation != "review"
            || !IsLowerHex64(candidateRecordId)
            || review.CandidateRecordId != candidateRecordId
            || !IsLowerHex64(review.ReviewedProposalFingerprint)
            || !IsGitObject(review.ReviewedBaseCommit)
            || !IsLowerHex64(review.CanonicalMappingFingerprint))
        {
            return false;
        }
        var repository = review.Repository;
        var recovery = review.RecoveryRequired;
        var repositoryValid = IsGitObject(repository.MainCommit)
            && repository.StagingRef.StartsWith("refs/", StringComparison.Ordinal)
            && (repository.StagedCommit is null
                || IsGitObject(repository.StagedCommit))
            && (recovery
                ? !repository.IntegrationAvailable
                    && repository.Code == "transaction_recovery_required"
                : repository.IntegrationAvailable
                    && repository.ProductionClean
                    && repository.StagedCommit is null);
        var proposalTargets = Targets(review.Proposal);
        var renderedTargets = review.RenderedTargets ?? [];
        var proposalValid = review.Proposal.RecordId == candidateRecordId
            && proposalTargets.Count > 0
            && proposalTargets.All(target =>
                IsMappingTargetPath(target.Path)
                && !string.IsNullOrWhiteSpace(target.MappingId)
                && IsLowerHex64(target.ExpectedSha256)
                && target.Operations is not null
                && target.Operations.All(operation =>
                    !string.IsNullOrWhiteSpace(operation.Operation)
                    && operation.Path.StartsWith("/", StringComparison.Ordinal)
                    && operation.Value.ValueKind != JsonValueKind.Undefined));
        var renderedValid = renderedTargets.Count > 0
            && renderedTargets.All(target =>
                IsMappingTargetPath(target.Path)
                && !string.IsNullOrWhiteSpace(target.MappingId)
                && IsLowerHex64(target.BeforeSha256)
                && IsLowerHex64(target.AfterSha256)
                && target.Changed.HasValue
                && target.Mode is > 0
                && (target.Mode.Value & Convert.ToInt32("111", 8)) == 0)
            && renderedTargets.Any(target => target.Changed is true);
        var proposalKeys = proposalTargets
            .Select(target => $"{target.Path}\0{target.MappingId}")
            .ToHashSet(StringComparer.Ordinal);
        var renderedKeys = renderedTargets
            .Select(target => $"{target.Path}\0{target.MappingId}")
            .ToHashSet(StringComparer.Ordinal);
        var targetsCorrespond = proposalKeys.Count == proposalTargets.Count
            && renderedKeys.Count == renderedTargets.Count
            && proposalKeys.SetEquals(renderedKeys)
            && renderedTargets.All(rendered => proposalTargets.Any(target =>
                target.Path == rendered.Path
                && target.MappingId == rendered.MappingId
                && target.ExpectedSha256 == rendered.BeforeSha256));
        return repositoryValid
            && proposalValid
            && renderedValid
            && targetsCorrespond
            && (recovery || proposalTargets.Any(target =>
                target.Operations.Count > 0));
    }

    public static SaveMappingIntegratePresentation IntegrateAvailability(
        SaveMappingIntegrationReview? review,
        string? candidateRecordId)
    {
        if (!ReviewMatches(review, candidateRecordId))
        {
            return new(
                false,
                "review_stale",
                "Select the observation, then review the exact proposal.");
        }
        return new(
            review!.Stage.Available,
            review.Stage.Code ?? "",
            review.Stage.Reason ?? "");
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

    public static string RepositoryText(SaveMappingRepositoryStatus? repository)
    {
        if (repository is null)
        {
            return "Private staging eligibility is unavailable.";
        }
        var readiness = repository.IntegrationAvailable
            ? "Eligible: main is clean and the private staging ref is empty."
            : string.IsNullOrWhiteSpace(repository.Reason)
                ? "Private-ref staging is unavailable."
                : repository.Reason;
        return $"main        {repository.MainCommit}{Environment.NewLine}"
            + $"staging ref {repository.StagingRef}{Environment.NewLine}"
            + $"staged      {repository.StagedCommit ?? "empty"}{Environment.NewLine}"
            + readiness;
    }

    public static string ProposalText(SaveMappingIntegrationReview review)
    {
        var text = new StringBuilder();
        text.AppendLine("REVIEWED PROPOSAL FINGERPRINT");
        text.AppendLine(review.ReviewedProposalFingerprint);
        text.AppendLine();
        text.AppendLine("REPOSITORY SNAPSHOT");
        text.AppendLine($"reviewed base {review.ReviewedBaseCommit}");
        text.AppendLine($"current main  {review.Repository.MainCommit}");
        text.AppendLine($"staging ref   {review.Repository.StagingRef}");
        text.AppendLine($"staged        {review.Repository.StagedCommit ?? "empty"}");
        foreach (var target in Targets(review.Proposal))
        {
            var rendered = (review.RenderedTargets ?? []).FirstOrDefault(item =>
                item.Path == target.Path && item.MappingId == target.MappingId);
            text.AppendLine();
            text.AppendLine($"TARGET {target.MappingId} · {target.Path}");
            text.AppendLine($"base {target.ExpectedSha256}");
            text.AppendLine($"after {rendered?.AfterSha256 ?? "unknown"}");
            text.AppendLine(rendered?.Mode is int mode
                ? $"mode 0{Convert.ToString(mode, 8)}"
                : "mode unknown");
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

    public static SaveMappingIntegratedResultValidation ValidateIntegratedResult(
        SaveMappingIntegratedResult? result,
        SaveMappingIntegrationReview? review)
    {
        var targets = result?.Targets;
        var reviewedTargets = review?.RenderedTargets;
        var targetKeys = targets?
            .Select(target => $"{target.Path}\0{target.MappingId}")
            .ToHashSet(StringComparer.Ordinal);
        var reviewedTargetKeys = reviewedTargets?
            .Select(target => $"{target.Path}\0{target.MappingId}")
            .ToHashSet(StringComparer.Ordinal);
        var valid = result is not null
            && review is not null
            && ReviewMatches(review, review.CandidateRecordId)
            && result.SchemaVersion == 3
            && result.Capability == "save_mapping_staged_candidate_v1"
            && result.Operation == "stage"
            && result.Disposition == "staged_for_promotion"
            && result.Idempotent.HasValue
            && result.CandidateRecordId == review.CandidateRecordId
            && result.ReviewedProposalFingerprint
                == review.ReviewedProposalFingerprint
            && IsGitObject(result.BaseCommit)
            && result.StagingRef == review.Repository.StagingRef
            && IsGitObject(result.StagedCommit)
            && result.Committed is true
            && result.Staged is true
            && result.Promoted.HasValue
            && (result.Promoted is not true || result.Idempotent is true)
            && result.MappingInvariants == "passed"
            && result.PromotionValidation == "pending"
            && targets is { Count: > 0 }
            && targets.All(target =>
                !string.IsNullOrWhiteSpace(target.Path)
                && !string.IsNullOrWhiteSpace(target.MappingId)
                && IsLowerHex64(target.BeforeSha256)
                && IsLowerHex64(target.AfterSha256)
                && target.Changed.HasValue
                && target.Mode.HasValue
                && (target.Mode.Value & Convert.ToInt32("111", 8)) == 0)
            && targets.Any(target => target.Changed is true)
            && reviewedTargets is { Count: > 0 }
            && targets.Count == reviewedTargets.Count
            && targetKeys is not null
            && reviewedTargetKeys is not null
            && targetKeys.Count == targets.Count
            && reviewedTargetKeys.Count == reviewedTargets.Count
            && targetKeys.SetEquals(reviewedTargetKeys)
            && targets.All(target => reviewedTargets.Any(reviewed =>
                target.Path == reviewed.Path
                && target.MappingId == reviewed.MappingId
                && target.BeforeSha256 == reviewed.BeforeSha256
                && target.AfterSha256 == reviewed.AfterSha256
                && target.Changed == reviewed.Changed
                && target.Mode == reviewed.Mode));
        return new(
            valid,
            valid ? "" : "integrated_result_invalid",
            valid
                ? ""
                : "The server response did not prove this exact reviewed proposal was staged without moving main.");
    }

    public static SaveMappingResultPresentation IntegratedResult(
        SaveMappingIntegratedResult? result,
        SaveMappingIntegrationReview? review)
    {
        var validation = ValidateIntegratedResult(
            result,
            review);
        if (!validation.Valid)
        {
            return new(
                false,
                "Staging outcome is unconfirmed",
                validation.Reason
                    + " Refresh the catalog before taking another action.",
                validation.Code);
        }
        var changed = result!.Targets!.Count(target => target.Changed is true);
        var commit = result.StagedCommit[..Math.Min(12, result.StagedCommit.Length)];
        return new(
            true,
            result.Idempotent is true
                ? "Already staged for promotion"
                : "Staged for promotion",
            $"{changed} canonical mapping file{(changed == 1 ? "" : "s")} "
                + $"committed as {commit}. Mapping invariants passed; "
                + (result.Promoted is true
                    ? "a fresh stable decode remains pending."
                    : "production promotion and a fresh stable decode remain pending."),
            "");
    }

    public static string IntegratedResultText(
        SaveMappingIntegratedResult result,
        SaveMappingIntegrationReview review)
    {
        var presentation = IntegratedResult(
            result,
            review);
        var text = new StringBuilder();
        text.AppendLine(presentation.Title);
        text.AppendLine(presentation.Detail);
        if (!presentation.Success)
        {
            return text.ToString().TrimEnd();
        }
        text.AppendLine();
        text.AppendLine($"base: {result.BaseCommit}");
        text.AppendLine($"staging ref: {result.StagingRef}");
        text.AppendLine($"staged commit: {result.StagedCommit}");
        text.AppendLine($"committed: {Lower(result.Committed)}");
        text.AppendLine($"staged: {Lower(result.Staged)}");
        text.AppendLine($"promoted: {Lower(result.Promoted)}");
        text.AppendLine($"mapping invariants: {result.MappingInvariants}");
        text.AppendLine($"production validation: {result.PromotionValidation}");
        foreach (var target in result.Targets!)
        {
            text.AppendLine();
            text.AppendLine($"{target.MappingId} · {target.Path}");
            text.AppendLine(target.BeforeSha256);
            text.AppendLine($"→ {target.AfterSha256}");
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
        bool integrateRequest)
    {
        if (code == "integration_busy")
        {
            return new(
                false,
                "Another integration is in progress",
                message
                    + " This request did not acquire integration authority. "
                    + "Refresh after the active request finishes; do not retry automatically.");
        }
        if (code == "staging_ref_update_failed")
        {
            return new(
                false,
                "Nothing was staged",
                message
                    + " Refresh, verify the same review, and retry once only when directed.");
        }
        var safelyRejected = code is "reviewed_proposal_stale"
            or "production_worktree_dirty"
            or "staging_ref_occupied"
            or "proposal_base_changed";
        if (safelyRejected || !integrateRequest)
        {
            return new(
                false,
                integrateRequest ? "Staging rejected" : "Review unavailable",
                message
                    + " Nothing was staged by this request. Refresh and review again.");
        }
        return new(
            true,
            "Integration outcome is unconfirmed",
            message
                + " Inspect main, the private staging ref, and the durable transaction before "
                + "another action; do not retry automatically.");
    }

    private static bool IsLowerHex64(string? value) =>
        value is { Length: 64 }
        && value.All(character =>
            character is >= '0' and <= '9'
            || character is >= 'a' and <= 'f');

    private static bool IsGitObject(string? value) =>
        value is { Length: >= 40 and <= 64 }
        && value.All(character =>
            character is >= '0' and <= '9'
            || character is >= 'a' and <= 'f');

    private static bool IsMappingTargetPath(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return false;
        }
        const string prefix = "config/player_save_versions/";
        var relative = value.StartsWith(prefix, StringComparison.Ordinal)
            ? value[prefix.Length..]
            : "";
        return relative.Length > ".json".Length
            && !relative.Contains('/')
            && relative.EndsWith(".json", StringComparison.Ordinal);
    }

    private static string Lower(bool? value) =>
        value?.ToString().ToLowerInvariant() ?? "missing";

    private static string Format(string? value) =>
        string.Join(
            " ",
            (value ?? "")
                .Split('_', StringSplitOptions.RemoveEmptyEntries)
                .Select(word => char.ToUpperInvariant(word[0]) + word[1..]));
}
