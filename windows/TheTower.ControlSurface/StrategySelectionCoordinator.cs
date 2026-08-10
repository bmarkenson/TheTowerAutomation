namespace TheTower.ControlSurface;

internal enum StrategyRequestKind
{
    NextBoundary,
    StartupDefault,
    ActiveBattle,
}

internal enum StrategyRequestOrigin
{
    UserSelection,
    PublishedRevision,
    Retry,
    StartupSave,
    ActiveAdoption,
}

internal sealed record StrategySelectionContext(
    bool LifecycleAvailable,
    bool ProcessActive,
    string? ConfiguredStrategy,
    string? CurrentStrategy,
    string? RequestedStrategy,
    string? PendingStrategy,
    string ApplyMode);

internal sealed record StrategyPublicationNotice(
    string StrategyId,
    int LogicalVersion);

internal sealed record StrategyRequestAttempt(
    long Token,
    string Strategy,
    StrategyRequestKind Kind,
    StrategyRequestOrigin Origin,
    bool DirtyBeforeRequest,
    StrategyPublicationNotice? Publication = null)
{
    public bool ApplyToActiveRun => Kind == StrategyRequestKind.ActiveBattle;

    public bool IsAutomaticNextBoundary =>
        Kind == StrategyRequestKind.NextBoundary
        && Origin is StrategyRequestOrigin.UserSelection
            or StrategyRequestOrigin.PublishedRevision
            or StrategyRequestOrigin.Retry;
}

internal sealed record StrategyRequestOutcome(bool Accepted, string Message);

internal sealed record StrategyPublicationUseResult(bool Succeeded, string Message);

internal sealed class StrategySelectionCoordinator
{
    private long _nextToken;
    private StrategyRequestAttempt? _inFlight;
    private string? _failedNextBoundaryStrategy;
    private StrategyPublicationNotice? _deferredPublication;
    private readonly HashSet<StrategyPublicationNotice> _handledPublications = [];

    public bool Dirty { get; private set; }

    public bool RequestInFlight => _inFlight is not null;

    public bool HasDeferredPublication => _deferredPublication is not null;

    public StrategyRequestAttempt? SelectionChanged(
        StrategySelectionContext context,
        string? selectedStrategy,
        bool userDriven)
    {
        if (!userDriven)
        {
            return null;
        }

        var selected = Normalize(selectedStrategy);
        if (selected is null)
        {
            _deferredPublication = null;
            _failedNextBoundaryStrategy = null;
            Dirty = false;
            return null;
        }
        if (_inFlight is not null)
        {
            return null;
        }
        _deferredPublication = null;
        _failedNextBoundaryStrategy = null;
        if (!context.LifecycleAvailable)
        {
            Dirty = true;
            return null;
        }
        if (!context.ProcessActive)
        {
            Dirty = !Same(selected, context.ConfiguredStrategy);
            return null;
        }
        if (IsNextBoundaryNoOp(context, selected))
        {
            Dirty = false;
            return null;
        }

        Dirty = true;
        return Begin(
            selected,
            StrategyRequestKind.NextBoundary,
            StrategyRequestOrigin.UserSelection);
    }

    public StrategyRequestAttempt? Published(
        StrategySelectionContext context,
        StrategyPublicationNotice publication)
    {
        var normalized = Normalize(publication.StrategyId);
        if (normalized is null)
        {
            return null;
        }
        var notice = publication with { StrategyId = normalized };
        if (!_handledPublications.Add(notice))
        {
            return null;
        }

        _failedNextBoundaryStrategy = null;
        Dirty = true;
        if (_inFlight is not null)
        {
            _deferredPublication = notice;
            return null;
        }
        if (!context.LifecycleAvailable || !context.ProcessActive)
        {
            return null;
        }

        return Begin(
            normalized,
            StrategyRequestKind.NextBoundary,
            StrategyRequestOrigin.PublishedRevision,
            notice);
    }

    public StrategyRequestAttempt? TryBeginDeferredPublication(
        StrategySelectionContext context)
    {
        if (_inFlight is not null || _deferredPublication is null)
        {
            return null;
        }
        var publication = _deferredPublication;
        _deferredPublication = null;
        Dirty = true;
        if (!context.LifecycleAvailable)
        {
            _failedNextBoundaryStrategy = publication.StrategyId;
            return null;
        }
        if (!context.ProcessActive)
        {
            _failedNextBoundaryStrategy = null;
            return null;
        }
        return Begin(
            publication.StrategyId,
            StrategyRequestKind.NextBoundary,
            StrategyRequestOrigin.PublishedRevision,
            publication);
    }

    public StrategyRequestAttempt? TryBeginRetry(
        StrategySelectionContext context,
        string? selectedStrategy)
    {
        var selected = Normalize(selectedStrategy);
        if (_inFlight is not null
            || selected is null
            || !context.LifecycleAvailable
            || !context.ProcessActive
            || !Same(selected, _failedNextBoundaryStrategy))
        {
            return null;
        }

        Dirty = true;
        return Begin(
            selected,
            StrategyRequestKind.NextBoundary,
            StrategyRequestOrigin.Retry);
    }

    public StrategyRequestAttempt? TryBeginStartupSave(
        StrategySelectionContext context,
        string? selectedStrategy)
    {
        var selected = Normalize(selectedStrategy);
        if (_inFlight is not null
            || selected is null
            || !context.LifecycleAvailable
            || context.ProcessActive)
        {
            return null;
        }
        if (Same(selected, context.ConfiguredStrategy))
        {
            Dirty = false;
            return null;
        }

        Dirty = true;
        return Begin(
            selected,
            StrategyRequestKind.StartupDefault,
            StrategyRequestOrigin.StartupSave);
    }

    public StrategyRequestAttempt? TryBeginActiveAdoption(
        StrategySelectionContext context,
        string? selectedStrategy)
    {
        var selected = Normalize(selectedStrategy);
        if (_inFlight is not null
            || selected is null
            || !context.LifecycleAvailable
            || !context.ProcessActive
            || Same(selected, context.CurrentStrategy)
            || (context.PendingStrategy is not null
                && Same(selected, context.RequestedStrategy)
                && string.Equals(
                    context.ApplyMode,
                    "active_battle",
                    StringComparison.OrdinalIgnoreCase)))
        {
            return null;
        }

        return Begin(
            selected,
            StrategyRequestKind.ActiveBattle,
            StrategyRequestOrigin.ActiveAdoption);
    }

    public bool CompleteAccepted(
        StrategyRequestAttempt attempt,
        string? selectedStrategy)
    {
        if (!MatchesInFlight(attempt))
        {
            return false;
        }

        _inFlight = null;
        _failedNextBoundaryStrategy = null;
        if (Same(attempt.Strategy, selectedStrategy)
            && _deferredPublication is null)
        {
            Dirty = false;
        }
        return true;
    }

    public bool CompleteFailed(
        StrategyRequestAttempt attempt,
        string? selectedStrategy)
    {
        if (!MatchesInFlight(attempt))
        {
            return false;
        }

        _inFlight = null;
        if (attempt.IsAutomaticNextBoundary
            && Same(attempt.Strategy, selectedStrategy)
            && _deferredPublication is null)
        {
            Dirty = true;
            _failedNextBoundaryStrategy = attempt.Strategy;
        }
        else
        {
            Dirty = attempt.DirtyBeforeRequest
                || _deferredPublication is not null;
        }
        return true;
    }

    public bool RetryAvailable(string? selectedStrategy) =>
        _inFlight is null
        && Same(selectedStrategy, _failedNextBoundaryStrategy);

    public bool HasHandledPublication(StrategyPublicationNotice publication)
    {
        var normalized = Normalize(publication.StrategyId);
        return normalized is null
            || _handledPublications.Contains(
                publication with { StrategyId = normalized });
    }

    public void MarkAutomaticFailure(string? selectedStrategy)
    {
        var selected = Normalize(selectedStrategy);
        if (_inFlight is not null || selected is null)
        {
            return;
        }
        Dirty = true;
        _failedNextBoundaryStrategy = selected;
    }

    public void MarkExternalAcceptance(string? selectedStrategy)
    {
        if (_inFlight is not null)
        {
            return;
        }
        _deferredPublication = null;
        _failedNextBoundaryStrategy = null;
        if (Normalize(selectedStrategy) is not null)
        {
            Dirty = false;
        }
    }

    public static bool IsNextBoundaryNoOp(
        StrategySelectionContext context,
        string? selectedStrategy)
    {
        var selected = Normalize(selectedStrategy);
        if (selected is null)
        {
            return true;
        }
        if (!context.ProcessActive)
        {
            return Same(selected, context.ConfiguredStrategy);
        }
        if (context.PendingStrategy is not null)
        {
            return Same(selected, context.RequestedStrategy)
                && string.Equals(
                    context.ApplyMode,
                    "next_boundary",
                    StringComparison.OrdinalIgnoreCase);
        }
        return Same(selected, context.CurrentStrategy);
    }

    private StrategyRequestAttempt Begin(
        string strategy,
        StrategyRequestKind kind,
        StrategyRequestOrigin origin,
        StrategyPublicationNotice? publication = null)
    {
        var attempt = new StrategyRequestAttempt(
            ++_nextToken,
            strategy,
            kind,
            origin,
            Dirty,
            publication);
        _inFlight = attempt;
        return attempt;
    }

    private bool MatchesInFlight(StrategyRequestAttempt attempt) =>
        _inFlight?.Token == attempt.Token;

    private static bool Same(string? first, string? second) =>
        string.Equals(
            Normalize(first),
            Normalize(second),
            StringComparison.OrdinalIgnoreCase);

    private static string? Normalize(string? strategy)
    {
        var value = strategy?.Trim().ToLowerInvariant();
        return string.IsNullOrWhiteSpace(value) ? null : value;
    }
}
