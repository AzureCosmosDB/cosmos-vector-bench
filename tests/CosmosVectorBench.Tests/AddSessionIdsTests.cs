using System.Text;
using System.Text.Json;
using CosmosVectorBench.AddSessionIds;
using Xunit;

namespace CosmosVectorBench.Tests;

public sealed class AddSessionIdsTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), $"add-sessionids-tests-{Guid.NewGuid():N}");

    public AddSessionIdsTests() => Directory.CreateDirectory(_root);

    public void Dispose() => Directory.Delete(_root, recursive: true);

    [Fact]
    public void LargeRollingScheduleRespectsBoundsAndAdjacency()
    {
        RollingSessionAssigner assigner = new(10, 100, 1_000, 42);
        List<string> assignments = Enumerable.Range(0, 250_000).Select(_ => assigner.Next()).ToList();
        int[] counts = assignments.GroupBy(value => value).Select(group => group.Count()).ToArray();

        Assert.All(assignments.Zip(assignments.Skip(1)), pair => Assert.NotEqual(pair.First, pair.Second));
        Assert.True(counts.Max() <= 100);
        Assert.True(counts.Count(count => count < 10) <= assigner.IncompleteSessions);
        Assert.True(assigner.IncompleteSessions <= 1_000);
        Assert.Equal(counts.Length, assigner.SessionsCreated);
        Assert.Equal(assigner.SessionsCreated, assigner.CompletedSessions + assigner.IncompleteSessions);
    }

    [Fact]
    public void SlotsAreCreatedLazily()
    {
        RollingSessionAssigner assigner = new(10, 100, 1_000, 42);
        string[] assignments = Enumerable.Range(0, 5).Select(_ => assigner.Next()).ToArray();

        Assert.Equal(5, assigner.SessionsCreated);
        Assert.Equal(5, assigner.IncompleteSessions);
        Assert.Equal(5, assignments.Distinct().Count());
    }

    [Fact]
    public void PreprocessPreservesOrderAndOverwritesExistingIds()
    {
        string input = WriteInput(5_000, existingSessionIds: true);
        string output = Path.Combine(_root, "output.json");

        PreprocessSummary summary = JsonlSessionIdPreprocessor.PreprocessJsonl(input, output, sessionPoolSize: 100);
        List<JsonElement> documents = ReadDocuments(output);
        int[] counts = documents.GroupBy(document => document.GetProperty("sessionid").GetString()).Select(group => group.Count()).ToArray();

        Assert.Equal(5_000, summary.DocumentsProcessed);
        Assert.Equal(5_000, summary.ExistingSessionIdsOverwritten);
        Assert.Equal(100, summary.SessionPoolSize);
        Assert.Equal(Enumerable.Range(0, 5_000), documents.Select(document => document.GetProperty("position").GetInt32()));
        Assert.All(counts, count => Assert.InRange(count, 1, 100));
        Assert.All(documents.Zip(documents.Skip(1)), pair =>
            Assert.NotEqual(pair.First.GetProperty("sessionid").GetString(), pair.Second.GetProperty("sessionid").GetString()));
        Assert.All(documents, document => Assert.Equal(document.GetProperty("position").GetInt32(), document.GetProperty("nested").GetProperty("value").GetInt32()));
        Assert.Equal(summary.SessionsCreated, summary.CompletedSessions + summary.IncompleteSessions);
        Assert.True(summary.IncompleteSessions <= 100);
    }

    [Fact]
    public void SameSeedProducesByteIdenticalOutputAndDifferentSeedDoesNot()
    {
        string input = WriteInput(2_000);
        string first = Path.Combine(_root, "first.json");
        string second = Path.Combine(_root, "second.json");
        string third = Path.Combine(_root, "third.json");

        JsonlSessionIdPreprocessor.PreprocessJsonl(input, first, seed: 42);
        JsonlSessionIdPreprocessor.PreprocessJsonl(input, second, seed: 42);
        JsonlSessionIdPreprocessor.PreprocessJsonl(input, third, seed: 43);

        Assert.Equal(File.ReadAllBytes(first), File.ReadAllBytes(second));
        Assert.NotEqual(File.ReadAllBytes(first), File.ReadAllBytes(third));
    }

    [Fact]
    public void EmptyInputCreatesEmptyBomlessOutput()
    {
        string input = WriteInput(0, includeBom: true);
        string output = Path.Combine(_root, "output.json");

        PreprocessSummary summary = JsonlSessionIdPreprocessor.PreprocessJsonl(input, output);

        Assert.Equal(0, summary.SessionsCreated);
        Assert.Equal(0, summary.IncompleteSessions);
        Assert.Empty(File.ReadAllBytes(output));
    }

    [Fact]
    public void ExistingOutputRequiresForce()
    {
        string input = WriteInput(20);
        string output = Path.Combine(_root, "output.json");
        File.WriteAllText(output, "existing");

        IOException exception = Assert.Throws<IOException>(() => JsonlSessionIdPreprocessor.PreprocessJsonl(input, output));
        Assert.Contains("--force", exception.Message);
        Assert.Equal("existing", File.ReadAllText(output));

        JsonlSessionIdPreprocessor.PreprocessJsonl(input, output, force: true);
        Assert.Equal(20, ReadDocuments(output).Count);
    }

    [Fact]
    public void InvalidJsonReportsLineAndLeavesNoOutputOrTempFile()
    {
        string input = Path.Combine(_root, "input.json");
        string output = Path.Combine(_root, "output.json");
        File.WriteAllText(input, "{\"docid\":\"one\"}\nnot-json\n", new UTF8Encoding(false));

        InvalidDataException exception = Assert.Throws<InvalidDataException>(() =>
            JsonlSessionIdPreprocessor.PreprocessJsonl(input, output));

        Assert.Contains("line 2", exception.Message);
        Assert.False(File.Exists(output));
        Assert.Empty(Directory.GetFiles(_root, "*.tmp"));
    }

    [Fact]
    public void NonObjectRecordIsRejected()
    {
        string input = Path.Combine(_root, "input.json");
        File.WriteAllText(input, "[]\n", new UTF8Encoding(false));

        InvalidDataException exception = Assert.Throws<InvalidDataException>(() =>
            JsonlSessionIdPreprocessor.PreprocessJsonl(input, Path.Combine(_root, "output.json")));

        Assert.Contains("expected an object", exception.Message);
    }

    [Fact]
    public void InputOutputCompressionAndOptionsAreValidated()
    {
        string input = WriteInput(20);
        Assert.Throws<ArgumentException>(() => JsonlSessionIdPreprocessor.PreprocessJsonl(input, input, force: true));
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            JsonlSessionIdPreprocessor.PreprocessJsonl(input, Path.Combine(_root, "output.json"), sessionPoolSize: 1));
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            JsonlSessionIdPreprocessor.PreprocessJsonl(input, Path.Combine(_root, "output.json"), progressEvery: 0));

        string compressed = Path.Combine(_root, "input.bz2");
        File.WriteAllText(compressed, "unused");
        Assert.Throws<ArgumentException>(() =>
            JsonlSessionIdPreprocessor.PreprocessJsonl(compressed, Path.Combine(_root, "output.json")));
    }

    [Fact]
    public void ProgressReportsOnePassCountsAndCompletion()
    {
        string input = WriteInput(5, existingSessionIds: true);
        string output = Path.Combine(_root, "output.json");
        List<string> messages = [];

        JsonlSessionIdPreprocessor.PreprocessJsonl(input, output, progress: messages.Add, progressEvery: 2);

        Assert.Contains(messages, message => message.Contains("Processed 2 JSON documents", StringComparison.Ordinal));
        Assert.Contains(messages, message => message.Contains("replaced 4 existing session IDs", StringComparison.Ordinal));
        Assert.DoesNotContain(messages, message => message.Contains("Inspected", StringComparison.Ordinal) || message.Contains('%'));
        Assert.Contains("added 5 session ID assignments", messages[^1]);
        Assert.DoesNotContain(messages, message => message.Contains("unique session", StringComparison.Ordinal));
    }

    [Fact]
    public void InvalidRollingOptionsAreRejected()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new RollingSessionAssigner(0, 100, 1_000, 42));
        Assert.Throws<ArgumentException>(() => new RollingSessionAssigner(101, 100, 1_000, 42));
        Assert.Throws<ArgumentOutOfRangeException>(() => new RollingSessionAssigner(10, 100, 1, 42));
    }

    private string WriteInput(int count, bool existingSessionIds = false, bool includeBom = false)
    {
        string path = Path.Combine(_root, $"input-{Guid.NewGuid():N}.json");
        using StreamWriter writer = new(path, append: false, new UTF8Encoding(includeBom)) { NewLine = "\n" };
        for (int index = 0; index < count; index++)
        {
            Dictionary<string, object> document = new()
            {
                ["docid"] = $"doc-{index}",
                ["position"] = index,
                ["nested"] = new Dictionary<string, int> { ["value"] = index }
            };
            if (existingSessionIds)
            {
                document["sessionid"] = "old-session";
            }
            writer.WriteLine(JsonSerializer.Serialize(document));
            if (index == 1)
            {
                writer.WriteLine();
            }
        }
        return path;
    }

    private static List<JsonElement> ReadDocuments(string path) => File.ReadLines(path)
        .Where(line => !string.IsNullOrWhiteSpace(line))
        .Select(line => JsonSerializer.Deserialize<JsonElement>(line))
        .ToList();
}
