using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using CosmosVectorBench;
using Xunit;

namespace CosmosVectorBench.Tests;

public sealed class SessionIdAssignerTests
{
    [Fact]
    public void Next_RotatesThroughPoolAndReplacesSlotsIndependently()
    {
        DataSource.SessionIdAssigner assigner = CreateAssigner(poolSize: 3, lifetime: 4, quantum: 2);

        string[] actual = Enumerable.Range(0, 18).Select(_ => assigner.Next()!).ToArray();

        Assert.Equal(
            [
                "session-1", "session-1",
                "session-2", "session-2",
                "session-3", "session-3",
                "session-1", "session-1",
                "session-2", "session-2",
                "session-3", "session-3",
                "session-4", "session-4",
                "session-5", "session-5",
                "session-6", "session-6",
            ],
            actual);
    }

    [Fact]
    public void Next_UsesEachSessionForItsExactLifetime()
    {
        DataSource.SessionIdAssigner assigner = CreateAssigner(poolSize: 4, lifetime: 7, quantum: 3);

        string[] ids = Enumerable.Range(0, 56).Select(_ => assigner.Next()!).ToArray();
        Dictionary<string, int> counts = ids.GroupBy(id => id).ToDictionary(group => group.Key, group => group.Count());

        Assert.Equal(8, counts.Count);
        Assert.All(counts.Values, count => Assert.Equal(7, count));
        AssertRunsDoNotExceed(ids, maximumRunLength: 3);
    }

    [Fact]
    public void Next_PoolSizeOnePreservesContiguousSessions()
    {
        DataSource.SessionIdAssigner assigner = CreateAssigner(poolSize: 1, lifetime: 5, quantum: 100);

        string[] actual = Enumerable.Range(0, 8).Select(_ => assigner.Next()!).ToArray();

        Assert.Equal(
            ["session-1", "session-1", "session-1", "session-1", "session-1", "session-2", "session-2", "session-2"],
            actual);
    }

    [Fact]
    public void Next_RotatesWhenSessionIsShorterThanQuantum()
    {
        DataSource.SessionIdAssigner assigner = CreateAssigner(poolSize: 2, lifetime: 2, quantum: 10);

        string[] actual = Enumerable.Range(0, 8).Select(_ => assigner.Next()!).ToArray();

        Assert.Equal(
            ["session-1", "session-1", "session-2", "session-2", "session-3", "session-3", "session-4", "session-4"],
            actual);
    }

    [Fact]
    public void Next_DoesNotRequirePoolOrSessionsToFinish()
    {
        DataSource.SessionIdAssigner assigner = CreateAssigner(poolSize: 5, lifetime: 20, quantum: 2);

        string[] actual = Enumerable.Range(0, 3).Select(_ => assigner.Next()!).ToArray();

        Assert.Equal(["session-1", "session-1", "session-2"], actual);
    }

    [Fact]
    public void Next_ReturnsNullWhenSessionGenerationIsDisabled()
    {
        var assigner = new DataSource.SessionIdAssigner(false, 1, 1, 1, 1);

        Assert.Null(assigner.Next());
    }

    [Theory]
    [InlineData(0, 1, 1, 1)]
    [InlineData(2, 1, 1, 1)]
    [InlineData(1, 1, 0, 1)]
    [InlineData(1, 1, 1, 0)]
    public void Constructor_RejectsInvalidSchedulingSettings(int minDocs, int maxDocs, int poolSize, int quantum)
    {
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            new DataSource.SessionIdAssigner(true, minDocs, maxDocs, poolSize, quantum));
    }

    [Fact]
    public async Task Next_IsThreadSafeForSharedSyntheticScheduling()
    {
        DataSource.SessionIdAssigner assigner = CreateAssigner(poolSize: 100, lifetime: 10, quantum: 1);
        var assigned = new ConcurrentBag<string>();

        Task[] callers = Enumerable.Range(0, 20)
            .Select(_ => Task.Run(() =>
            {
                for (int i = 0; i < 500; i++)
                {
                    assigned.Add(assigner.Next()!);
                }
            }))
            .ToArray();

        await Task.WhenAll(callers);

        Assert.Equal(10_000, assigned.Count);
        Dictionary<string, int> counts = assigned.GroupBy(id => id).ToDictionary(group => group.Key, group => group.Count());
        Assert.Equal(1_000, counts.Count);
        Assert.All(counts.Values, count => Assert.Equal(10, count));
    }

    private static DataSource.SessionIdAssigner CreateAssigner(int poolSize, int lifetime, int quantum)
    {
        int nextId = 0;
        return new DataSource.SessionIdAssigner(
            enabled: true,
            minDocs: lifetime,
            maxDocs: lifetime,
            poolSize,
            quantum,
            sessionIdFactory: () => $"session-{Interlocked.Increment(ref nextId)}",
            sessionSizeFactory: () => lifetime);
    }

    private static void AssertRunsDoNotExceed(IReadOnlyList<string> ids, int maximumRunLength)
    {
        int runLength = 0;
        string? previous = null;
        foreach (string id in ids)
        {
            runLength = id == previous ? runLength + 1 : 1;
            Assert.True(runLength <= maximumRunLength, $"Session {id} exceeded a run of {maximumRunLength} documents.");
            previous = id;
        }
    }
}

public sealed class LoadedDocumentSessionTests
{
    [Fact]
    public void PrepareLoadedDoc_PreservesPreprocessedSessionId()
    {
        var document = new JsonObject
        {
            ["docid"] = "doc-1",
            ["sessionid"] = "preprocessed-session",
        };

        JsonObject prepared = DataSource.PrepareLoadedDoc(
            document,
            recordNumber: 1,
            partitionKeyFields: ["sessionid", "docid"],
            documentIdFallbackField: "docid");

        Assert.Equal("preprocessed-session", prepared["sessionid"]!.GetValue<string>());
        Assert.Equal("doc-1", prepared["id"]!.GetValue<string>());
    }

    [Fact]
    public void PrepareLoadedDoc_RejectsUnprocessedHpkDocument()
    {
        var document = new JsonObject
        {
            ["docid"] = "doc-1",
        };

        InvalidDataException error = Assert.Throws<InvalidDataException>(() => DataSource.PrepareLoadedDoc(
            document,
            recordNumber: 1,
            partitionKeyFields: ["sessionid", "docid"],
            documentIdFallbackField: "docid"));

        Assert.Contains("sessionid", error.Message);
    }
}
