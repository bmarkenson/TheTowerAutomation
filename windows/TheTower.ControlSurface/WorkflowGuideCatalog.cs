namespace TheTower.ControlSurface;

internal static class WorkflowGuideIds
{
    public const string Controls = "controls";
    public const string MoveEmulator = "move-emulator";
    public const string RestartBlueStacks = "restart-bluestacks";
    public const string EditStrategy = "edit-strategy";
}

internal enum WorkflowGuideDestination
{
    Overview,
    Connections,
    Diagnostics,
    StrategyProfiles,
}

internal enum WorkflowGuideSectionTone
{
    Normal,
    Caution,
    Success,
}

internal sealed record WorkflowGuideItem(string Marker, string Text);

internal sealed record WorkflowGuideSection(
    string Heading,
    string Introduction,
    IReadOnlyList<WorkflowGuideItem> Items,
    WorkflowGuideSectionTone Tone = WorkflowGuideSectionTone.Normal);

internal sealed record WorkflowGuide(
    string Id,
    string Title,
    string Summary,
    string NavigationLabel,
    WorkflowGuideDestination Destination,
    string CanonicalSource,
    IReadOnlyList<WorkflowGuideSection> Sections);

internal static class WorkflowGuideCatalog
{
    public static IReadOnlyList<WorkflowGuide> All { get; } = Array.AsReadOnly(
    [
        new WorkflowGuide(
            WorkflowGuideIds.Controls,
            "How the controls fit together",
            "The process, action authority, battle workflow, emulator path, and Strategy are separate decisions. Read this first when two controls appear to disagree.",
            "Open Overview",
            WorkflowGuideDestination.Overview,
            "Control Surface Architecture → Current GUI capabilities",
            [
                Bullets(
                    "Independent layers",
                    "Process lifecycle — Start or Completely stop only the fixed Linux automation process. Start Automation launches Paused and does not choose a battle.",
                    "Action authority — Automation Paused means zero automated device input while capture and observation continue. Automation Enabled permits guarded actions; it does not claim that the game is on a battle screen.",
                    "Battle workflow — Start Battle and Attach to Battle are explicit, screen-specific intents. A running process never invents one from an old battle or a familiar screen.",
                    "Connections — Linux service, HTTP, API SSH, ADB SSH, the managed ADB target, and the selected Windows emulator host are independently observed.",
                    "Strategy scope — Current names the Strategy for this battle. Next names a queued next-boundary or next-start choice. Publishing never switches the current battle.",
                    "Evidence — Screen Age and the current screen describe observation freshness. They do not replace acknowledgement, target ownership, or save-backed battle identity."),
                Steps(
                    "A safe decision sequence",
                    "Identify the layer you intend to change: process, authority, battle, connection, emulator host, or Strategy.",
                    "Use the narrow control for that layer and leave unrelated layers unchanged.",
                    "Wait for the corresponding acknowledgement or completed workflow, not merely a successful button click.",
                    "Verify fresh screen, target, and warning state before enabling automated input."),
                Caution(
                    "Do not infer across layers",
                    "A green tunnel does not prove a usable game frame. A live process does not prove Automation Enabled. A familiar port does not prove which PC owns it. A selected Strategy does not prove that the current battle changed."),
                Success(
                    "Success looks like",
                    "The requested layer reports its completed or acknowledged state, the other layers retain their prior state, and current evidence has no ownership or freshness warning."),
            ]),
        new WorkflowGuide(
            WorkflowGuideIds.MoveEmulator,
            "Move the emulator between PCs",
            "Move a running battle to another Windows PC without replacing the Linux automation process or manufacturing a new battle boundary.",
            "Open Connections",
            WorkflowGuideDestination.Connections,
            "Managed Runtime Operations → Move the emulator between Windows PCs",
            [
                Bullets(
                    "Before you begin",
                    "The destination PC has the matching complete package: TheTower.ControlSurface.exe and TheTower.TunnelHost.exe are adjacent.",
                    "Passwordless SSH, host-key trust, Linux destination, API ports, Windows BlueStacks listener port, and the chosen Linux ADB-forward port are already configured on the destination.",
                    "Choose whether each PC has a distinct Linux port or both reuse one. Reusing a port requires the source ADB forward to release it before the destination can bind it.",
                    "A mid-battle move is supported, but the completed battle becomes mixed-host and is excluded from host-specific performance baselines."),
                Steps(
                    "On the source PC",
                    "Select indefinite Automation Paused. Do not use a timed Pause.",
                    "Wait until the runtime reports that exact Pause request as acknowledged and action authority says Automation Paused.",
                    "Open System → Connections, select Stop ADB forward, and wait until the ADB reverse-forward panel says Stopped. The API tunnel may remain active.",
                    "Only after Pause acknowledgement and forward release, close The Tower, BlueStacks, or the source Control Surface if desired."),
                Steps(
                    "On the destination PC",
                    "Start BlueStacks, launch The Tower, and wait for the intended current game screen to be fully visible.",
                    "Open the Control Surface and System → Connections. Require API SSH Active, HTTP Connected, and a compatible Linux service.",
                    "Verify the configured Linux-port → Windows-port mapping. If the ADB panel retains a stale or wrong desired endpoint, Stop it before adopting corrected Preferences.",
                    "Select Start ADB forward and wait for ADB SSH Active on the exact intended endpoint.",
                    "Reconfirm that indefinite Pause remains acknowledged.",
                    "Select Use this PC's emulator.",
                    "Wait for the selected host to name this PC and say acknowledged. Require the intended active target, a fresh correct screen and Screen Age, and no handoff warning.",
                    "Select Automation Enabled only when normal automated input should resume, then wait for acknowledgement."),
                Bullets(
                    "If something is blocked",
                    "Conflict or ‘remote port forwarding failed’ usually means another forward still owns the Linux port. Release that exact forward or choose another free port; never displace an unidentified listener.",
                    "If Preferences are correct but the panel shows an old endpoint, Stop the desired forward, wait for Stopped, then Start it again.",
                    "ADB SSH Active or transport device is not sufficient when the selected host is old or the visible screen is stale. Keep Pause and repeat explicit host selection after correcting the evidence."),
                Caution(
                    "Safety boundaries",
                    "Do not Completely stop automation, request Attach, or Surrender merely to move PCs. Closing BlueStacks or the GUI does not release a desired ADB forward; Stop ADB forward does."),
                Success(
                    "Success looks like",
                    "The destination host and exact port mapping are acknowledged, status shows a fresh expected screen, Automation Enabled is acknowledged, and the existing Linux process continues with its current battle and Strategy state."),
            ]),
        new WorkflowGuide(
            WorkflowGuideIds.RestartBlueStacks,
            "When and how to restart BlueStacks",
            "Use the coordinated restart only for persistent, corroborated emulator degradation or an operator-confirmed recovery need—not for one noisy measurement or the wrong connection layer.",
            "Open Diagnostics",
            WorkflowGuideDestination.Diagnostics,
            "Native Windows Control Surface → BlueStacks recovery; Control Surface Architecture → Automatic BlueStacks degradation recovery",
            [
                Bullets(
                    "Evidence worth acting on",
                    "System → Diagnostics explicitly reports an eligible restart or would trigger (disabled) for the exact current BlueStacks listener lifetime.",
                    "The preventive lane has a sustained ten-minute handle median of at least 25,000 and at least +10,000 above the retained low-water for that exact listener lifetime.",
                    "The severe in-run lane has three consecutive save-backed intervals at or below 60% of the tolerant lower envelope at normal effective speed, with handle corroboration.",
                    "A completed-run comparison independently confirms the same degradation on the same attributed host and compatible configuration."),
                Bullets(
                    "What is not enough by itself",
                    "One slow interval, one OCR anomaly, or one high handle reading.",
                    "High CPU, GPU, or memory pressure from another Windows process. That invalidates attribution and defers preventive recovery.",
                    "API SSH, HTTP, ADB SSH, or Screen Age trouble. Repair the failing service, tunnel, or observation layer before blaming BlueStacks.",
                    "An unidentified HD-Player.exe process or a different BlueStacks instance."),
                Bullets(
                    "Before restarting",
                    "Preferences name the absolute HD-Player.exe path, exact instance name, and that instance's real Windows ADB listener port.",
                    "Diagnostics names one unambiguous listener owner and the intended running Farm battle. Hover a disabled Restart button for the current safety blocker.",
                    "Understand that the operator button bypasses the performance decision, automatic opt-in, cooldown, and once-per-battle creation gates only. Linux still requires fresh exact-owner runtime and target authority."),
                Steps(
                    "Coordinated restart",
                    "Open System → Diagnostics and review the detector verdict, listener identity, handle trend, other-process contention, and any active maintenance phase.",
                    "Select Restart BlueStacks… and read the confirmation, including the possible non-earning replay to the old wave high-water and the End run/new-battle fallback.",
                    "Confirm only if the named executable, instance, port, process, target, and battle are the intended ones.",
                    "Leave the client open while the coordinator installs its no-input hold, replaces only the verified process, reconnects ADB, launches The Tower, and handles Welcome Back.",
                    "Wait for maintenance completion, a fresh expected screen and Screen Age, the intended target, and normal acknowledged action authority. Review any explicit degraded or failed result before intervening."),
                Caution(
                    "Avoid competing recovery",
                    "Do not kill a generic BlueStacks PID, restart tunnels speculatively, or replay the button after a lost response. The coordinator retains the exact process and request identities and reconciles uncertain responses."),
                Success(
                    "Success looks like",
                    "Diagnostics reports completed recovery, the replacement listener identity is different and acknowledged, The Tower is freshly observed on the intended target, and the retained or successor battle has an explicit recovery outcome."),
            ]),
        new WorkflowGuide(
            WorkflowGuideIds.EditStrategy,
            "Edit and publish a Strategy",
            "Use Strategy Authoring to change sparse, reviewed configuration. Editing and publishing never repair or switch the current battle.",
            "Open Strategy Profiles",
            WorkflowGuideDestination.StrategyProfiles,
            "Strategy Authoring Architecture → GUI contract; Native Windows Control Surface → Strategy profiles",
            [
                Bullets(
                    "Know the objects",
                    "A Base is a reusable sparse component and can never be activated. Publishing a Base creates a new immutable revision.",
                    "A Strategy is the activatable profile. Bundled Strategies are read-only; clone one before editing. Custom Strategies retain immutable publication history.",
                    "Current is the Strategy attached to this battle. Next is the queued next-boundary or next-start selection."),
                Steps(
                    "Edit and publish",
                    "Open Tools → Strategy profiles….",
                    "Select an editable custom Strategy, reopen a captured draft, or Clone Strategy from a bundled read-only profile.",
                    "Use Show active only for the compact view or Show all settings to expose omitted settings.",
                    "Choose each setting's source policy: inherit, enforce, observe, ignore, or reset to inherited where the server allows it. Use the managed editor for ordered lists, presets, toggles, Modules, Target Priority, and Orb Distance.",
                    "Select Validate draft. Resolve validation, dependency, stale-catalog, or Base-review errors without discarding the open draft.",
                    "Select Review & Publish… and inspect source changes, effective changes, Base effects, validation, and fingerprints before confirming.",
                    "After publication, verify the new Strategy is selected and its next-boundary request is accepted. The current battle must remain unchanged."),
                Bullets(
                    "Related workflows",
                    "Capture current setup as… creates an unpublished Strategy draft or immutable Module preset. It never selects, queues, applies, or publishes the captured setup.",
                    "Edit a copy… materializes a shared preset into this open profile's local draft only. Validate and publish are still required.",
                    "History compares immutable custom revisions. Restore publishes reviewed historical intent as a new latest revision rather than moving an old revision."),
                Caution(
                    "Publication boundary",
                    "Publishing a Strategy while automation is active queues ordinary next-boundary use; while stopped it selects the Strategy for Start Automation. It never switches the current battle. Publishing a Base submits no control request."),
                Success(
                    "Success looks like",
                    "The review succeeds, a new immutable revision is published, the catalog refreshes, and the client clearly distinguishes the unchanged Current Strategy from the accepted Next selection."),
            ]),
    ]);

    public static WorkflowGuide Get(string id) =>
        All.FirstOrDefault(guide => string.Equals(
            guide.Id,
            id,
            StringComparison.Ordinal))
        ?? throw new ArgumentOutOfRangeException(nameof(id), id, "Unknown guide ID");

    private static WorkflowGuideSection Bullets(
        string heading,
        params string[] items) =>
        new(
            heading,
            "",
            Array.AsReadOnly(items
                .Select(item => new WorkflowGuideItem("•", item))
                .ToArray()));

    private static WorkflowGuideSection Steps(
        string heading,
        params string[] items) =>
        new(
            heading,
            "",
            Array.AsReadOnly(items
                .Select((item, index) => new WorkflowGuideItem(
                    $"{index + 1}.",
                    item))
                .ToArray()));

    private static WorkflowGuideSection Caution(
        string heading,
        string text) =>
        new(
            heading,
            text,
            Array.Empty<WorkflowGuideItem>(),
            WorkflowGuideSectionTone.Caution);

    private static WorkflowGuideSection Success(
        string heading,
        string text) =>
        new(
            heading,
            text,
            Array.Empty<WorkflowGuideItem>(),
            WorkflowGuideSectionTone.Success);
}
