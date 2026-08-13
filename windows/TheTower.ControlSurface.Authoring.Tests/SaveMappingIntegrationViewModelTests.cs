using System.Text.Json;

namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class SaveMappingIntegrationViewModelTests
{
    private static readonly string CandidateId = new('a', 64);

    [Fact]
    public void Revision_40_review_contract_deserializes_without_workspace()
    {
        const string payload = """
        {
          "schema_version": 2,
          "capability": "save_mapping_develop_integration_v1",
          "operation": "review",
          "candidate_record_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "reviewed_proposal_fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "reviewed_base_commit": "1111111111111111111111111111111111111111",
          "repository": {
            "main_commit":"main",
            "develop_commit":"develop",
            "synchronized":true,
            "integration_available":true,
            "develop_path":"/develop"
          },
          "proposal": {
            "schema_version": 2,
            "atomic_group": true,
            "targets": [{
              "mapping_id": "data-9-game-1073",
              "path": "config/player_save_versions/data_9_game_1073.json",
              "expected_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
              "state": "pending",
              "operations": [{
                "op": "add",
                "path": "/module_loadout/assist/0/values/-",
                "value": {"info_index":10,"name":"Astral Deliverance"}
              }]
            }]
          },
          "integrate": {"available":true,"code":"","reason":""}
        }
        """;

        var review = JsonSerializer.Deserialize<SaveMappingIntegrationReview>(
            payload);

        Assert.NotNull(review);
        Assert.Equal("save_mapping_develop_integration_v1", review.Capability);
        Assert.True(review.Repository.Synchronized);
        var operation = Assert.Single(Assert.Single(review.Proposal.Targets).Operations);
        Assert.Equal("add", operation.Operation);
        Assert.Equal(10, operation.Value.GetProperty("info_index").GetInt32());
    }

    [Fact]
    public void Candidate_change_invalidates_reviewed_proposal()
    {
        var review = Review();

        Assert.True(SaveMappingIntegrationViewModels.ReviewMatches(
            review,
            CandidateId));
        var changed = SaveMappingIntegrationViewModels.IntegrateAvailability(
            review,
            "candidate-2");
        Assert.False(changed.Available);
        Assert.Equal("review_stale", changed.Code);
    }

    [Fact]
    public void Exact_fingerprint_and_server_availability_enable_integration()
    {
        var available = SaveMappingIntegrationViewModels.IntegrateAvailability(
            Review(),
            CandidateId);

        Assert.True(available.Available);
        Assert.Equal("", available.Code);
    }

    [Fact]
    public void Exact_durable_recovery_review_remains_actionable_after_refresh()
    {
        var review = Review();
        review.RecoveryRequired = true;
        review.Repository.DevelopCommit = new string('b', 40);
        review.Repository.Synchronized = false;
        review.Repository.ProductionClean = false;
        review.Repository.DevelopClean = false;
        review.Repository.IntegrationAvailable = false;
        review.Repository.Code = "transaction_recovery_required";
        review.Proposal.Targets[0].Operations = [];
        review.Integrate.Code = "transaction_recovery_required";

        var available = SaveMappingIntegrationViewModels.IntegrateAvailability(
            review,
            CandidateId);

        Assert.True(available.Available);
        Assert.Equal("transaction_recovery_required", available.Code);
    }

    [Fact]
    public void Proposal_and_result_keep_commit_and_promotion_boundaries_visible()
    {
        using var value = JsonDocument.Parse(
            "{\"info_index\":10,\"name\":\"Astral Deliverance\"}");
        var review = Review();
        review.Proposal = new SaveMappingProposal
        {
            SchemaVersion = 2,
            RecordId = CandidateId,
            AtomicGroup = true,
            Targets =
            [
                new()
                {
                    MappingId = "data-9-game-1073",
                    Path = "config/player_save_versions/data_9_game_1073.json",
                    ExpectedSha256 = new string('d', 64),
                    State = "pending",
                    Operations =
                    [
                        new()
                        {
                            Operation = "add",
                            Path = "/module_loadout/assist/0/values/-",
                            Value = value.RootElement.Clone(),
                        },
                    ],
                },
            ],
        };
        var proposal = SaveMappingIntegrationViewModels.ProposalText(review);
        Assert.Contains("REVIEWED PROPOSAL FINGERPRINT", proposal);
        Assert.Contains("after " + new string('e', 64), proposal);
        Assert.Contains("mode 0664", proposal);
        Assert.Contains("Astral Deliverance", proposal);
        Assert.DoesNotContain("feature", proposal, StringComparison.OrdinalIgnoreCase);

        var result = IntegratedResult();
        result.Warning = "Audit logging needs inspection.";
        var presentation = SaveMappingIntegrationViewModels.IntegratedResult(
            result,
            review);
        var detail = SaveMappingIntegrationViewModels.IntegratedResultText(
            result,
            review);
        Assert.True(presentation.Success);
        Assert.Contains("Mapping invariants passed", presentation.Detail);
        Assert.Contains("committed: true", detail);
        Assert.Contains("promoted: false", detail);
        Assert.Contains("production validation: pending", detail);
        Assert.Contains("Audit logging needs inspection.", detail);
    }

    [Fact]
    public void Integrated_result_is_bound_to_exact_identity_and_lifecycle()
    {
        var result = IntegratedResult();

        Assert.True(SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            Review()).Valid);

        result.CandidateRecordId = "different";
        Assert.False(SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            Review()).Valid);
        result = IntegratedResult();
        result.Committed = false;
        Assert.False(SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            Review()).Valid);
        result = IntegratedResult();
        result.PromotionValidation = "passed";
        Assert.False(SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            Review()).Valid);
        result = IntegratedResult();
        result.Targets![0].AfterSha256 = "bad";
        Assert.False(SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            Review()).Valid);
        result = IntegratedResult();
        result.Promoted = true;
        Assert.False(SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            Review()).Valid);
        result.Idempotent = true;
        var recovery = Review();
        recovery.RecoveryRequired = true;
        recovery.Repository.MainCommit = result.IntegrationCommit;
        recovery.Repository.DevelopCommit = result.IntegrationCommit;
        recovery.Repository.IntegrationAvailable = false;
        recovery.Repository.Code = "transaction_recovery_required";
        recovery.Proposal.Targets[0].Operations = [];
        Assert.True(SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            recovery).Valid);
        result = IntegratedResult();
        result.Targets = [result.Targets![0], result.Targets[0]];
        Assert.False(SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            Review()).Valid);
    }

    [Fact]
    public void Missing_result_flags_cannot_deserialize_as_valid_false_flags()
    {
        const string payload = """
        {
          "schema_version":2,
          "capability":"save_mapping_develop_integration_v1",
          "operation":"integrate",
          "disposition":"committed_to_develop",
          "candidate_record_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "reviewed_proposal_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "integration_commit":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "develop_commit":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "mapping_invariants":"passed",
          "promotion_validation":"pending",
          "targets":[{
            "path":"config/player_save_versions/data.json",
            "mapping_id":"data-9-game-1073",
            "before_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
            "after_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            "changed":true
          }]
        }
        """;
        var result = JsonSerializer.Deserialize<SaveMappingIntegratedResult>(payload);

        Assert.NotNull(result);
        Assert.Null(result.Committed);
        Assert.Null(result.Promoted);
        Assert.Null(result.Idempotent);
        Assert.False(SaveMappingIntegrationViewModels.ValidateIntegratedResult(
            result,
            Review()).Valid);
    }

    [Fact]
    public void Failure_copy_separates_safe_rejection_retry_and_uncertainty()
    {
        var rejected = SaveMappingIntegrationViewModels.Failure(
            "repository_not_synchronized",
            "Pending promotion.",
            integrateRequest: true);
        var uncertain = SaveMappingIntegrationViewModels.Failure(
            "commit_state_uncertain",
            "Inspect transaction.",
            integrateRequest: true);
        var unchanged = SaveMappingIntegrationViewModels.Failure(
            "develop_fast_forward_failed",
            "Develop stayed at base.",
            integrateRequest: true);

        Assert.False(rejected.Uncertain);
        Assert.Contains("Nothing was committed", rejected.Detail);
        Assert.True(uncertain.Uncertain);
        Assert.Contains("do not retry automatically", uncertain.Detail);
        Assert.False(unchanged.Uncertain);
        Assert.Contains("retry once only when directed", unchanged.Detail);
    }

    private static SaveMappingIntegrationReview Review() => new()
    {
        SchemaVersion = 2,
        Capability = "save_mapping_develop_integration_v1",
        Operation = "review",
        CandidateRecordId = CandidateId,
        ReviewedProposalFingerprint = new string('a', 64),
        ReviewedBaseCommit = new string('c', 40),
        Repository = new SaveMappingRepositoryStatus
        {
            MainCommit = new string('c', 40),
            DevelopCommit = new string('c', 40),
            Synchronized = true,
            ProductionClean = true,
            DevelopClean = true,
            IntegrationAvailable = true,
            DevelopPath = "/develop",
        },
        CanonicalMappingFingerprint = new string('f', 64),
        Proposal = new SaveMappingProposal
        {
            SchemaVersion = 2,
            Capability = "player_save_mapping_candidate_review_v2",
            RecordId = CandidateId,
            AtomicGroup = true,
            Targets =
            [
                new()
                {
                    MappingId = "data-9-game-1073",
                    Path = "config/player_save_versions/data_9_game_1073.json",
                    ExpectedSha256 = new string('d', 64),
                    State = "pending",
                    Operations =
                    [
                        new()
                        {
                            Operation = "add",
                            Path = "/values/-",
                            Value = JsonValue("7"),
                        },
                    ],
                },
            ],
        },
        RenderedTargets =
        [
            new()
            {
                MappingId = "data-9-game-1073",
                Path = "config/player_save_versions/data_9_game_1073.json",
                BeforeSha256 = new string('d', 64),
                AfterSha256 = new string('e', 64),
                Changed = true,
                Mode = Convert.ToInt32("664", 8),
            },
        ],
        Integrate = new BetterControlActionAvailability
        {
            Available = true,
            Code = "",
            Reason = "",
        },
    };

    private static SaveMappingIntegratedResult IntegratedResult() => new()
    {
        SchemaVersion = 2,
        Capability = "save_mapping_develop_integration_v1",
        Operation = "integrate",
        Disposition = "committed_to_develop",
        Idempotent = false,
        CandidateRecordId = CandidateId,
        ReviewedProposalFingerprint = new string('a', 64),
        BaseCommit = new string('c', 40),
        DevelopCommit = new string('b', 40),
        IntegrationCommit = new string('b', 40),
        Committed = true,
        Promoted = false,
        MappingInvariants = "passed",
        PromotionValidation = "pending",
        Targets =
        [
            new()
            {
                MappingId = "data-9-game-1073",
                Path = "config/player_save_versions/data_9_game_1073.json",
                BeforeSha256 = new string('d', 64),
                AfterSha256 = new string('e', 64),
                Changed = true,
                Mode = Convert.ToInt32("664", 8),
            },
        ],
    };

    private static JsonElement JsonValue(string payload)
    {
        using var document = JsonDocument.Parse(payload);
        return document.RootElement.Clone();
    }
}
