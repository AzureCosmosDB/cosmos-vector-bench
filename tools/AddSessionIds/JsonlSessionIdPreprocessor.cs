using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace CosmosVectorBench.AddSessionIds;

public sealed record PreprocessSummary(
    [property: JsonPropertyName("input")] string Input,
    [property: JsonPropertyName("output")] string Output,
    [property: JsonPropertyName("seed")] int Seed,
    [property: JsonPropertyName("documents_processed")] long DocumentsProcessed,
    [property: JsonPropertyName("sessions_created")] long SessionsCreated,
    [property: JsonPropertyName("session_pool_size")] int SessionPoolSize,
    [property: JsonPropertyName("completed_sessions")] long CompletedSessions,
    [property: JsonPropertyName("incomplete_sessions")] int IncompleteSessions,
    [property: JsonPropertyName("configured_min_session_docs")] int ConfiguredMinSessionDocs,
    [property: JsonPropertyName("configured_max_session_docs")] int ConfiguredMaxSessionDocs,
    [property: JsonPropertyName("actual_min_session_docs")] int ActualMinSessionDocs,
    [property: JsonPropertyName("actual_max_session_docs")] int ActualMaxSessionDocs,
    [property: JsonPropertyName("existing_sessionids_overwritten")] long ExistingSessionIdsOverwritten);

public sealed class RollingSessionAssigner
{
    private readonly int _minDocs;
    private readonly int _maxDocs;
    private readonly SessionSlot?[] _slots;
    private readonly HashSet<string> _activeIds = new(StringComparer.Ordinal);
    private readonly Random _random;
    private int _cursor;
    private int? _completedMin;
    private int _completedMax;

    public RollingSessionAssigner(int minDocs, int maxDocs, int poolSize, int seed)
    {
        if (minDocs < 1)
        {
            throw new ArgumentOutOfRangeException(nameof(minDocs), "Minimum session documents must be at least one.");
        }
        if (maxDocs < minDocs)
        {
            throw new ArgumentException("Maximum session documents must be greater than or equal to the minimum.", nameof(maxDocs));
        }
        if (poolSize < 2)
        {
            throw new ArgumentOutOfRangeException(nameof(poolSize), "Session pool size must be at least two.");
        }

        _minDocs = minDocs;
        _maxDocs = maxDocs;
        _slots = new SessionSlot[poolSize];
        _random = new Random(seed);
    }

    public long SessionsCreated { get; private set; }
    public long CompletedSessions { get; private set; }
    public int IncompleteSessions => _slots.Count(slot => slot is not null);

    public int ActualMinDocs
    {
        get
        {
            int? minimum = _completedMin;
            foreach (SessionSlot? slot in _slots)
            {
                if (slot is not null)
                {
                    minimum = minimum is null ? slot.Count : Math.Min(minimum.Value, slot.Count);
                }
            }
            return minimum ?? 0;
        }
    }

    public int ActualMaxDocs => Math.Max(
        _completedMax,
        _slots.Where(slot => slot is not null).Select(slot => slot!.Count).DefaultIfEmpty(0).Max());

    public string Next()
    {
        int slotIndex = _cursor;
        _cursor = (_cursor + 1) % _slots.Length;
        SessionSlot? slot = _slots[slotIndex];
        if (slot is null)
        {
            string sessionId;
            do
            {
                sessionId = DeterministicUuid(_random);
            }
            while (_activeIds.Contains(sessionId));

            slot = new SessionSlot(sessionId, (int)_random.NextInt64(_minDocs, (long)_maxDocs + 1));
            _slots[slotIndex] = slot;
            _activeIds.Add(sessionId);
            SessionsCreated++;
        }

        slot.Count++;
        if (slot.Count == slot.Target)
        {
            CompletedSessions++;
            _completedMin = _completedMin is null ? slot.Count : Math.Min(_completedMin.Value, slot.Count);
            _completedMax = Math.Max(_completedMax, slot.Count);
            _activeIds.Remove(slot.SessionId);
            _slots[slotIndex] = null;
        }
        return slot.SessionId;
    }

    private static string DeterministicUuid(Random random)
    {
        Span<byte> bytes = stackalloc byte[16];
        random.NextBytes(bytes);
        bytes[6] = (byte)((bytes[6] & 0x0F) | 0x40);
        bytes[8] = (byte)((bytes[8] & 0x3F) | 0x80);
        string hex = Convert.ToHexString(bytes).ToLowerInvariant();
        return string.Create(
            36,
            hex,
            static (destination, value) =>
            {
                value.AsSpan(0, 8).CopyTo(destination);
                destination[8] = '-';
                value.AsSpan(8, 4).CopyTo(destination[9..]);
                destination[13] = '-';
                value.AsSpan(12, 4).CopyTo(destination[14..]);
                destination[18] = '-';
                value.AsSpan(16, 4).CopyTo(destination[19..]);
                destination[23] = '-';
                value.AsSpan(20, 12).CopyTo(destination[24..]);
            });
    }

    private sealed class SessionSlot(string sessionId, int target)
    {
        public string SessionId { get; } = sessionId;
        public int Target { get; } = target;
        public int Count { get; set; }
    }
}

public static class JsonlSessionIdPreprocessor
{
    public const int DefaultMinSessionDocs = 10;
    public const int DefaultMaxSessionDocs = 100;
    public const int DefaultSeed = 42;
    public const int DefaultProgressEvery = 10_000;
    public const int DefaultSessionPoolSize = 1_000;

    public static PreprocessSummary PreprocessJsonl(
        string inputPath,
        string outputPath,
        int minSessionDocs = DefaultMinSessionDocs,
        int maxSessionDocs = DefaultMaxSessionDocs,
        int seed = DefaultSeed,
        int sessionPoolSize = DefaultSessionPoolSize,
        bool force = false,
        Action<string>? progress = null,
        int progressEvery = DefaultProgressEvery)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(inputPath);
        ArgumentException.ThrowIfNullOrWhiteSpace(outputPath);
        if (progressEvery < 1)
        {
            throw new ArgumentOutOfRangeException(nameof(progressEvery), "Progress interval must be at least one.");
        }

        RollingSessionAssigner assigner = new(minSessionDocs, maxSessionDocs, sessionPoolSize, seed);
        string input = Path.GetFullPath(inputPath);
        string output = Path.GetFullPath(outputPath);
        if (string.Equals(input, output, OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal))
        {
            throw new ArgumentException("Input and output paths must be different.");
        }
        if (string.Equals(Path.GetExtension(input), ".bz2", StringComparison.OrdinalIgnoreCase))
        {
            throw new ArgumentException("Compressed input is not supported; decompress the JSONL file first.", nameof(inputPath));
        }
        if (!File.Exists(input))
        {
            throw new FileNotFoundException($"Input file does not exist: {input}", input);
        }

        string outputDirectory = Path.GetDirectoryName(output)!;
        if (!Directory.Exists(outputDirectory))
        {
            throw new DirectoryNotFoundException($"Output directory does not exist: {outputDirectory}");
        }
        if (File.Exists(output) && !force)
        {
            throw new IOException($"Output file already exists: {output}. Use --force to replace it.");
        }

        string tempPath = Path.Combine(outputDirectory, $".{Path.GetFileName(output)}.{Guid.NewGuid():N}.tmp");
        long documentCount = 0;
        long overwrittenCount = 0;
        try
        {
            {
                using StreamReader reader = new(input, Encoding.UTF8, detectEncodingFromByteOrderMarks: true);
                using FileStream outputFile = new(tempPath, FileMode.CreateNew, FileAccess.Write, FileShare.None);
                using StreamWriter writer = new(outputFile, new UTF8Encoding(encoderShouldEmitUTF8Identifier: false), leaveOpen: true)
                {
                    NewLine = "\n"
                };

                long lineNumber = 0;
                while (reader.ReadLine() is { } line)
                {
                    lineNumber++;
                    if (string.IsNullOrWhiteSpace(line))
                    {
                        continue;
                    }

                    JsonObject document = ParseObject(line, lineNumber);
                    if (document.ContainsKey("sessionid"))
                    {
                        overwrittenCount++;
                    }
                    document["sessionid"] = assigner.Next();
                    writer.WriteLine(document.ToJsonString(JsonOptions));
                    documentCount++;

                    if (progress is not null && documentCount % progressEvery == 0)
                    {
                        progress(
                            $"Processed {documentCount:N0} JSON documents; added {documentCount:N0} session ID " +
                            $"assignments; replaced {overwrittenCount:N0} existing session IDs...");
                    }
                }

                writer.Flush();
                outputFile.Flush(flushToDisk: true);
            }
            File.Move(tempPath, output, overwrite: force);
            progress?.Invoke(
                $"Completed: processed {documentCount:N0} JSON documents, added {documentCount:N0} session ID " +
                $"assignments, and wrote {output}.");
        }
        finally
        {
            if (File.Exists(tempPath))
            {
                File.Delete(tempPath);
            }
        }

        return new PreprocessSummary(
            input,
            output,
            seed,
            documentCount,
            assigner.SessionsCreated,
            sessionPoolSize,
            assigner.CompletedSessions,
            assigner.IncompleteSessions,
            minSessionDocs,
            maxSessionDocs,
            assigner.ActualMinDocs,
            assigner.ActualMaxDocs,
            overwrittenCount);
    }

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = false
    };

    private static JsonObject ParseObject(string line, long lineNumber)
    {
        try
        {
            JsonNode? node = JsonNode.Parse(line);
            return node as JsonObject ?? throw new InvalidDataException(
                $"JSONL record at line {lineNumber.ToString(CultureInfo.InvariantCulture)} is not an object, expected an object.");
        }
        catch (JsonException exception)
        {
            throw new InvalidDataException(
                $"Invalid JSONL record at line {lineNumber.ToString(CultureInfo.InvariantCulture)}: {exception.Message}",
                exception);
        }
    }
}
