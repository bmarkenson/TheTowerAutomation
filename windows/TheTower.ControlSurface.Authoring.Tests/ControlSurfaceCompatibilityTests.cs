namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class ControlSurfaceCompatibilityTests
{
    private static StatusResponse Status(
        int revision,
        params string[] capabilities)
    {
        return new StatusResponse
        {
            ApiVersion = 1,
            ServerRevision = revision,
            Capabilities = capabilities.ToList(),
        };
    }

    [Fact]
    public void BetterControlActionsRejectOldServerRevision()
    {
        var result = ControlSurfaceCompatibility.Evaluate(
            Status(
                31,
                "better_control_model_v2",
                "save_backed_setup_capture_v2")
        );

        Assert.False(result.IsCompatible);
        Assert.False(result.ServerRevisionSupported);
    }

    [Fact]
    public void BetterControlActionsRejectMissingCapability()
    {
        var status = Status(34);
        var result = ControlSurfaceCompatibility.Evaluate(status);

        Assert.False(result.IsCompatible);
        Assert.Contains("better_control_model_v2", result.MissingCapabilities);
        Assert.Contains("current_battle_perks_v1", result.MissingCapabilities);
        Assert.Contains(
            "confirmed_local_mapping_status_v1",
            result.MissingCapabilities);
        Assert.Contains(
            "save_backed_setup_capture_v2",
            result.MissingCapabilities);
        Assert.Contains(
            "save_mapping_review_status_v1",
            result.MissingCapabilities);
        Assert.Contains(
            "strategy_authoring_preset_local_copy_v1",
            result.MissingCapabilities);
    }

    [Fact]
    public void SetupCaptureDescribesRetainedReturnControlEvidence()
    {
        var response = System.Text.Json.JsonSerializer.Deserialize<SetupCaptureResponse>(
            """
            {
              "schema_version": 1,
              "server_revision": 32,
              "capability": "save_backed_setup_capture_v2",
              "capture": {
                "request_id": "capture-1",
                "status": "ready",
                "acquisition_source": "retained_return_control_refresh",
                "source_manual_control_id": "manual-1"
              }
            }
            """);

        Assert.NotNull(response?.Capture);
        Assert.Equal(
            "retained_return_control_refresh",
            response!.Capture!.AcquisitionSource);
        Assert.Equal("manual-1", response.Capture.SourceManualControlId);
    }

    [Fact]
    public void StatusDeserializesCurrentSaveBackedPerks()
    {
        var response = System.Text.Json.JsonSerializer.Deserialize<StatusResponse>(
            """
            {
              "api_version": 1,
              "server_revision": 32,
              "current_battle_perks": {
                "schema_version": 1,
                "status": "available",
                "reason": "",
                "source": "monitor_validated_player_save_perk_prefix",
                "order_semantics": "most_recent_selection_first",
                "captured_at": "2026-08-08T17:05:00+00:00",
                "saved_wave": 620,
                "picked_count": 5,
                "unique_count": 2,
                "items": [
                  {
                    "perk_key": "damage",
                    "label": "Damage",
                    "level": 1,
                    "last_selected_wave": 580,
                    "last_selected_sequence": 5
                  },
                  {
                    "perk_key": "perk_wave_requirement",
                    "label": "Perk Wave Requirement",
                    "level": 3,
                    "last_selected_wave": 540,
                    "last_selected_sequence": 4
                  }
                ]
              }
            }
            """);

        Assert.NotNull(response);
        Assert.Equal("available", response!.CurrentBattlePerks.Status);
        Assert.Equal(620, response.CurrentBattlePerks.SavedWave);
        Assert.Equal(5, response.CurrentBattlePerks.PickedCount);
        Assert.Equal(2, response.CurrentBattlePerks.Items.Count);
        Assert.Equal("Damage", response.CurrentBattlePerks.Items[0].Label);
        Assert.Equal(3, response.CurrentBattlePerks.Items[1].Level);
    }

    [Fact]
    public void ReadyCaptureCannotBypassServerCompatibility()
    {
        var oldServer = ControlSurfaceCompatibility.Evaluate(
            Status(
                30,
                "better_control_model_v2",
                "save_backed_setup_capture_v2"));
        var model = new BetterControlModelStatus
        {
            SetupCapture = new SetupCaptureStatus { Status = "ready" },
            Actions = new Dictionary<string, BetterControlActionAvailability>
            {
                ["capture_current_setup"] = new() { Available = true },
            },
        };

        Assert.False(
            ControlSurfaceCompatibility.CanOpenSetupCapture(oldServer, model));
    }

    [Fact]
    public void CaptureAvailabilityTracksReadyAndActionStates()
    {
        var compatible = ControlSurfaceCompatibility.Evaluate(
            Status(
                34,
                "active_battle_strategy_adoption",
                "advisory_preflight_decisions",
                "better_control_model_v2",
                "completed_battle_discard",
                "confirmed_local_mapping_status_v1",
                "current_battle_perks_v1",
                "current_run_activity_scope",
                "exclusive_strategy_validation_status",
                "explicit_strategy_disposition",
                "game_speed_target",
                "host_performance_gpu_v1",
                "host_performance_telemetry_v1",
                "managed_custom_module_presets_v1",
                "observed_game_speed",
                "selected_strategy_process_start",
                "save_backed_setup_capture_v2",
                "save_mapping_review_status_v1",
                "strategy_action_gate_v1",
                "strategy_authoring_local_loadout_editors_v1",
                "strategy_authoring_preset_local_copy_v1",
                "strategy_authoring_profile_lifecycle_v1",
                "strategy_authoring_specialized_editors_v1",
                "strategy_authoring_v1",
                "strategy_profile_catalog_v1",
                "strategy_profile_editor_v2",
                "strategy_revision_history_v1",
                "terminal_dispositions_v2",
                "tournament_launch_confirmation"));
        var model = new BetterControlModelStatus
        {
            SetupCapture = new SetupCaptureStatus { Status = "requested" },
            Actions = new Dictionary<string, BetterControlActionAvailability>
            {
                ["capture_current_setup"] = new() { Available = false },
            },
        };

        Assert.True(
            ControlSurfaceCompatibility.CanOpenSetupCapture(compatible, model));
        model.SetupCapture.Status = "ready";
        Assert.True(
            ControlSurfaceCompatibility.CanOpenSetupCapture(compatible, model));
    }

    [Fact]
    public void ConfirmedLocalMappingPresentationIsPersistentButNonmodal()
    {
        var active = new ConfirmedLocalMappingStatus
        {
            Available = true,
            BlocksStartup = false,
            Items =
            [
                new ConfirmedLocalMappingItem
                {
                    MappingId = "data-9-game-1101",
                    RawValue = 41,
                    SemanticValue = "Being Annihilator",
                    Scope = new Dictionary<string, string>
                    {
                        ["slot_key"] = "cannon_assist",
                    },
                    State = "active_local",
                    Reason = "canonical integration is pending",
                },
            ],
        };

        var presentation =
            ControlSurfaceCompatibility.ConfirmedLocalMapping(active);

        Assert.True(presentation.Visible);
        Assert.Equal("warning", presentation.Severity);
        Assert.Contains("cannon_assist", presentation.Detail);
        Assert.False(active.BlocksStartup);

        active.Items[0].State = "integrated";
        Assert.False(
            ControlSurfaceCompatibility.ConfirmedLocalMapping(active).Visible);

        active.Items[0].State = "canonical_conflict";
        Assert.Equal(
            "danger",
            ControlSurfaceCompatibility.ConfirmedLocalMapping(active).Severity);

        active.Items =
        [
            new ConfirmedLocalMappingItem
            {
                State = "active_local",
                Reason = "pending integration",
            },
            new ConfirmedLocalMappingItem
            {
                State = "canonical_conflict",
                Reason = "conflicting canonical value",
                Scope = null!,
            },
        ];
        presentation = ControlSurfaceCompatibility.ConfirmedLocalMapping(active);
        Assert.Equal("danger", presentation.Severity);
        Assert.Contains("conflicting canonical value", presentation.Detail);

        active.Items = null!;
        Assert.False(
            ControlSurfaceCompatibility.ConfirmedLocalMapping(active).Visible);
    }

    [Fact]
    public void TerminalCaptureOpensReadOnlyAndRetryRemainsSeparate()
    {
        var model = new BetterControlModelStatus
        {
            SetupCapture = new SetupCaptureStatus
            {
                Status = "unavailable",
                AuthorityOutcome = "preserved",
            },
            Actions = new Dictionary<string, BetterControlActionAvailability>
            {
                ["capture_current_setup"] = new() { Available = false },
            },
        };

        Assert.Equal(
            SetupCaptureOpenAction.Inspect,
            ControlSurfaceCompatibility.SetupCaptureAction(model));

        model.SetupCapture = null;
        model.Actions["capture_current_setup"].Available = true;
        Assert.Equal(
            SetupCaptureOpenAction.Request,
            ControlSurfaceCompatibility.SetupCaptureAction(model));
    }

    [Theory]
    [InlineData("requested", true, false)]
    [InlineData("acknowledged", true, false)]
    [InlineData("no_op", false, true)]
    [InlineData("stale", false, true)]
    [InlineData("rejected", false, true)]
    [InlineData("unavailable", false, true)]
    [InlineData("interrupted", false, true)]
    public void WorkflowPresentationDistinguishesPendingAndTerminalStates(
        string status,
        bool pending,
        bool terminal)
    {
        var presentation = ControlSurfaceCompatibility.PresentWorkflow(status);

        Assert.Equal(pending, presentation.Pending);
        Assert.Equal(terminal, presentation.Terminal);
        Assert.False(string.IsNullOrWhiteSpace(presentation.Label));
    }

    [Fact]
    public void DurableCapturedDraftRetainsReviewAndEvidenceOrigin()
    {
        var response = System.Text.Json.JsonSerializer.Deserialize<CapturedStrategyDraftResponse>(
            """
            {
              "draft": {
                "id": "captured_farm",
                "saved_at": "2026-08-07T20:00:00-07:00",
                "source": {"id": "captured_farm", "display_name": "Captured Farm", "family": "farm", "tier": 19, "settings": {}},
                "capture": {
                  "status": "partial",
                  "captured_at": "2026-08-07T19:59:00-07:00",
                  "mapping_id": "data-9-game-1073",
                  "mapping_maturity": "candidate",
                  "capture_origin": {
                    "acquisition_source": "retained_return_control_refresh",
                    "source_manual_control_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                  }
                },
                "review": {
                  "captured_vs_base": {"change_count": 1},
                  "unresolved": [{"setting_id": "orb_distance", "display_name": "Orb Distance", "status": "unresolved", "reason": "not mapped"}],
                  "review_fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                }
              }
            }
            """);

        Assert.Equal(
            "retained_return_control_refresh",
            response!.Draft.Capture.CaptureOrigin.AcquisitionSource);
        Assert.Single(response.Draft.Review.Unresolved);
        Assert.Equal(
            "orb_distance",
            response.Draft.Review.Unresolved[0].SettingId);
        Assert.Equal(1, response.Draft.Review.CapturedVsBase.GetProperty("change_count").GetInt32());
    }
}
