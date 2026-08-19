namespace CosmosVectorBench;

public sealed record SearchMetricSnapshot
{
    public required bool Started { get; init; }
    public required bool Finished { get; init; }
    public required double? StartedEpoch { get; init; }
    public required long Completed { get; init; }
    public required long Success { get; init; }
    public required long Errors { get; init; }
    public required double CurrentQueriesPerSec { get; init; }
    public required IReadOnlyList<double> QueryTotalTimeMsSamples { get; init; }
    public required double RequestChargeTotal { get; init; }
}

public sealed record SearchResultSnapshot
{
    public required double? StartedEpoch { get; init; }
    public required double? FinishedEpoch { get; init; }
    public required long Completed { get; init; }
    public required long Success { get; init; }
    public required long Errors { get; init; }
    public required List<double> ThroughputQueriesPerSecSamples { get; init; }
    public required List<double> QueryTotalTimeMsSamples { get; init; }
    public required double RequestChargeTotal { get; init; }
}

public sealed class SearchWorkerMetrics
{
    private readonly BenchmarkConfig _config;
    private readonly object _sync = new();
    private long _completed;
    private long _success;
    private long _errors;
    private double? _startedAt;
    private double? _startedEpoch;
    private double? _finishedAt;
    private double? _finishedEpoch;
    private double? _throughputLastSampleAt;
    private long _throughputLastSampleCompleted;
    private readonly List<double> _throughputSamples = [];
    private readonly List<double> _queryTotalTimeMsSamples = [];
    private double _requestChargeTotal;

    public SearchWorkerMetrics(BenchmarkConfig config) => _config = config;

    public void RecordStarted()
    {
        lock (_sync)
        {
            if (_startedAt is not null)
            {
                return;
            }

            _startedAt = Clock.Now;
            _startedEpoch = Clock.Epoch;
            _throughputLastSampleAt = _startedAt;
        }
    }

    public void RecordQueryCompleted(bool success, double requestCharge, double queryTotalTimeMs)
    {
        lock (_sync)
        {
            _completed++;
            if (success)
            {
                _success++;
            }
            else
            {
                _errors++;
            }

            _requestChargeTotal += Math.Max(requestCharge, 0.0);
            if (queryTotalTimeMs > 0)
            {
                _queryTotalTimeMsSamples.Add(queryTotalTimeMs);
            }
        }
    }

    public void RecordFinished()
    {
        RecordThroughputSample(force: true);
        lock (_sync)
        {
            _finishedAt = Clock.Now;
            _finishedEpoch = Clock.Epoch;
        }
    }

    public void RecordThroughputSample(bool force = false)
    {
        lock (_sync)
        {
            if (_startedAt is null)
            {
                return;
            }

            if (_finishedAt is not null)
            {
                return;
            }

            double now = Clock.Now;
            if (_throughputLastSampleAt is not double lastSampleAt)
            {
                _throughputLastSampleAt = now;
                _throughputLastSampleCompleted = _completed;
                return;
            }

            double elapsed = now - lastSampleAt;
            if (elapsed < _config.MetricsSampleIntervalSec && !force)
            {
                return;
            }

            if (elapsed > 0)
            {
                _throughputSamples.Add((_completed - _throughputLastSampleCompleted) / elapsed);
            }

            _throughputLastSampleAt = now;
            _throughputLastSampleCompleted = _completed;
        }
    }

    public SearchMetricSnapshot LiveSnapshot()
    {
        lock (_sync)
        {
            double elapsed = _startedAt is null ? 0.0 : Math.Max(Clock.Now - _startedAt.Value, 0.000001);
            double currentThroughput = _throughputSamples.Count > 0
                ? _throughputSamples[^1]
                : Stats.SafeDiv(_completed, elapsed);

            return new SearchMetricSnapshot
            {
                Started = _startedAt is not null,
                Finished = _finishedAt is not null,
                StartedEpoch = _startedEpoch,
                Completed = _completed,
                Success = _success,
                Errors = _errors,
                CurrentQueriesPerSec = currentThroughput,
                QueryTotalTimeMsSamples = [.. _queryTotalTimeMsSamples],
                RequestChargeTotal = _requestChargeTotal,
            };
        }
    }

    public SearchResultSnapshot ResultSnapshot()
    {
        lock (_sync)
        {
            double elapsed = _startedAt is null || _finishedAt is null
                ? 0.0
                : Math.Max(_finishedAt.Value - _startedAt.Value, 0.000001);
            List<double> samples = _throughputSamples.Count > 0
                ? [.. _throughputSamples]
                : Stats.SafeDiv(_completed, elapsed) is double fallback && fallback > 0 ? [fallback] : [];

            return new SearchResultSnapshot
            {
                StartedEpoch = _startedEpoch,
                FinishedEpoch = _finishedEpoch,
                Completed = _completed,
                Success = _success,
                Errors = _errors,
                ThroughputQueriesPerSecSamples = samples,
                QueryTotalTimeMsSamples = [.. _queryTotalTimeMsSamples],
                RequestChargeTotal = _requestChargeTotal,
            };
        }
    }
}