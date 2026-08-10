namespace TheTower.ControlSurface;

public sealed class StrategySelectionCoordinatorTests
{
    [Fact]
    public void ActiveUserSelectionBeginsExactlyOneNextBoundaryRequest()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t18");

        var attempt = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true);
        var duplicate = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true);

        Assert.NotNull(attempt);
        Assert.Equal(StrategyRequestKind.NextBoundary, attempt!.Kind);
        Assert.Equal(StrategyRequestOrigin.UserSelection, attempt.Origin);
        Assert.False(attempt.ApplyToActiveRun);
        Assert.Null(duplicate);
        Assert.True(coordinator.RequestInFlight);
    }

    [Fact]
    public void ProgrammaticSelectionDoesNotDirtyOrSubmit()
    {
        var coordinator = new StrategySelectionCoordinator();

        var attempt = coordinator.SelectionChanged(
            Active(current: "farm_t18"),
            "farm_t19",
            userDriven: false);

        Assert.Null(attempt);
        Assert.False(coordinator.Dirty);
        Assert.False(coordinator.RequestInFlight);
    }

    [Fact]
    public void AcceptedMatchingRequestClearsDirtyState()
    {
        var coordinator = new StrategySelectionCoordinator();
        var attempt = coordinator.SelectionChanged(
            Active(current: "farm_t18"),
            "farm_t19",
            userDriven: true)!;

        Assert.True(coordinator.CompleteAccepted(attempt, "farm_t19"));

        Assert.False(coordinator.Dirty);
        Assert.False(coordinator.RequestInFlight);
        Assert.False(coordinator.RetryAvailable("farm_t19"));
    }

    [Fact]
    public void FailedAutomaticRequestPreservesDirtySelectionAndEnablesRetry()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t18");
        var attempt = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true)!;

        Assert.True(coordinator.CompleteFailed(attempt, "farm_t19"));

        Assert.True(coordinator.Dirty);
        Assert.True(coordinator.RetryAvailable("farm_t19"));
    }

    [Fact]
    public void RetryCanBeginOnlyOnce()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t18");
        var failed = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true)!;
        coordinator.CompleteFailed(failed, "farm_t19");

        var retry = coordinator.TryBeginRetry(context, "farm_t19");
        var duplicate = coordinator.TryBeginRetry(context, "farm_t19");

        Assert.NotNull(retry);
        Assert.Equal(StrategyRequestOrigin.Retry, retry!.Origin);
        Assert.Null(duplicate);
    }

    [Fact]
    public void FailedRetryRemainsRetryableAndAcceptedRetryClearsIntent()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t18");
        var first = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true)!;
        coordinator.CompleteFailed(first, "farm_t19");
        var failedRetry = coordinator.TryBeginRetry(context, "farm_t19")!;

        coordinator.CompleteFailed(failedRetry, "farm_t19");

        Assert.True(coordinator.RetryAvailable("farm_t19"));
        var acceptedRetry = coordinator.TryBeginRetry(context, "farm_t19")!;
        coordinator.CompleteAccepted(acceptedRetry, "farm_t19");
        Assert.False(coordinator.RetryAvailable("farm_t19"));
        Assert.False(coordinator.Dirty);
    }

    [Fact]
    public void SelectingCurrentReplacesDifferentPendingRequest()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(
            current: "farm_t18",
            requested: "farm_t19",
            pending: "farm_t19");

        var attempt = coordinator.SelectionChanged(
            context,
            "farm_t18",
            userDriven: true);

        Assert.NotNull(attempt);
        Assert.Equal("farm_t18", attempt!.Strategy);
    }

    [Theory]
    [InlineData("farm_t18", null, null, "next_boundary")]
    [InlineData("farm_t19", "farm_t19", "farm_t19", "next_boundary")]
    public void AlreadyCurrentOrAlreadyPendingSelectionsAreNoOps(
        string selected,
        string? requested,
        string? pending,
        string applyMode)
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(
            current: "farm_t18",
            requested: requested,
            pending: pending,
            applyMode: applyMode);

        var attempt = coordinator.SelectionChanged(
            context,
            selected,
            userDriven: true);

        Assert.Null(attempt);
        Assert.False(coordinator.Dirty);
    }

    [Fact]
    public void ActiveAdoptionRequiresExplicitSwitchAndSetsApplyToActiveRun()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t18");

        Assert.False(coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true)!.ApplyToActiveRun);

        var queued = new StrategySelectionCoordinator();
        var adoption = queued.TryBeginActiveAdoption(context, "farm_t19");

        Assert.NotNull(adoption);
        Assert.True(adoption!.ApplyToActiveRun);
        Assert.Equal(StrategyRequestOrigin.ActiveAdoption, adoption.Origin);
    }

    [Fact]
    public void FailedActiveAdoptionDoesNotCreateBoundaryRetry()
    {
        var coordinator = new StrategySelectionCoordinator();
        var adoption = coordinator.TryBeginActiveAdoption(
            Active(current: "farm_t18"),
            "farm_t19")!;

        coordinator.CompleteFailed(adoption, "farm_t19");

        Assert.False(coordinator.Dirty);
        Assert.False(coordinator.RetryAvailable("farm_t19"));
    }

    [Fact]
    public void StoppedSelectionIsDraftUntilExplicitStartupSave()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Stopped(configured: "farm_t18");

        var automatic = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true);

        Assert.Null(automatic);
        Assert.True(coordinator.Dirty);

        var saved = coordinator.TryBeginStartupSave(context, "farm_t19");
        Assert.NotNull(saved);
        Assert.Equal(StrategyRequestKind.StartupDefault, saved!.Kind);
    }

    [Fact]
    public void SameIdPublicationForcesNextBoundaryRequest()
    {
        var coordinator = new StrategySelectionCoordinator();

        var attempt = coordinator.Published(
            Active(current: "farm_t19"),
            new StrategyPublicationNotice("farm_t19", 2));

        Assert.NotNull(attempt);
        Assert.Equal(StrategyRequestKind.NextBoundary, attempt!.Kind);
        Assert.Equal(StrategyRequestOrigin.PublishedRevision, attempt.Origin);
    }

    [Fact]
    public void StoppedPublicationSelectsForStartWithoutSaving()
    {
        var coordinator = new StrategySelectionCoordinator();

        var attempt = coordinator.Published(
            Stopped(configured: "farm_t18"),
            new StrategyPublicationNotice("farm_t19_custom", 1));

        Assert.Null(attempt);
        Assert.True(coordinator.Dirty);
        Assert.False(coordinator.RequestInFlight);
    }

    [Fact]
    public void UnavailableAutomaticPublicationCanExposeRetryLater()
    {
        var coordinator = new StrategySelectionCoordinator();
        var unavailable = Active(current: "farm_t18") with
        {
            LifecycleAvailable = false,
        };

        Assert.Null(coordinator.Published(
            unavailable,
            new StrategyPublicationNotice("farm_t19", 2)));
        coordinator.MarkAutomaticFailure("farm_t19");

        Assert.True(coordinator.Dirty);
        Assert.True(coordinator.RetryAvailable("farm_t19"));
        Assert.NotNull(coordinator.TryBeginRetry(
            Active(current: "farm_t18"),
            "farm_t19"));
    }

    [Fact]
    public void ProgrammaticPollingCannotOverwriteFailedIntent()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t18");
        var failed = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true)!;
        coordinator.CompleteFailed(failed, "farm_t19");

        var pollingAttempt = coordinator.SelectionChanged(
            context,
            "farm_t18",
            userDriven: false);

        Assert.Null(pollingAttempt);
        Assert.True(coordinator.Dirty);
        Assert.True(coordinator.RetryAvailable("farm_t19"));
    }

    [Fact]
    public void StaleCompletionCannotClearNewerDirtySelection()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t18");
        var first = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true)!;
        coordinator.CompleteFailed(first, "farm_t19");
        var retry = coordinator.TryBeginRetry(context, "farm_t19")!;

        Assert.False(coordinator.CompleteAccepted(first, "farm_t19"));
        Assert.True(coordinator.Dirty);
        Assert.True(coordinator.RequestInFlight);

        Assert.True(coordinator.CompleteAccepted(retry, "farm_t19"));
        Assert.False(coordinator.Dirty);
    }

    [Fact]
    public void DuplicatePublicationNoticeIsIgnoredButLaterVersionQueues()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t19");
        var versionTwo = new StrategyPublicationNotice("farm_t19", 2);
        var first = coordinator.Published(context, versionTwo)!;
        coordinator.CompleteAccepted(first, "farm_t19");

        Assert.True(coordinator.HasHandledPublication(versionTwo));
        Assert.Null(coordinator.Published(context, versionTwo));
        Assert.NotNull(coordinator.Published(
            context,
            new StrategyPublicationNotice("farm_t19", 3)));
    }

    [Fact]
    public void EarlierHandledPublicationRemainsDuplicateAfterLaterVersion()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t19");
        var versionTwo = new StrategyPublicationNotice("farm_t19", 2);
        var versionThree = new StrategyPublicationNotice("farm_t19", 3);
        var first = coordinator.Published(context, versionTwo)!;
        coordinator.CompleteAccepted(first, "farm_t19");
        var second = coordinator.Published(context, versionThree)!;
        coordinator.CompleteAccepted(second, "farm_t19");

        Assert.True(coordinator.HasHandledPublication(versionTwo));
        Assert.Null(coordinator.Published(context, versionTwo));
        Assert.False(coordinator.RequestInFlight);
    }

    [Fact]
    public void PublicationDuringInflightRequestCoalescesToLatestRevision()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t18");
        var selection = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true)!;

        Assert.Null(coordinator.Published(
            context,
            new StrategyPublicationNotice("farm_t19", 2)));
        Assert.Null(coordinator.Published(
            context,
            new StrategyPublicationNotice("farm_t19", 3)));
        Assert.True(coordinator.HasDeferredPublication);

        coordinator.CompleteAccepted(selection, "farm_t19");
        var publication = coordinator.TryBeginDeferredPublication(context);

        Assert.NotNull(publication);
        Assert.Equal(3, publication!.Publication!.LogicalVersion);
        Assert.Equal(StrategyRequestOrigin.PublishedRevision, publication.Origin);
    }

    [Fact]
    public void DeferredPublicationBecomesStoppedDirtySelectionWithoutStranding()
    {
        var coordinator = new StrategySelectionCoordinator();
        var active = Active(current: "farm_t18");
        var selection = coordinator.SelectionChanged(
            active,
            "farm_t19",
            userDriven: true)!;
        coordinator.Published(
            active,
            new StrategyPublicationNotice("farm_t19", 2));
        coordinator.CompleteAccepted(selection, "farm_t19");

        var deferred = coordinator.TryBeginDeferredPublication(
            Stopped(configured: "farm_t18"));

        Assert.Null(deferred);
        Assert.False(coordinator.HasDeferredPublication);
        Assert.True(coordinator.Dirty);
        Assert.False(coordinator.RetryAvailable("farm_t19"));

        coordinator.MarkExternalAcceptance("farm_t19");
        Assert.False(coordinator.Dirty);
        Assert.False(coordinator.HasDeferredPublication);
    }

    [Fact]
    public void DeferredPublicationUnavailableBecomesRetryable()
    {
        var coordinator = new StrategySelectionCoordinator();
        var active = Active(current: "farm_t18");
        var selection = coordinator.SelectionChanged(
            active,
            "farm_t19",
            userDriven: true)!;
        coordinator.Published(
            active,
            new StrategyPublicationNotice("farm_t19", 2));
        coordinator.CompleteAccepted(selection, "farm_t19");

        var deferred = coordinator.TryBeginDeferredPublication(
            active with { LifecycleAvailable = false });

        Assert.Null(deferred);
        Assert.False(coordinator.HasDeferredPublication);
        Assert.True(coordinator.Dirty);
        Assert.True(coordinator.RetryAvailable("farm_t19"));
    }

    [Fact]
    public void NewUserSelectionSupersedesDeferredPublication()
    {
        var coordinator = new StrategySelectionCoordinator();
        var context = Active(current: "farm_t18");
        var selection = coordinator.SelectionChanged(
            context,
            "farm_t19",
            userDriven: true)!;
        coordinator.Published(
            context,
            new StrategyPublicationNotice("farm_t19", 2));
        coordinator.CompleteAccepted(selection, "farm_t19");

        var replacement = coordinator.SelectionChanged(
            context,
            "tournament",
            userDriven: true);

        Assert.NotNull(replacement);
        Assert.Equal("tournament", replacement!.Strategy);
        Assert.Equal(StrategyRequestOrigin.UserSelection, replacement.Origin);
        Assert.False(coordinator.HasDeferredPublication);
    }

    private static StrategySelectionContext Active(
        string current,
        string? requested = null,
        string? pending = null,
        string applyMode = "next_boundary") =>
        new(
            LifecycleAvailable: true,
            ProcessActive: true,
            ConfiguredStrategy: current,
            CurrentStrategy: current,
            RequestedStrategy: requested ?? current,
            PendingStrategy: pending,
            ApplyMode: applyMode);

    private static StrategySelectionContext Stopped(string configured) =>
        new(
            LifecycleAvailable: true,
            ProcessActive: false,
            ConfiguredStrategy: configured,
            CurrentStrategy: null,
            RequestedStrategy: configured,
            PendingStrategy: null,
            ApplyMode: "next_boundary");
}
