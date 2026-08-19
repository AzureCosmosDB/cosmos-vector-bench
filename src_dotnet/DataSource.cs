using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace CosmosVectorBench;

/// <summary>
/// Runtime document sources for the benchmark: a synthetic generator for fake mode and a streaming
/// JSON/JSONL reader for file mode. Mirrors the behavior of the Python <c>src/data.py</c> module.
/// </summary>
public static class DataSource
{
    public readonly record struct RawDocument(byte[] Payload, string? SessionId);

    public sealed class SessionIdAssigner
    {
        private readonly bool _enabled;
        private readonly int _minDocs;
        private readonly int _maxDocs;
        private int _remaining;
        private string? _sessionId;

        public SessionIdAssigner(BenchmarkConfig config)
        {
            _enabled = config.SessionIdEnabled;
            _minDocs = config.SessionIdMinDocs;
            _maxDocs = config.SessionIdMaxDocs;
        }

        public string? Next()
        {
            if (!_enabled)
            {
                return null;
            }

            if (_remaining == 0)
            {
                _sessionId = NewGuidId();
                _remaining = Random.Shared.Next(_minDocs, _maxDocs + 1);
            }

            _remaining--;
            return _sessionId;
        }
    }

    /// <summary>
    /// Creates one synthetic benchmark document with a unique Cosmos id plus <c>docid</c>, <c>title</c>, <c>text</c>,
    /// and a randomly generated <c>emb</c> embedding vector of <paramref name="vectorDim"/> floats in [-1, 1].
    /// </summary>
    public static JsonObject MakeDoc(int i, string text, int vectorDim, string? sessionId = null)
    {
        var emb = new JsonArray();
        for (int d = 0; d < vectorDim; d++)
        {
            emb.Add(Math.Round(Random.Shared.NextDouble() * 2.0 - 1.0, 8));
        }

        var doc = new JsonObject
        {
            ["id"] = NewGuidId(),
            ["docid"] = NewGuidId(),
            ["title"] = $"Document {i}",
            ["text"] = text,
            ["emb"] = emb,
        };
        if (sessionId is not null)
        {
            doc["sessionid"] = sessionId;
        }

        return doc;
    }

    /// <summary>Generates synthetic document bulks for a contiguous range of values.</summary>
    public static IEnumerable<List<JsonObject>> GenerateBulks(int startInclusive, int endExclusive, int bulkSize, string text, BenchmarkConfig config)
    {
        var sessionIds = new SessionIdAssigner(config);
        for (int bulkStart = startInclusive; bulkStart < endExclusive; bulkStart += bulkSize)
        {
            int bulkEnd = Math.Min(bulkStart + bulkSize, endExclusive);
            var bulk = new List<JsonObject>(bulkEnd - bulkStart);
            for (int i = bulkStart; i < bulkEnd; i++)
            {
                bulk.Add(MakeDoc(i, text, config.FakeDataVectorDim, sessionIds.Next()));
            }

            yield return bulk;
        }
    }

    /// <summary>
    /// Validates a loaded source record and ensures it has a Cosmos id, applying the same rules as the
    /// Python <c>_prepare_loaded_doc</c>: the partition key field is required and ids fall back to the partition
    /// key value.
    /// </summary>
    public static JsonObject PrepareLoadedDoc(
        JsonObject doc,
        long recordNumber,
        BenchmarkConfig config,
        SessionIdAssigner? sessionIds = null)
    {
        if (config.SessionIdEnabled)
        {
            doc["sessionid"] = sessionIds?.Next();
        }

        string[] missingFields = config.PartitionKeyFields
            .Where(field => !doc.TryGetPropertyValue(field, out JsonNode? node) || IsNullOrEmpty(node))
            .ToArray();
        if (missingFields.Length > 0)
        {
            string available = string.Join(", ", doc.Select(kvp => kvp.Key).OrderBy(k => k, StringComparer.Ordinal).Take(20));
            throw new InvalidDataException(
                $"Loaded record {recordNumber} is missing required partition key field(s) '{string.Join(", ", missingFields)}'. Available fields: {available}");
        }

        if (doc.TryGetPropertyValue("id", out JsonNode? idNode) && !IsNullOrEmpty(idNode))
        {
            if (idNode is JsonValue idValue && idValue.TryGetValue(out string? idStr))
            {
                _ = idStr; // already a string id
            }
            else
            {
                doc["id"] = idNode!.ToString();
            }

            return doc;
        }

        if (!doc.TryGetPropertyValue(config.DocumentIdFallbackField, out JsonNode? fallbackNode) || IsNullOrEmpty(fallbackNode))
        {
            throw new InvalidDataException(
                $"Loaded record {recordNumber} is missing id and fallback field '{config.DocumentIdFallbackField}'");
        }

        doc["id"] = fallbackNode!.ToString();
        return doc;
    }

    /// <summary>
    /// Streams JSON records from disk, preparing each one, and invokes <paramref name="onDoc"/> for every
    /// document up to the optional <paramref name="maxDocs"/> cap. Supports jsonl, array, and multiple_values formats.
    /// Returns the number of documents read.
    /// </summary>
    public static long StreamJsonDocs(BenchmarkConfig config, int? maxDocs, Action<JsonObject> onDoc, CancellationToken cancellationToken)
    {
        string path = config.DocJsonPath;
        long docsRead = 0;
        var sessionIds = new SessionIdAssigner(config);

        switch (config.DocJsonFormat)
        {
            case "jsonl":
                docsRead = StreamJsonLines(path, config, maxDocs, onDoc, cancellationToken, sessionIds);
                break;
            case "array":
                docsRead = StreamJsonArray(path, config, maxDocs, onDoc, cancellationToken, sessionIds, multipleValues: false);
                break;
            case "multiple_values":
                docsRead = StreamJsonArray(path, config, maxDocs, onDoc, cancellationToken, sessionIds, multipleValues: true);
                break;
            default:
                throw new InvalidOperationException($"Unsupported DOC_JSON_FORMAT: {config.DocJsonFormat}");
        }

        return docsRead;
    }

    /// <summary>
    /// Streams raw UTF-8 record bytes from disk and invokes <paramref name="onDoc"/> for each record up to the optional
    /// <paramref name="maxDocs"/> cap, returning the number of documents read. The producer does no JSON parsing for
    /// JSONL input, so per-document parsing and partition-key/id preparation happen on the writer workers instead.
    /// </summary>
    public static long StreamRawJsonDocs(BenchmarkConfig config, int? maxDocs, Action<RawDocument> onDoc, CancellationToken cancellationToken)
    {
        var sessionIds = new SessionIdAssigner(config);
        void AddSession(byte[] payload) => onDoc(new RawDocument(payload, sessionIds.Next()));
        return config.DocJsonFormat switch
        {
            "jsonl" => StreamRawJsonLines(config.DocJsonPath, maxDocs, AddSession, cancellationToken),
            "array" => StreamRawJsonArray(config.DocJsonPath, maxDocs, AddSession, cancellationToken, multipleValues: false),
            "multiple_values" => StreamRawJsonArray(config.DocJsonPath, maxDocs, AddSession, cancellationToken, multipleValues: true),
            _ => throw new InvalidOperationException($"Unsupported DOC_JSON_FORMAT: {config.DocJsonFormat}"),
        };
    }

    private static long StreamRawJsonLines(string path, int? maxDocs, Action<byte[]> onDoc, CancellationToken cancellationToken)
    {
        long docsRead = 0;
        using var reader = new StreamReader(OpenRead(path));
        string? line;
        while ((line = reader.ReadLine()) is not null)
        {
            cancellationToken.ThrowIfCancellationRequested();
            string raw = line.Trim();
            if (raw.Length == 0)
            {
                continue;
            }

            onDoc(Encoding.UTF8.GetBytes(raw));
            docsRead++;
            if (maxDocs.HasValue && docsRead >= maxDocs.Value)
            {
                break;
            }
        }

        return docsRead;
    }

    private static long StreamRawJsonArray(string path, int? maxDocs, Action<byte[]> onDoc, CancellationToken cancellationToken, bool multipleValues)
    {
        long docsRead = 0;
        byte[] bytes = File.ReadAllBytes(path);
        var options = new JsonReaderOptions
        {
            AllowTrailingCommas = true,
            CommentHandling = JsonCommentHandling.Skip,
        };
        var reader = new Utf8JsonReader(bytes, options);

        if (multipleValues)
        {
            while (reader.Read())
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (reader.TokenType != JsonTokenType.StartObject)
                {
                    throw new InvalidDataException($"multiple_values record is {reader.TokenType}, expected a JSON object");
                }

                using JsonDocument element = JsonDocument.ParseValue(ref reader);
                onDoc(Encoding.UTF8.GetBytes(element.RootElement.GetRawText()));
                docsRead++;
                if (maxDocs.HasValue && docsRead >= maxDocs.Value)
                {
                    break;
                }
            }

            return docsRead;
        }

        if (!reader.Read() || reader.TokenType != JsonTokenType.StartArray)
        {
            throw new InvalidDataException($"DOC_JSON_FORMAT=array expects a top-level JSON array in {path}");
        }

        while (reader.Read() && reader.TokenType != JsonTokenType.EndArray)
        {
            cancellationToken.ThrowIfCancellationRequested();
            using JsonDocument element = JsonDocument.ParseValue(ref reader);
            if (element.RootElement.ValueKind != JsonValueKind.Object)
            {
                throw new InvalidDataException($"Loaded record {docsRead + 1} is {element.RootElement.ValueKind}, expected a JSON object");
            }

            onDoc(Encoding.UTF8.GetBytes(element.RootElement.GetRawText()));
            docsRead++;
            if (maxDocs.HasValue && docsRead >= maxDocs.Value)
            {
                break;
            }
        }

        return docsRead;
    }

    private static long StreamJsonLines(
        string path,
        BenchmarkConfig config,
        int? maxDocs,
        Action<JsonObject> onDoc,
        CancellationToken cancellationToken,
        SessionIdAssigner sessionIds)
    {
        long docsRead = 0;
        long lineNumber = 0;
        using var reader = new StreamReader(OpenRead(path));
        string? line;
        while ((line = reader.ReadLine()) is not null)
        {
            cancellationToken.ThrowIfCancellationRequested();
            lineNumber++;
            string raw = line.Trim();
            if (raw.Length == 0)
            {
                continue;
            }

            JsonObject doc;
            try
            {
                doc = ParseObject(raw);
            }
            catch (JsonException ex)
            {
                throw new InvalidDataException($"Invalid JSONL record at line {lineNumber}: {ex.Message}", ex);
            }

            onDoc(PrepareLoadedDoc(doc, lineNumber, config, sessionIds));
            docsRead++;
            if (maxDocs.HasValue && docsRead >= maxDocs.Value)
            {
                break;
            }
        }

        return docsRead;
    }

    private static long StreamJsonArray(
        string path,
        BenchmarkConfig config,
        int? maxDocs,
        Action<JsonObject> onDoc,
        CancellationToken cancellationToken,
        SessionIdAssigner sessionIds,
        bool multipleValues)
    {
        // Stream-parse either a top-level array of objects, or (multipleValues) a concatenation of JSON values.
        long docsRead = 0;
        using var stream = OpenRead(path);
        var options = new JsonDocumentOptions
        {
            AllowTrailingCommas = true,
            CommentHandling = JsonCommentHandling.Skip,
        };

        // For large corpora we read fully into a Utf8JsonReader-friendly buffer via JsonDocument per top-level value.
        // multiple_values: parse sequential root values; array: enumerate the single root array.
        byte[] bytes = File.ReadAllBytes(path);
        var jsonReaderOptions = new JsonReaderOptions
        {
            AllowTrailingCommas = true,
            CommentHandling = JsonCommentHandling.Skip,
        };

        var reader = new Utf8JsonReader(bytes, jsonReaderOptions);

        if (multipleValues)
        {
            while (ReadRootValue(ref reader, out JsonObject? obj))
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (obj is null)
                {
                    continue;
                }

                onDoc(PrepareLoadedDoc(obj, docsRead + 1, config, sessionIds));
                docsRead++;
                if (maxDocs.HasValue && docsRead >= maxDocs.Value)
                {
                    break;
                }
            }

            return docsRead;
        }

        // array format: advance into the array then read each element object.
        if (!reader.Read() || reader.TokenType != JsonTokenType.StartArray)
        {
            throw new InvalidDataException($"DOC_JSON_FORMAT=array expects a top-level JSON array in {path}");
        }

        while (reader.Read() && reader.TokenType != JsonTokenType.EndArray)
        {
            cancellationToken.ThrowIfCancellationRequested();
            using JsonDocument element = JsonDocument.ParseValue(ref reader);
            if (element.RootElement.ValueKind != JsonValueKind.Object)
            {
                throw new InvalidDataException($"Loaded record {docsRead + 1} is {element.RootElement.ValueKind}, expected a JSON object");
            }

            var obj = (JsonObject)JsonNode.Parse(element.RootElement.GetRawText())!;
            onDoc(PrepareLoadedDoc(obj, docsRead + 1, config, sessionIds));
            docsRead++;
            if (maxDocs.HasValue && docsRead >= maxDocs.Value)
            {
                break;
            }
        }

        return docsRead;
    }

    private static bool ReadRootValue(ref Utf8JsonReader reader, out JsonObject? obj)
    {
        obj = null;
        if (!reader.Read())
        {
            return false;
        }

        if (reader.TokenType is JsonTokenType.None)
        {
            return false;
        }

        using JsonDocument doc = JsonDocument.ParseValue(ref reader);
        if (doc.RootElement.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidDataException($"multiple_values record is {doc.RootElement.ValueKind}, expected a JSON object");
        }

        obj = (JsonObject)JsonNode.Parse(doc.RootElement.GetRawText())!;
        return true;
    }

    private static JsonObject ParseObject(string raw)
    {
        JsonNode? node = JsonNode.Parse(raw);
        if (node is not JsonObject obj)
        {
            throw new InvalidDataException($"Loaded record is {(node?.GetType().Name ?? "null")}, expected a JSON object");
        }

        return obj;
    }

    private static FileStream OpenRead(string path)
    {
        return new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, bufferSize: 1 << 20, FileOptions.SequentialScan);
    }

    private static bool IsNullOrEmpty(JsonNode? node)
    {
        if (node is null)
        {
            return true;
        }

        if (node is JsonValue value && value.TryGetValue(out string? s))
        {
            return string.IsNullOrEmpty(s);
        }

        return false;
    }

    private static string NewGuidId() => Guid.NewGuid().ToString("D");
}
