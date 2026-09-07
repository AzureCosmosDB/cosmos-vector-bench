using System.Globalization;
using System.Text.Json;
using CosmosVectorBench.AddSessionIds;

return AddSessionIdsProgram.Run(args);

public static class AddSessionIdsProgram
{
    public static int Run(string[] args)
    {
        try
        {
            Options options = ParseArgs(args);
            if (options.Help)
            {
                PrintUsage(Console.Out);
                return 0;
            }

            PreprocessSummary summary = JsonlSessionIdPreprocessor.PreprocessJsonl(
                options.Input!,
                options.Output!,
                minSessionDocs: options.MinSessionDocs,
                maxSessionDocs: options.MaxSessionDocs,
                seed: options.Seed,
                sessionPoolSize: options.SessionPoolSize,
                force: options.Force,
                progress: message => Console.Error.WriteLine(message),
                progressEvery: options.ProgressEvery);
            Console.Out.WriteLine(JsonSerializer.Serialize(summary));
            return 0;
        }
        catch (CliException exception)
        {
            Console.Error.WriteLine($"error: {exception.Message}");
            Console.Error.WriteLine("Use --help for usage.");
            return 2;
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or ArgumentException or InvalidDataException)
        {
            Console.Error.WriteLine($"error: {exception.Message}");
            return 1;
        }
    }

    internal static Options ParseArgs(IReadOnlyList<string> args)
    {
        Options options = new();
        for (int index = 0; index < args.Count; index++)
        {
            string argument = args[index];
            switch (argument)
            {
                case "-h" or "--help":
                    options.Help = true;
                    break;
                case "--input":
                    options.Input = NextValue(args, ref index, argument);
                    break;
                case "--output":
                    options.Output = NextValue(args, ref index, argument);
                    break;
                case "--min-session-docs":
                    options.MinSessionDocs = PositiveInt(NextValue(args, ref index, argument), argument);
                    break;
                case "--max-session-docs":
                    options.MaxSessionDocs = PositiveInt(NextValue(args, ref index, argument), argument);
                    break;
                case "--seed":
                    options.Seed = IntValue(NextValue(args, ref index, argument), argument);
                    break;
                case "--session-pool-size":
                    options.SessionPoolSize = PositiveInt(NextValue(args, ref index, argument), argument);
                    break;
                case "--progress-every":
                    options.ProgressEvery = PositiveInt(NextValue(args, ref index, argument), argument);
                    break;
                case "--force":
                    options.Force = true;
                    break;
                default:
                    throw new CliException($"Unknown argument: {argument}");
            }
        }

        if (!options.Help)
        {
            if (string.IsNullOrWhiteSpace(options.Input))
            {
                throw new CliException("--input is required.");
            }
            if (string.IsNullOrWhiteSpace(options.Output))
            {
                throw new CliException("--output is required.");
            }
            if (options.MaxSessionDocs < options.MinSessionDocs)
            {
                throw new CliException("--max-session-docs must be greater than or equal to --min-session-docs.");
            }
            if (options.SessionPoolSize < 2)
            {
                throw new CliException("--session-pool-size must be greater than or equal to 2.");
            }
        }

        return options;
    }

    internal static void PrintUsage(TextWriter writer)
    {
        writer.WriteLine("Add deterministic, non-consecutive session IDs to an uncompressed JSONL corpus.");
        writer.WriteLine();
        writer.WriteLine("Usage:");
        writer.WriteLine("  dotnet run --project tools/AddSessionIds -- --input <path> --output <path> [options]");
        writer.WriteLine();
        writer.WriteLine("Options:");
        writer.WriteLine("  --input <path>              Source uncompressed JSONL file (required).");
        writer.WriteLine("  --output <path>             Destination JSONL file, different from input (required).");
        writer.WriteLine($"  --min-session-docs <n>      Minimum uses per completed session; EOF sessions may be smaller (default: {JsonlSessionIdPreprocessor.DefaultMinSessionDocs}).");
        writer.WriteLine($"  --max-session-docs <n>      Maximum uses per session (default: {JsonlSessionIdPreprocessor.DefaultMaxSessionDocs}).");
        writer.WriteLine($"  --seed <n>                  Deterministic .NET random seed (default: {JsonlSessionIdPreprocessor.DefaultSeed}).");
        writer.WriteLine($"  --session-pool-size <n>     Concurrent active session slots, at least 2 (default: {JsonlSessionIdPreprocessor.DefaultSessionPoolSize:N0}).");
        writer.WriteLine($"  --progress-every <n>        Report after this many documents (default: {JsonlSessionIdPreprocessor.DefaultProgressEvery:N0}).");
        writer.WriteLine("  --force                     Replace an existing output file.");
        writer.WriteLine("  -h, --help                  Show this help.");
    }

    private static string NextValue(IReadOnlyList<string> args, ref int index, string argument)
    {
        index++;
        if (index >= args.Count)
        {
            throw new CliException($"{argument} requires a value.");
        }
        return args[index];
    }

    private static int PositiveInt(string value, string argument)
    {
        string normalized = value.Replace("_", string.Empty, StringComparison.Ordinal).Replace(",", string.Empty, StringComparison.Ordinal);
        if (!int.TryParse(normalized, NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed) || parsed < 1)
        {
            throw new CliException($"{argument} must be an integer greater than or equal to 1.");
        }
        return parsed;
    }

    private static int IntValue(string value, string argument)
    {
        if (!int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out int parsed))
        {
            throw new CliException($"{argument} must be an integer.");
        }
        return parsed;
    }

    internal sealed class Options
    {
        public string? Input { get; set; }
        public string? Output { get; set; }
        public int MinSessionDocs { get; set; } = JsonlSessionIdPreprocessor.DefaultMinSessionDocs;
        public int MaxSessionDocs { get; set; } = JsonlSessionIdPreprocessor.DefaultMaxSessionDocs;
        public int Seed { get; set; } = JsonlSessionIdPreprocessor.DefaultSeed;
        public int SessionPoolSize { get; set; } = JsonlSessionIdPreprocessor.DefaultSessionPoolSize;
        public int ProgressEvery { get; set; } = JsonlSessionIdPreprocessor.DefaultProgressEvery;
        public bool Force { get; set; }
        public bool Help { get; set; }
    }

    private sealed class CliException(string message) : Exception(message);
}
