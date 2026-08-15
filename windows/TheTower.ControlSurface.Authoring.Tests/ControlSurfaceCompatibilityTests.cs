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
        var status = Status(40);
        var result = ControlSurfaceCompatibility.Evaluate(status);

        Assert.False(result.IsCompatible);
        Assert.Contains("better_control_model_v2", result.MissingCapabilities);
        Assert.Contains("current_battle_perks_v1", result.MissingCapabilities);
        Assert.Contains(
            "confirmed_local_mapping_status_v2",
            result.MissingCapabilities);
        Assert.Contains(
            "save_backed_setup_capture_v2",
            result.MissingCapabilities);
        Assert.Contains(
            "save_mapping_staged_candidate_v1",
            result.MissingCapabilities);
        Assert.Contains(
            "save_mapping_candidate_disposition_v1",
            result.MissingCapabilities);
        Assert.Contains(
            "save_mapping_automatic_promotion_v1",
            result.MissingCapabilities);
        Assert.Contains(
            "save_mapping_machine_verification_v1",
            result.MissingCapabilities);
        Assert.Contains(
            "save_mapping_review_status_v2",
            result.MissingCapabilities);
        Assert.Contains(
            "strategy_authoring_preset_local_copy_v1",
            result.MissingCapabilities);
        Assert.Contains(
            "host_performance_process_attribution_v1",
            result.MissingCapabilities);
        Assert.Contains(
            "strategy_aware_attach_v1",
            result.MissingCapabilities);
    }

    [Fact]
    public void StrategyAwareAttachRequiresRevisionThirtyEight()
    {
        var result = ControlSurfaceCompatibility.Evaluate(
            Status(
                37,
                "strategy_aware_attach_v1"));

        Assert.False(result.IsCompatible);
        Assert.False(result.ServerRevisionSupported);
    }

    [Theory]
    [InlineData(true, false, "strategy_selection_unaccepted")]
    [InlineData(false, true, "strategy_selection_pending")]
    public void AttachWaitsForTheVisibleStrategySelectionToBeAccepted(
        bool dirty,
        bool requestInFlight,
        string expectedCode)
    {
        var availability = ControlSurfaceCompatibility.ResolveAttachAvailability(
            new BetterControlActionAvailability
            {
                Available = true,
                Code = "available",
                Reason = "Attach is available.",
            },
            dirty,
            requestInFlight);

        Assert.False(availability.Available);
        Assert.Equal(expectedCode, availability.Code);
        Assert.Contains("Strategy", availability.Reason);
    }

    [Fact]
    public void AuthoritativeStrategyScopeWinsOverMissingLegacyAcknowledgement()
    {
        var status = Status(35, "better_control_model_v2");
        status.Control = new ControlStatus
        {
            Strategy = "legacy_pending",
            StrategyApplyMode = "next_boundary",
        };
        status.Acknowledgements = new AcknowledgementStatus
        {
            Strategy = null,
        };
        status.ControlModel = new BetterControlModelStatus
        {
            StrategyScope = new BetterControlStrategyScopeStatus
            {
                StartupDefault = "farm_t19_ad_assist",
                ActiveBattle = "farm_t19_ad_assist",
                PendingNextBoundary = null,
            },
        };

        var presentation = ControlSurfaceCompatibility.ResolveStrategyScope(
            status,
            processActive: true,
            configuredStrategy: "legacy_configured");

        Assert.True(presentation.Authoritative);
        Assert.Equal("farm_t19_ad_assist", presentation.StartupDefault);
        Assert.Equal("farm_t19_ad_assist", presentation.CurrentStrategy);
        Assert.Null(presentation.PendingStrategy);
    }

    [Fact]
    public void AuthoritativeStrategyScopeRendersCurrentAndPendingBoundary()
    {
        var status = Status(35, "better_control_model_v2");
        status.ControlModel = new BetterControlModelStatus
        {
            StrategyScope = new BetterControlStrategyScopeStatus
            {
                StartupDefault = "farm_t19",
                ActiveBattle = "farm_t18",
                PendingNextBoundary = "farm_t19",
            },
        };

        var presentation = ControlSurfaceCompatibility.ResolveStrategyScope(
            status,
            processActive: true,
            configuredStrategy: "contradictory_legacy");

        Assert.Equal("farm_t18", presentation.CurrentStrategy);
        Assert.Equal("farm_t19", presentation.PendingStrategy);
        Assert.Equal("Pending boundary", presentation.PendingLabel);
    }

    [Fact]
    public void AuthoritativeStrategyScopeFlagsRunningDegradation()
    {
        var status = Status(38, "better_control_model_v2");
        status.ControlModel = new BetterControlModelStatus
        {
            StrategyScope = new BetterControlStrategyScopeStatus
            {
                StartupDefault = "farm_t19",
                ActiveBattle = null,
                Degradation = new Dictionary<string, object?>
                {
                    ["reason"] = "attached Tier mismatch",
                },
            },
        };

        var presentation = ControlSurfaceCompatibility.ResolveStrategyScope(
            status,
            processActive: true,
            configuredStrategy: "farm_t19");

        Assert.True(presentation.Degraded);
    }

    [Fact]
    public void ActiveAdoptionAndStoppedScopeRemainExplicit()
    {
        var status = Status(35, "better_control_model_v2");
        status.ControlModel = new BetterControlModelStatus
        {
            StrategyScope = new BetterControlStrategyScopeStatus
            {
                StartupDefault = "farm_t19",
                ActiveBattle = "farm_t18",
                PendingNextBoundary = null,
                PendingActiveBattle = "farm_t19",
            },
        };

        var active = ControlSurfaceCompatibility.ResolveStrategyScope(
            status,
            processActive: true,
            configuredStrategy: "legacy");
        var stopped = ControlSurfaceCompatibility.ResolveStrategyScope(
            status,
            processActive: false,
            configuredStrategy: "legacy");

        Assert.Equal("farm_t19", active.PendingStrategy);
        Assert.Equal("Pending active adoption", active.PendingLabel);
        Assert.Equal("farm_t19", stopped.StartupDefault);
        Assert.Null(stopped.CurrentStrategy);
        Assert.Null(stopped.PendingStrategy);
    }

    [Fact]
    public void LegacyStrategyReconstructionRunsOnlyWithoutCapability()
    {
        var legacy = Status(34);
        legacy.Control = new ControlStatus
        {
            Strategy = "farm_t19",
            StrategyApplyMode = "next_boundary",
        };
        legacy.Acknowledgements = new AcknowledgementStatus
        {
            Strategy = new DirectiveAcknowledgement
            {
                Value = "farm_t18",
                AcknowledgesCurrent = false,
            },
        };
        var reconstructed = ControlSurfaceCompatibility.ResolveStrategyScope(
            legacy,
            processActive: true,
            configuredStrategy: "farm_t18");

        var authoritativeButMissing = Status(35, "better_control_model_v2");
        authoritativeButMissing.Control = legacy.Control;
        authoritativeButMissing.Acknowledgements = legacy.Acknowledgements;
        var unavailable = ControlSurfaceCompatibility.ResolveStrategyScope(
            authoritativeButMissing,
            processActive: true,
            configuredStrategy: "farm_t18");

        Assert.False(reconstructed.Authoritative);
        Assert.Equal("farm_t18", reconstructed.CurrentStrategy);
        Assert.Equal("farm_t19", reconstructed.PendingStrategy);
        Assert.True(unavailable.Authoritative);
        Assert.Null(unavailable.CurrentStrategy);
        Assert.Null(unavailable.PendingStrategy);
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
                ControlSurfaceCompatibility.MinimumServerRevision,
                "active_battle_strategy_adoption",
                "advisory_preflight_decisions",
                "better_control_model_v2",
                "bluestacks_maintenance_v1",
                "bluestacks_maintenance_v2",
                "bluestacks_operator_restart_v1",
                "bluestacks_listener_lifetime_telemetry_v1",
                "bluestacks_maintenance_policy_v1",
                "completed_battle_discard",
                "confirmed_local_mapping_status_v2",
                "current_battle_perks_v1",
                "current_run_activity_scope",
                "exclusive_strategy_validation_status",
                "explicit_strategy_disposition",
                "game_speed_target",
                "host_performance_gpu_v1",
                "host_performance_process_attribution_v1",
                "host_performance_telemetry_v1",
                "managed_custom_module_presets_v1",
                "observed_game_speed",
                "runtime_control_acknowledgements_v1",
                "selected_strategy_process_start",
                "save_backed_setup_capture_v2",
                "save_mapping_candidate_disposition_v1",
                "save_mapping_automatic_promotion_v1",
                "save_mapping_machine_verification_v1",
                "save_mapping_staged_candidate_v1",
                "save_mapping_review_status_v2",
                "strategy_aware_attach_v1",
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
        Assert.True(
            ControlSurfaceCompatibility.CanOpenSaveMappingIntegration(compatible));
        model.SetupCapture.Status = "ready";
        Assert.True(
            ControlSurfaceCompatibility.CanOpenSetupCapture(compatible, model));
    }

    [Fact]
    public void ConfirmedLocalMappingPresentationIsPersistentButNonmodal()
    {
        var active = new ConfirmedLocalMappingStatus
        {
            SchemaVersion = 2,
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
        Assert.Contains("Module identity", presentation.Title);
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

        active.Items =
        [
            new ConfirmedLocalMappingItem
            {
                State = "active_local",
                Reason = "ordinary local queue",
            },
            new ConfirmedLocalMappingItem
            {
                State = "promotion_pending",
                Reason = "awaiting exact production promotion",
            },
        ];
        presentation = ControlSurfaceCompatibility.ConfirmedLocalMapping(active);
        Assert.Contains("automatic promotion", presentation.Title);
        Assert.Contains("awaiting exact production promotion", presentation.Detail);

        active.Items =
        [
            new ConfirmedLocalMappingItem
            {
                State = "promotion_cleanup_pending",
                Reason = "owner release pending",
            },
        ];
        presentation = ControlSurfaceCompatibility.ConfirmedLocalMapping(active);
        Assert.Contains("automatic cleanup", presentation.Title);
        Assert.Contains("owner release pending", presentation.Detail);

        active.Items =
        [
            new ConfirmedLocalMappingItem
            {
                State = "restaging_required",
                Reason = "main advanced",
            },
        ];
        presentation = ControlSurfaceCompatibility.ConfirmedLocalMapping(active);
        Assert.Equal("warning", presentation.Severity);
        Assert.Contains("restaged", presentation.Title);

        active.Items = null!;
        var malformed = ControlSurfaceCompatibility.ConfirmedLocalMapping(active);
        Assert.True(malformed.Visible);
        Assert.Equal("danger", malformed.Severity);
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

    [Fact]
    public void StatusDeserializesBlueStacksRecoveryContracts()
    {
        var response = System.Text.Json.JsonSerializer.Deserialize<StatusResponse>(
            """
            {
              "emulator_degradation": {
                "schema_version": 1,
                "assessed_at": "2026-08-10T12:00:00+00:00",
                "status": "automatic_ready",
                "automatic_ready": true,
                "reason": "degraded",
                "candidate_battle_ids": ["Battle1", "Battle2"],
                "candidate_cph_ratio": 0.88,
                "effective_game_speed_ratio": 0.99,
                "host_evidence": {
                  "status": "confirmed_growth",
                  "identity_scope": "exact_listener_lifetime",
                  "sample_count": 120,
                  "span_seconds": 1190,
                  "stable_process_windows": 120,
                  "sampler_session_count": 3,
                  "handle_low_water": 3884,
                  "handle_recent_median": 25297,
                  "handle_ratio": 6.51,
                  "handle_delta": 21413,
                  "listener_identity": {
                    "host_id": "ALIEN",
                    "adb_port": 5555,
                    "process_id": 90,
                    "process_started_at": "2026-08-10T10:00:00.1234567+00:00",
                    "executable_path": "C:\\\\Program Files\\\\BlueStacks_nxt\\\\HD-Player.exe",
                    "instance_name": "Nougat32"
                  },
                  "reason": "sustained handle growth confirmed"
                },
                "host_contention": {
                  "status": "clear",
                  "reason": "no sustained external contention",
                  "other_cpu_percent_median": 18.5,
                  "other_gpu_percent_median": 7.0
                },
                "automatic_request_gate": {
                  "available": true,
                  "code": "available",
                  "reason": "fresh authority"
                },
                "automatic_triggers": {
                  "preventive_handle_ceiling": {
                    "status": "ready",
                    "ready": true,
                    "deferred_by_contention": false,
                    "handle_recent_median": 25297,
                    "handle_low_water": 3884,
                    "handle_delta": 21413,
                    "sampled_coverage_seconds": 610,
                    "reason": "ceiling met"
                  },
                  "severe_in_run_loss": {
                    "status": "within_relaxed_band",
                    "ready": false,
                    "interval_count": 3,
                    "interval_cph_ratios": [0.81, 0.84, 0.79],
                    "reason": "healthy enough"
                  },
                  "completed_run_degradation": {
                    "status": "ready",
                    "ready": true,
                    "reason": "completed evidence"
                  }
                }
              },
              "host_maintenance": {
                "schema_version": 1,
                "host_restart_authorized": true,
                "operator_restart": {
                  "available": true,
                  "code": "available",
                  "reason": "fresh RUNNING Farm battle authority is available"
                },
                "request": {
                  "request_id": "0123456789abcdef0123456789abcdef",
                  "state": "requested",
                  "reason": "degraded",
                  "initiator": "operator",
                  "terminal_disposition": "fallback_new_battle",
                  "terminal_reason": "resume unavailable; new Farm battle started",
                  "host_target": {
                    "host_id": "ALIEN",
                    "adb_port": 5555,
                    "process_id": 90,
                    "process_started_at": "2026-08-10T10:00:00.1234567+00:00",
                    "executable_path": "C:\\\\Program Files\\\\BlueStacks_nxt\\\\HD-Player.exe",
                    "instance_name": "Nougat32"
                  }
                }
              }
            }
            """);

        Assert.NotNull(response);
        Assert.True(response!.EmulatorDegradation.AutomaticReady);
        Assert.Equal(0.88, response.EmulatorDegradation.CandidateCphRatio);
        Assert.Equal(
            "exact_listener_lifetime",
            response.EmulatorDegradation.HostEvidence!.IdentityScope);
        Assert.Equal(25297, response.EmulatorDegradation.HostEvidence.HandleRecentMedian);
        Assert.Equal(3, response.EmulatorDegradation.HostEvidence.SamplerSessionCount);
        Assert.Equal(
            "clear",
            response.EmulatorDegradation.HostContention!.Status);
        Assert.Equal(
            18.5,
            response.EmulatorDegradation.HostContention.OtherCpuPercentMedian);
        Assert.True(response.EmulatorDegradation.AutomaticRequestGate.Available);
        Assert.True(
            response.EmulatorDegradation.AutomaticTriggers
                .PreventiveHandleCeiling.Ready);
        Assert.Equal(
            [0.81, 0.84, 0.79],
            response.EmulatorDegradation.AutomaticTriggers
                .SevereInRunLoss.IntervalCphRatios);
        Assert.Equal(
            "2026-08-10T10:00:00.1234567+00:00",
            response.EmulatorDegradation.HostEvidence.ListenerIdentity!
                .ProcessStartedAt);
        Assert.True(response.HostMaintenance.HostRestartAuthorized);
        Assert.True(response.HostMaintenance.OperatorRestart.Available);
        Assert.Equal("requested", response.HostMaintenance.Request!.State);
        Assert.Equal("operator", response.HostMaintenance.Request.Initiator);
        Assert.Equal(90, response.HostMaintenance.Request.HostTarget!.ProcessId);
        Assert.Equal(
            "2026-08-10T10:00:00.1234567+00:00",
            response.HostMaintenance.Request.HostTarget.ProcessStartedAt);
        Assert.Equal(
            "fallback_new_battle",
            response.HostMaintenance.Request.TerminalDisposition);
        Assert.Equal(
            "resume unavailable; new Farm battle started",
            response.HostMaintenance.Request.TerminalReason);
    }

    [Theory]
    [InlineData("Nougat32")]
    [InlineData("Pie64_1")]
    public void BlueStacksInstanceNamesAreBounded(string instanceName)
    {
        Assert.Equal(
            instanceName,
            BlueStacksInstanceController.ValidateInstanceName(instanceName));
        Assert.Throws<ArgumentException>(() =>
            BlueStacksInstanceController.ValidateInstanceName(
                instanceName + " --instance Other"));
    }

    [Fact]
    public void BlueStacksConfigurationBindsInstanceNameToStatusAdbPort()
    {
        var mappings = BlueStacksInstanceController.ParseInstanceAdbPortMappings(
            [
                "bst.instance.Nougat32.status.adb_port=\"5555\"",
                "bst.instance.Pie64_1.status.adb_port=\"5565\"",
                "bst.instance.Nougat32.adb_port=\"9999\"",
                "unrelated=value",
            ]);

        Assert.Equal(2, mappings.Count);
        Assert.Equal(5555, mappings["Nougat32"]);
        Assert.Equal(5565, mappings["Pie64_1"]);
    }

    [Fact]
    public void BlueStacksStoppedPortCannotBecomeAnotherLiveInstancePort()
    {
        var target = new BlueStacksRecoveryTarget(
            @"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
            "Nougat32",
            5555);

        BlueStacksInstanceController.ValidateConfiguredPortBinding(
            target,
            5555,
            allowStoppedPort: false);
        BlueStacksInstanceController.ValidateConfiguredPortBinding(
            target,
            0,
            allowStoppedPort: true);
        Assert.True(
            BlueStacksInstanceController.ReplacementMappingReady(target, 5555));
        Assert.False(
            BlueStacksInstanceController.ReplacementMappingReady(target, 0));
        Assert.Throws<BlueStacksTargetBindingException>(() =>
            BlueStacksInstanceController.ValidateConfiguredPortBinding(
                target,
                5565,
                allowStoppedPort: true));
        Assert.Throws<BlueStacksTargetBindingException>(() =>
            BlueStacksInstanceController.ReplacementMappingReady(target, 5565));
        Assert.Throws<BlueStacksTargetBindingException>(() =>
            BlueStacksInstanceController.ValidateConfiguredPortBinding(
                target,
                0,
                allowStoppedPort: false));
    }

    [Fact]
    public void BlueStacksConfiguredPortMustIdentifyOneInstance()
    {
        var target = new BlueStacksRecoveryTarget(
            @"C:\Program Files\BlueStacks_nxt\HD-Player.exe",
            "Nougat32",
            5555);

        BlueStacksInstanceController.ValidateUniqueConfiguredPortBinding(
            target,
            new Dictionary<string, int>
            {
                ["Nougat32"] = 5555,
                ["Pie64"] = 5565,
            },
            5555);
        Assert.Throws<BlueStacksTargetBindingException>(() =>
            BlueStacksInstanceController.ValidateUniqueConfiguredPortBinding(
                target,
                new Dictionary<string, int>
                {
                    ["Nougat32"] = 5555,
                    ["Pie64"] = 5555,
                },
                5555));

        // Multiple stopped instances commonly use zero and do not make a
        // previously acknowledged exact process ambiguous.
        BlueStacksInstanceController.ValidateUniqueConfiguredPortBinding(
            target,
            new Dictionary<string, int>
            {
                ["Nougat32"] = 0,
                ["Pie64"] = 0,
            },
            0);
    }

    [Fact]
    public void BlueStacksNativeCreationTimePreservesWindowsTicks()
    {
        var expected = new DateTimeOffset(
                2026,
                8,
                13,
                4,
                0,
                0,
                TimeSpan.Zero)
            .AddTicks(1_234_567);
        var fileTime = expected.ToFileTime();
        var native = new BlueStacksInstanceController.NativeFileTime(
            unchecked((uint)fileTime),
            unchecked((uint)(fileTime >> 32)));

        Assert.Equal(
            expected,
            BlueStacksInstanceController.FileTimeToUtc(native));
    }
}
