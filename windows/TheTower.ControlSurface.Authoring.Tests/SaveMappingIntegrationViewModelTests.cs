using System.Text.Json;

namespace TheTower.ControlSurface.Authoring.Tests;

public sealed class SaveMappingIntegrationViewModelTests
{
    [Fact]
    public void Revision_35_review_contract_deserializes_exact_operations()
    {
        const string payload = """
        {
          "schema_version": 1,
          "capability": "save_mapping_integration_v1",
          "operation": "review",
          "candidate_record_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "reviewed_proposal_fingerprint": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "repository": {"main_commit":"main","develop_commit":"develop"},
          "workspace": {"workspace_id":"workspace","branch":"feature/mapping-test","head_commit":"feature"},
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
          "prepare": {"available":true,"code":"","reason":""},
          "prepared": false
        }
        """;

        var review = JsonSerializer.Deserialize<SaveMappingIntegrationReview>(
            payload);

        Assert.NotNull(review);
        Assert.Equal("save_mapping_integration_v1", review.Capability);
        var operation = Assert.Single(Assert.Single(review.Proposal.Targets).Operations);
        Assert.Equal("add", operation.Operation);
        Assert.Equal(10, operation.Value.GetProperty("info_index").GetInt32());
        Assert.Equal(
            "Astral Deliverance",
            operation.Value.GetProperty("name").GetString());
    }

    [Fact]
    public void Selection_change_invalidates_reviewed_proposal()
    {
        var review = Review();

        Assert.True(SaveMappingIntegrationViewModels.ReviewMatches(
            review,
            "candidate-1",
            "workspace-1"));
        var changed = SaveMappingIntegrationViewModels.PrepareAvailability(
            review,
            "candidate-2",
            "workspace-1");
        Assert.False(changed.Available);
        Assert.Equal("review_stale", changed.Code);
    }

    [Fact]
    public void Exact_fingerprint_and_server_availability_enable_prepare()
    {
        var available = SaveMappingIntegrationViewModels.PrepareAvailability(
            Review(),
            "candidate-1",
            "workspace-1");

        Assert.True(available.Available);
        Assert.Equal("", available.Code);
    }

    [Fact]
    public void Proposal_and_prepared_result_keep_lifecycle_boundaries_visible()
    {
        using var value = JsonDocument.Parse(
            "{\"info_index\":10,\"name\":\"Astral Deliverance\"}");
        var review = Review();
        review.Proposal = new SaveMappingProposal
        {
            SchemaVersion = 2,
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
        Assert.Contains("Astral Deliverance", proposal);
        Assert.Contains("data-9-game-1073", proposal);

        var result = PreparedResult();
        result.Warning = "Audit logging needs inspection.";
        var presentation = SaveMappingIntegrationViewModels.PreparedResult(
            result,
            "candidate-1",
            "workspace-1",
            new string('a', 64));
        var detail = SaveMappingIntegrationViewModels.PreparedResultText(
            result,
            "candidate-1",
            "workspace-1",
            new string('a', 64));
        Assert.True(presentation.Success);
        Assert.Contains("Validation, commit, and promotion remain required", presentation.Detail);
        Assert.Contains("committed: false", detail);
        Assert.Contains("promoted: false", detail);
        Assert.Contains("validation: pending", detail);
        Assert.Contains("Audit logging needs inspection.", detail);
    }

    [Fact]
    public void Prepared_result_is_bound_to_exact_identity_and_lifecycle()
    {
        var result = PreparedResult();

        Assert.True(SaveMappingIntegrationViewModels.ValidatePreparedResult(
            result,
            "candidate-1",
            "workspace-1",
            new string('a', 64)).Valid);

        result.CandidateRecordId = "different";
        Assert.False(SaveMappingIntegrationViewModels.ValidatePreparedResult(
            result,
            "candidate-1",
            "workspace-1",
            new string('a', 64)).Valid);
        result = PreparedResult();
        result.Committed = true;
        Assert.False(SaveMappingIntegrationViewModels.ValidatePreparedResult(
            result,
            "candidate-1",
            "workspace-1",
            new string('a', 64)).Valid);
        result = PreparedResult();
        result.ValidationStatus = "passed";
        Assert.False(SaveMappingIntegrationViewModels.ValidatePreparedResult(
            result,
            "candidate-1",
            "workspace-1",
            new string('a', 64)).Valid);
        result = PreparedResult();
        result.Targets![0].AfterSha256 = "bad";
        Assert.False(SaveMappingIntegrationViewModels.ValidatePreparedResult(
            result,
            "candidate-1",
            "workspace-1",
            new string('a', 64)).Valid);
    }

    [Fact]
    public void Missing_result_flags_cannot_deserialize_as_valid_false_flags()
    {
        const string payload = """
        {
          "schema_version":1,
          "capability":"save_mapping_integration_v1",
          "operation":"prepare",
          "disposition":"prepared",
          "candidate_record_id":"candidate-1",
          "reviewed_proposal_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "workspace":{"workspace_id":"workspace-1"},
          "validation_status":"pending",
          "targets":[{
            "path":"config/player_save_versions/data.json",
            "mapping_id":"data-9-game-1073",
            "before_sha256":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
            "after_sha256":"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            "changed":true
          }],
          "validation":[]
        }
        """;
        var result = JsonSerializer.Deserialize<SaveMappingPreparedResult>(payload);

        Assert.NotNull(result);
        Assert.Null(result.Committed);
        Assert.Null(result.Promoted);
        Assert.Null(result.Idempotent);
        Assert.False(SaveMappingIntegrationViewModels.ValidatePreparedResult(
            result,
            "candidate-1",
            "workspace-1",
            new string('a', 64)).Valid);
    }

    [Fact]
    public void Reopened_review_deserializes_durable_prepared_result()
    {
        var review = Review();
        review.Prepared = true;
        review.PreparedResult = PreparedResult();
        review.Prepare.Available = false;
        review.Prepare.Code = "already_prepared";
        review.PreparedResult.Idempotent = true;

        var payload = JsonSerializer.Serialize(review);
        var reopened = JsonSerializer.Deserialize<SaveMappingIntegrationReview>(
            payload);

        Assert.NotNull(reopened?.PreparedResult);
        var presentation = SaveMappingIntegrationViewModels.PreparedResult(
            reopened.PreparedResult,
            reopened.CandidateRecordId,
            reopened.Workspace.WorkspaceId,
            reopened.ReviewedProposalFingerprint);
        Assert.True(presentation.Success);
        Assert.Contains("Already prepared", presentation.Title);
    }

    [Fact]
    public void Failure_copy_separates_safe_rejection_from_uncertain_outcome()
    {
        var rejected = SaveMappingIntegrationViewModels.Failure(
            "workspace_dirty",
            "Dirty workspace.",
            prepareRequest: true);
        var uncertain = SaveMappingIntegrationViewModels.Failure(
            "commit_state_uncertain",
            "Inspect transaction.",
            prepareRequest: true);
        var rolledBack = SaveMappingIntegrationViewModels.Failure(
            "mapping_prepare_write_failed",
            "Every transaction-owned target was restored.",
            prepareRequest: true);

        Assert.False(rejected.Uncertain);
        Assert.Contains("Nothing was written by this request", rejected.Detail);
        Assert.True(uncertain.Uncertain);
        Assert.DoesNotContain("Nothing was written", uncertain.Detail);
        Assert.Contains("do not retry automatically", uncertain.Detail);
        Assert.False(rolledBack.Uncertain);
        Assert.Contains("No prepared changes", rolledBack.Detail);
        Assert.DoesNotContain("Nothing was written", rolledBack.Detail);
    }

    private static SaveMappingIntegrationReview Review() => new()
    {
        SchemaVersion = 1,
        Capability = "save_mapping_integration_v1",
        Operation = "review",
        CandidateRecordId = "candidate-1",
        ReviewedProposalFingerprint = new string('a', 64),
        Workspace = new SaveMappingWorkspaceStatus
        {
            WorkspaceId = "workspace-1",
            Branch = "feature/mapping-test",
            HeadCommit = new string('b', 40),
        },
        Repository = new SaveMappingRepositoryStatus
        {
            MainCommit = new string('c', 40),
            DevelopCommit = new string('c', 40),
        },
        Prepare = new BetterControlActionAvailability
        {
            Available = true,
            Code = "",
            Reason = "",
        },
    };

    private static SaveMappingPreparedResult PreparedResult() => new()
    {
        SchemaVersion = 1,
        Capability = "save_mapping_integration_v1",
        Operation = "prepare",
        Disposition = "prepared",
        Idempotent = false,
        CandidateRecordId = "candidate-1",
        ReviewedProposalFingerprint = new string('a', 64),
        Workspace = new SaveMappingWorkspaceStatus
        {
            WorkspaceId = "workspace-1",
            Branch = "feature/mapping-test",
            HeadCommit = new string('b', 40),
        },
        Committed = false,
        Promoted = false,
        ValidationStatus = "pending",
        Targets =
        [
            new()
            {
                MappingId = "data-9-game-1073",
                Path = "config/player_save_versions/data_9_game_1073.json",
                BeforeSha256 = new string('d', 64),
                AfterSha256 = new string('e', 64),
                Changed = true,
            },
        ],
        Validation = [".venv/bin/python tools/development.py checkpoint"],
    };
}
