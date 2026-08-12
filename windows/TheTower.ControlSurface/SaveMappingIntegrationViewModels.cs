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
            || review.SchemaVersion != 2
            || review.Capability != "save_mapping_develop_integration_v1"
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
            && IsGitObject(repository.DevelopCommit)
            && !string.IsNullOrWhiteSpace(repository.DevelopPath)
            && (recovery || review.ReviewedBaseCommit == repository.MainCommit)
            && (recovery
                ? !repository.IntegrationAvailable
                    && repository.Code == "transaction_recovery_required"
                : repository.IntegrationAvailable
                    && repository.Synchronized
                    && repository.ProductionClean
                    && repository.DevelopClean
                    && repository.MainCommit == repository.DevelopCommit);
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
            review!.Integrate.Available,
            review.Integrate.Code ?? "",
            review.Integrate.Reason ?? "");
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
            return "Develop eligibility is unavailable.";
        }
        var readiness = repository.IntegrationAvailable
            ? "Eligible: main and develop are clean and synchronized."
            : string.IsNullOrWhiteSpace(repository.Reason)
                ? "Direct develop integration is unavailable."
                : repository.Reason;
        return $"main    {repository.MainCommit}{Environment.NewLine}"
            + $"develop {repository.DevelopCommit}{Environment.NewLine}"
            + $"path    {repository.DevelopPath}{Environment.NewLine}"
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
        text.AppendLine($"main         {review.Repository.MainCommit}");
        text.AppendLine($"develop      {review.Repository.DevelopCommit}");
        text.AppendLine($"synchronized {review.Repository.Synchronized}");
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
            && result.SchemaVersion == 2
            && result.Capability == "save_mapping_develop_integration_v1"
            && result.Operation == "integrate"
            && result.Disposition == "committed_to_develop"
            && result.Idempotent.HasValue
            && result.CandidateRecordId == review.CandidateRecordId
            && result.ReviewedProposalFingerprint
                == review.ReviewedProposalFingerprint
            && result.BaseCommit == review.ReviewedBaseCommit
            && IsGitObject(result.IntegrationCommit)
            && result.DevelopCommit == result.IntegrationCommit
            && result.Committed is true
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
                : "The server response did not prove this exact reviewed proposal was committed to develop.");
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
                "Integration outcome is unconfirmed",
                validation.Reason
                    + " Refresh the catalog before taking another action.",
                validation.Code);
        }
        var changed = result!.Targets!.Count(target => target.Changed is true);
        var commit = result.IntegrationCommit[..Math.Min(12, result.IntegrationCommit.Length)];
        return new(
            true,
            result.Idempotent is true
                ? "Already committed to develop"
                : "Committed to develop",
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
        text.AppendLine($"commit: {result.IntegrationCommit}");
        text.AppendLine($"committed: {Lower(result.Committed)}");
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
        if (code == "develop_fast_forward_failed")
        {
            return new(
                false,
                "Develop remained unchanged",
                message
                    + " Refresh, verify the same review, and retry once only when directed.");
        }
        var safelyRejected = code is "reviewed_proposal_stale"
            or "develop_worktree_dirty"
            or "production_worktree_dirty"
            or "repository_not_synchronized"
            or "proposal_base_changed";
        if (safelyRejected || !integrateRequest)
        {
            return new(
                false,
                integrateRequest ? "Integration rejected" : "Review unavailable",
                message
                    + " Nothing was committed by this request. Refresh and review again.");
        }
        return new(
            true,
            "Integration outcome is unconfirmed",
            message
                + " Inspect main, develop, and the durable transaction before "
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
