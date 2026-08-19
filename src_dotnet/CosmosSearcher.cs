using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.Azure.Cosmos;

namespace CosmosVectorBench;

public sealed record PartitionValueSample(
    string Field,
    IReadOnlyList<string> Values,
    double ElapsedTimeSec,
    double RequestCharge);

public sealed class CosmosSearcher
{
    private const int PartitionSampleSize = 100;
    private readonly BenchmarkConfig _config;
    private readonly Container _container;
    private readonly string _vectorExpression;
    private int _errorSamplesLogged;
    private int _diagnosticSamplesLogged;

    public CosmosSearcher(BenchmarkConfig config, Container container)
    {
        _config = config;
        _container = container;
        _vectorExpression = BuildPropertyExpression(config.CosmosVectorPath);
    }

    public async Task<PartitionValueSample> SamplePartitionValuesAsync(CancellationToken cancellationToken)
    {
        string field = _config.PartitionKeyFields[0];
        string fieldExpression = BuildPropertyExpression('/' + field);
        var query = new QueryDefinition(
            $"SELECT DISTINCT VALUE {fieldExpression} FROM c WHERE IS_DEFINED({fieldExpression})");
        var options = new QueryRequestOptions
        {
            MaxConcurrency = -1,
            MaxItemCount = -1,
        };

        double startedAt = Clock.Now;
        double requestCharge = 0.0;
        long valuesSeen = 0;
        var reservoir = new List<string>(PartitionSampleSize);
        using FeedIterator<string> iterator = _container.GetItemQueryIterator<string>(query, requestOptions: options);
        while (iterator.HasMoreResults)
        {
            FeedResponse<string> response = await iterator.ReadNextAsync(cancellationToken).ConfigureAwait(false);
            requestCharge += response.RequestCharge;
            foreach (string value in response)
            {
                if (string.IsNullOrWhiteSpace(value))
                {
                    continue;
                }

                valuesSeen++;
                if (reservoir.Count < PartitionSampleSize)
                {
                    reservoir.Add(value);
                    continue;
                }

                long replacementIndex = Random.Shared.NextInt64(valuesSeen);
                if (replacementIndex < PartitionSampleSize)
                {
                    reservoir[(int)replacementIndex] = value;
                }
            }
        }

        if (reservoir.Count < PartitionSampleSize)
        {
            throw new InvalidOperationException(
                $"Search mode requires at least {PartitionSampleSize} distinct {field} values, but found {reservoir.Count}.");
        }

        return new PartitionValueSample(field, reservoir, Clock.Now - startedAt, requestCharge);
    }

    public async Task RunWorkerAsync(
        int queryCount,
        IReadOnlyList<string> partitionValues,
        SearchWorkerMetrics metrics,
        CancellationToken cancellationToken)
    {
        metrics.RecordStarted();
        double workerStartedAt = Clock.Now;
        double intervalSec = 1.0 / _config.SearchQueriesPerSecond;
        var pending = new List<Task>(queryCount);

        for (int queryIndex = 0; queryIndex < queryCount; queryIndex++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            double dueAt = workerStartedAt + queryIndex * intervalSec;
            double delaySec = dueAt - Clock.Now;
            if (delaySec > 0)
            {
                await Task.Delay(TimeSpan.FromSeconds(delaySec), cancellationToken).ConfigureAwait(false);
            }

            string partitionValue = partitionValues[Random.Shared.Next(partitionValues.Count)];
            pending.Add(ExecuteAndRecordAsync(partitionValue, metrics, cancellationToken));
        }

        await Task.WhenAll(pending).ConfigureAwait(false);
        metrics.RecordFinished();
    }

    private async Task ExecuteAndRecordAsync(
        string partitionValue,
        SearchWorkerMetrics metrics,
        CancellationToken cancellationToken)
    {
        double requestCharge = 0.0;
        double queryTotalTimeMs = 0.0;
        bool success = false;

        try
        {
            float[] vector = CreateRandomVector(_config.CosmosVectorDimensions);
            string queryText =
                $"SELECT TOP 10 c.id, c.docid, c.sessionid, " +
                $"VectorDistance({_vectorExpression}, @vector) AS score FROM c " +
                $"ORDER BY VectorDistance({_vectorExpression}, @vector)";
            var query = new QueryDefinition(queryText).WithParameter("@vector", vector);
            var options = new QueryRequestOptions
            {
                PartitionKey = BuildPartitionKey(partitionValue),
                MaxConcurrency = -1,
                MaxItemCount = 10,
            };

            using FeedIterator<JsonObject> iterator = _container.GetItemQueryIterator<JsonObject>(query, requestOptions: options);
            while (iterator.HasMoreResults)
            {
                FeedResponse<JsonObject> response = await iterator.ReadNextAsync(cancellationToken).ConfigureAwait(false);
                requestCharge += response.RequestCharge;
                try
                {
                    queryTotalTimeMs += response.Diagnostics.GetQueryMetrics().CumulativeMetrics.TotalTime.TotalMilliseconds;
                }
                catch (Exception ex)
                {
                    if (Interlocked.Increment(ref _diagnosticSamplesLogged) <= _config.CosmosErrorSampleLimit)
                    {
                        Console.Error.WriteLine($"search_query_metrics_unavailable={ex.Message}");
                    }
                }
            }

            success = true;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (CosmosException ex)
        {
            requestCharge += ex.RequestCharge;
            LogErrorSample($"status={(int)ex.StatusCode}, substatus={ex.SubStatusCode}, message={ex.Message}");
        }
        catch (Exception ex)
        {
            LogErrorSample($"type={ex.GetType().Name}, message={ex.Message}");
        }
        finally
        {
            metrics.RecordQueryCompleted(success, requestCharge, queryTotalTimeMs);
        }
    }

    private PartitionKey BuildPartitionKey(string value)
    {
        if (_config.PartitionKeyFields.Count == 1)
        {
            return new PartitionKey(value);
        }

        return new PartitionKeyBuilder().Add(value).Build();
    }

    private void LogErrorSample(string detail)
    {
        if (Interlocked.Increment(ref _errorSamplesLogged) <= _config.CosmosErrorSampleLimit)
        {
            Console.Error.WriteLine($"search_error_sample={detail}");
        }
    }

    private static float[] CreateRandomVector(int dimensions)
    {
        var vector = new float[dimensions];
        for (int index = 0; index < vector.Length; index++)
        {
            vector[index] = (float)(Random.Shared.NextDouble() * 2.0 - 1.0);
        }

        return vector;
    }

    private static string BuildPropertyExpression(string path)
    {
        string[] segments = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
        return "c" + string.Concat(segments.Select(segment => $"[\"{segment}\"]"));
    }
}