# Cosmos DB Vector Write Throughput Test

This repository contains a standalone throughput test for writing documents to Azure Cosmos DB. It can either generate synthetic documents or stream a JSON/JSONL corpus, including `.bz2`-compressed input. Use `src/download_data.py` to download and decompress the source data into `data/` before running throughput tests; compressed input can limit app-side throughput because the loader must decompress records during the run.

Two interchangeable implementations are provided. Both read the same root `.env`, the same scenario configs, and the same data files, and both write metrics CSVs with identical columns into `results/`:

- **Python** (`src/`, `main.py`) is the reference implementation.
- **.NET** (`src_dotnet/`) is an alternative .NET 9 port that uses the native Cosmos SDK bulk-execution API. See [.NET Implementation (Alternative)](#net-implementation-alternative).

## File Layout

Python implementation:

- `main.py` is the root command entrypoint and accepts CLI overrides for common benchmark settings.
- `src/benchmark.py` is the internal benchmark entrypoint.
- `src/core.py` contains the Cosmos write path and worker orchestration.
- `src/metrics.py` contains metrics tracking, aggregation, console output, and CSV output.
- `src/data.py` contains runtime fake-doc and JSON/JSONL document sources.
- `src/config.py` loads repo-root `.env` and benchmark configuration.
- `src/download_data.py` downloads source datasets into `data/` and can optionally decompress `.bz2` files.
- `counts.py` streams a JSON/JSONL corpus and compares total records with unique `docid` values.

.NET implementation (`src_dotnet/`):

- `Program.cs` is the command entrypoint and parses the same CLI overrides as `main.py`.
- `Benchmark.cs` and `Worker.cs` contain worker orchestration for fake and file modes.
- `CosmosWriter.cs` contains the Cosmos write path (uses `AllowBulkExecution`).
- `CosmosClientFactory.cs` builds the Cosmos client and System.Text.Json serializer.
- `DataSource.cs` contains the fake-doc generator and JSON/JSONL document streaming.
- `BenchmarkConfig.cs` loads the same repo-root `.env` and benchmark configuration.
- `Metrics.cs` and `MetricsReporter.cs` track metrics and write the same CSV columns.

## Scenarios

- [OpenAI vector corpus scenarios](scenarios/README.md) describes how to setup using data from ESRally's OpenAI vector corpus setup, scenario infrastructure files, and helper scripts.

## Get Started Right Away

Before the benchmark setup, create a Python environment and install dependencies:

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
az login
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
az login
```

### Cosmos DB Permissions

Cosmos DB uses separate permission planes for these workflows:

| Workflow | Required role | Permission plane |
|---|---|---|
| Container creation through Bicep, scripts, or Azure Resource Manager | `Cosmos DB Operator` | Azure control plane RBAC |
| Data insertion with `DefaultAzureCredential` / Entra ID | `Cosmos DB Built-in Data Contributor` | Cosmos DB native data plane RBAC |

If you set `COSMOS_KEY`, the benchmark uses key-based data-plane access for inserts. If `COSMOS_KEY` is blank, assign the data-plane role below to the signed-in user, group, managed identity, or service principal running the benchmark.

Bash:

```bash
RESOURCE_GROUP="myResourceGroup"
ACCOUNT_NAME="mycosmosaccount"
SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"

ACCOUNT_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.DocumentDB/databaseAccounts/$ACCOUNT_NAME"

az role assignment create \
   --assignee "$PRINCIPAL_ID" \
   --role "Cosmos DB Operator" \
   --scope "$ACCOUNT_SCOPE"

DATA_ROLE_ID="00000000-0000-0000-0000-000000000002"

az cosmosdb sql role assignment create \
   --account-name "$ACCOUNT_NAME" \
   --resource-group "$RESOURCE_GROUP" \
   --role-definition-id "$DATA_ROLE_ID" \
   --principal-id "$PRINCIPAL_ID" \
   --scope "/dbs"
```

PowerShell using Azure CLI:

```powershell
$ResourceGroup = "myResourceGroup"
$AccountName = "mycosmosaccount"
$SubscriptionId = az account show --query id -o tsv
$PrincipalId = az ad signed-in-user show --query id -o tsv

$AccountScope = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroup/providers/Microsoft.DocumentDB/databaseAccounts/$AccountName"

az role assignment create `
   --assignee $PrincipalId `
   --role "Cosmos DB Operator" `
   --scope $AccountScope

$DataRoleId = "00000000-0000-0000-0000-000000000002"

az cosmosdb sql role assignment create `
   --account-name $AccountName `
   --resource-group $ResourceGroup `
   --role-definition-id $DataRoleId `
   --principal-id $PrincipalId `
   --scope "/dbs"
```

The data-plane scope can be narrowed from `/dbs` to `/dbs/<database>` or `/dbs/<database>/colls/<container>`.

1. Configure the Cosmos DB resource, database, and container.

   Create or choose a Cosmos DB for NoSQL account, a database, and a container with the partition key and vector policy you want to test. The script expects the database and container to already exist. It authenticates with `COSMOS_KEY` when that value is set, and falls back to `DefaultAzureCredential` (Entra ID) when it is blank.

   Use a new container, or make sure the target container is empty before each file-based benchmark run. The writer uses create operations, so items that already exist with the same `id` and partition key are not overwritten; they fail as duplicate-item errors.

2. Configure `.env`.

   Set the Cosmos target, source mode, data path, partition key field, and throughput knobs. Some key values are:

   ```dotenv
   COSMOS_ENDPOINT=https://<account>.documents.azure.com:443/
   COSMOS_KEY=
   COSMOS_DATABASE_NAME=testdb
   COSMOS_CONTAINER_NAME=<container>
   DATA_TYPE=file
   DOC_JSON_PATH=./data/data-file.json
   DOC_JSON_FORMAT=jsonl
   PARTITION_KEY_FIELDS=docid
   DOCUMENT_ID_FALLBACK_FIELD=docid
   ```

3. Download the dataset.

   Windows PowerShell:

   ```powershell
   .\.venv\Scripts\python.exe .\src\download_data.py
   ```

   macOS/Linux:

   ```bash
   ./.venv/bin/python ./src/download_data.py
   ```

   This downloads `DATA_URL` into `DATA_DIR` and, by default, decompresses `.bz2` files next to the downloaded archive. Use the decompressed file for throughput runs. The benchmark reader can use either file, but compressed input can limit app-side throughput.

4. Run the benchmark.

   Windows PowerShell:

   ```powershell
   .\.venv\Scripts\python.exe .\main.py --num-clients 4 --container-name <container>
   ```

   macOS/Linux:

   ```bash
   ./.venv/bin/python ./main.py --num-clients 4 --container-name <container>
   ```

Final metrics are printed to the console and written to a CSV file under `results/` when `CSV_OUTPUT_ENABLED=true`:

```text
results/<MMDDYY-HHMMSS>-clients-<N>-bulk-<BULK_SIZE>-maxdocs-<MAX_TOTAL_DOCS-or-all>.csv
```

For example:

```text
results/052326-143508-clients-40-bulk-30-maxdocs-all.csv
```

## .NET Implementation (Alternative)

The `src_dotnet/` project is a .NET 9 port of the benchmark. It reads the same root `.env`, accepts the same CLI overrides as `main.py`, uses the same scenario configs and decompressed data files, and writes metrics CSVs with the same columns into `results/`. Use it when you prefer a .NET client or want to compare client-runtime behavior.

Differences from the Python implementation:

- It uses the native Cosmos SDK `AllowBulkExecution` write path (concurrent point creates batched by the SDK) instead of the Python per-item path, so raw docs/sec is not directly comparable between the two; the CSV schema still matches for side-by-side runs.
- Concurrency uses in-process logical clients (`NUM_CLIENTS` async worker loops) rather than child processes.
- Input must be a decompressed `.json`/`.jsonl` file. Reading `.bz2` directly is not supported, so download and decompress first (`src/download_data.py`).

### Prerequisites

Install the [.NET 9 SDK](https://dotnet.microsoft.com/download). If `COSMOS_KEY` is blank, sign in for `DefaultAzureCredential` authentication. The Cosmos DB permissions described in [Cosmos DB Permissions](#cosmos-db-permissions) apply to the .NET implementation as well.

```powershell
az login
dotnet build .\src_dotnet\CosmosVectorBench.csproj -c Release
```

### Quick examples

Run an insert test using the data and Cosmos DB settings in `.env`:

```powershell
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --partition-key-mode docid --num-clients 4 --total-docs 10000
```

Run a search test against an existing populated container:

```powershell
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --search --partition-key-mode docid --num-clients 100 --queries-per-second 1 --total-queries 10000
```

To bypass the 1,000-query warmup:

```powershell
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --search --warmup false --partition-key-mode docid --num-clients 100 --queries-per-second 1 --total-queries 10000
```

The search command uses `COSMOS_CONTAINER_NAME` from `.env`. Add `--container-name <name>` to either command to select another container. On macOS or Linux, use `/` instead of `\` in the project path.

### Run the benchmark

The CLI arguments match `main.py`: `--num-clients`, `--bulk-size`, `--total-docs`, `--data-path`, `--container-name`, and `--partition-key-mode`. Pass them after `--`.

Fake mode (set `DATA_TYPE=fake` in `.env`):

Windows PowerShell:

```powershell
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --num-clients 4
```

macOS/Linux:

```bash
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --num-clients 4
```

File mode:

Windows PowerShell:

```powershell
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --num-clients 40 --bulk-size 30 --total-docs 100000 --data-path .\data\data-file.json --container-name benchmark-100k
```

macOS/Linux:

```bash
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --num-clients 40 --bulk-size 30 --total-docs 100000 --data-path ./data/data-file.json --container-name benchmark-100k
```

### Run vector searches

The .NET client can run a search-only benchmark against an existing populated container. `--search` (or `--search true`) skips all document generation and inserts, and requires an explicit `--partition-key-mode` so request routing matches the container.

```powershell
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --search --partition-key-mode hpk --container-name s7-quantizedFlat-hpk --num-clients 100 --queries-per-second 5 --total-queries 10000
```

`--queries-per-second` is the target query-start rate for each logical client, defaults to `1`, and accepts `1` through `100`. `--total-queries` is global across all clients and defaults to `1000`. The client divides that total evenly across clients and allows asynchronous queries to overlap so query latency does not silently cap the requested issue rate.

Before timing the workload, the client streams distinct partition values and reservoir-samples 100 uniformly. It samples `docid` in `docid` mode and `sessionid` in `sessionid` or `hpk` mode. The list is retained in memory, and each query randomly selects one value. A container with fewer than 100 distinct values fails setup.

Warmup is enabled by default so a brand-new Cosmos DB container is primed for vector search before measurement. The client runs 1,000 warmup vector search queries at the configured per-client QPS, then starts the measured search timer and metrics only after all warmup queries finish successfully. Warmup latency, throughput, and RU are excluded from search statistics and CSV output. This priming is not needed for a steady-state production container; pass `--warmup false` to skip it, or set `SEARCH_WARMUP_ENABLED=false` in `.env`.

Each query generates a random vector and executes a partition-scoped `TOP 10` `VectorDistance` query with no `WHERE` clause. Scalar modes pass a scalar `QueryRequestOptions.PartitionKey`; `hpk` mode passes the `sessionid` prefix built with `PartitionKeyBuilder`. Query request options set `MaxConcurrency=-1` and `MaxItemCount=10`.

Set `COSMOS_VECTOR_PATH` and `COSMOS_VECTOR_DIMENSIONS` to match the container vector policy. They default to `/emb` and `1536`, matching the checked-in scenarios. Search output reports elapsed time, active/completed clients, completed queries, search queries/sec, average RU/search query, and `query_total_time_ms` from `response.Diagnostics.GetQueryMetrics()`. This TotalTime is server query execution time, not end-to-end client wall-clock latency. Search runs use a separate `search-*.csv` schema and do not alter insert CSV output.

## Use Fake Documents

Fake mode is useful for checking auth, container access, write throughput, and basic throttling without a large source file.

Set:

```dotenv
DATA_TYPE=fake
TOTAL_DOCS=10000
BULK_SIZE=100
MAX_CONCURRENCY=100
PAYLOAD_BYTES=5000
SESSION_ID_ENABLED=false
```

Then run:

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\main.py --num-clients 4
```

macOS/Linux:

```bash
./.venv/bin/python ./main.py --num-clients 4
```

To add a synthetic `sessionid` to either fake or file-input documents, enable session grouping:

```dotenv
SESSION_ID_ENABLED=true
SESSION_ID_MIN_DOCS=10
SESSION_ID_MAX_DOCS=1000
```

Consecutive documents share one random session GUID. A new GUID and group size are generated only when the previous group is exhausted. The final group can be shorter when a bounded run ends. Set `PARTITION_KEY_FIELDS=sessionid` for a session partition key or `PARTITION_KEY_FIELDS=sessionid,docid` for an ordered hierarchical partition key.

For a single run, prefer `--partition-key-mode hpk|docid|sessionid`. The option sets session generation, ordered partition-key fields, and `DOCUMENT_ID_FALLBACK_FIELD=docid` together. When omitted, the benchmark uses process and `.env` settings; the repository default remains `docid`.

## Test Partition Key Strategies

The benchmark can compare three partition-key strategies while keeping the document corpus, vector policy, and client pressure constant:

| Strategy | Container partition paths | Benchmark settings |
|---|---|---|
| Document | `/docid` | `--partition-key-mode docid` |
| Session | `/sessionid` | `--partition-key-mode sessionid` |
| Hierarchical (HPK) | `/sessionid`, `/docid` | `--partition-key-mode hpk` |

The mode option always keeps `DOCUMENT_ID_FALLBACK_FIELD=docid`. Using `sessionid` as the fallback item ID would give documents in the same session duplicate IDs. The selected mode must match the target container paths, including hierarchical order.

The quantizedFlat scenario parameter files provide all three container shapes. The `-docid`, `-sessionid`, and `-hpk` variants use `/docid`, `/sessionid`, and `/sessionid` + `/docid`, respectively. See [Config 7 partition-key tests](scenarios/README.md#config-7) for complete provisioning and benchmark commands at the maximum-throughput scenario settings.

## Use the local data file

Large json files are sometimes distributed as a bz2-compressed JSONL file where each line is a document. First download it:

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\src\download_data.py
```

macOS/Linux:

```bash
./.venv/bin/python ./src/download_data.py
```

By default, the downloader also writes the decompressed `.json` file, which is the recommended input for throughput runs. To download only the `.bz2` archive, run:

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\src\download_data.py --no-decompress
```

macOS/Linux:

```bash
./.venv/bin/python ./src/download_data.py --no-decompress
```

Then configure the writer to read the decompressed JSON file. The benchmark reader can also stream the downloaded `.bz2` file, but compressed input can limit app-side throughput because decompression happens during the benchmark run.

```dotenv
DATA_URL=https://path-to-data-file.json
DATA_DIR=./data
DATA_TYPE=file
DOC_JSON_PATH=./data/datafile-json
DOC_JSON_FORMAT=jsonl
PARTITION_KEY_FIELDS=docid
DOCUMENT_ID_FALLBACK_FIELD=docid
BULK_SIZE=30
MAX_CONCURRENCY=30
DOC_QUEUE_MULTIPLIER=30
```

To stream the compressed file directly, use:

```dotenv
DOC_JSON_PATH=./data/data-file.json.bz2
```

Reading `.bz2` directly avoids keeping the decompressed file, but it spends CPU decompressing during each benchmark run and can limit app-side ingestion throughput. For repeated throughput runs, the decompressed `.json` file is usually the steadier input path.

Run:

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\main.py --num-clients 40
```

macOS/Linux:

```bash
./.venv/bin/python ./main.py --num-clients 40
```

If you want a bounded test run, set:

```dotenv
MAX_TOTAL_DOCS=100000
```

Leave it blank for the full file:

```dotenv
MAX_TOTAL_DOCS=
```

Cosmos DB requires every item to have an `id`, and file-input records must contain every configured `PARTITION_KEY_FIELDS` component after optional session enrichment. If a source document does not already have an `id`, the writer copies `DOCUMENT_ID_FALLBACK_FIELD` into `id`. Keep that fallback as `docid` when partitioning by `sessionid`, otherwise documents in one session would receive duplicate IDs.

## CLI Overrides

Both clients apply CLI arguments before loading benchmark configuration. Provided arguments override matching process and `.env` values for that run, while omitted values still come from the environment. This is useful for reusing one `.env` while targeting a different container or partition-key strategy for a single run.

| Argument | Overrides | Notes |
|---|---|---|
| `--num-clients` | `NUM_CLIENTS` | Number of worker client processes. |
| `--bulk-size` | `BULK_SIZE` | Number of documents in each worker bulk. |
| `--total-docs` | `TOTAL_DOCS`, `MAX_TOTAL_DOCS` | Fake mode document count; JSON mode upload cap. |
| `--data-path` | `DOC_JSON_PATH`, `DATA_TYPE=file` | Uses the provided JSON/JSONL file. Paths ending in `.bz2` are decompressed while reading. |
| `--container-name` | `COSMOS_CONTAINER_NAME` | Target Cosmos DB container name. Wins over `.env` when specified. |
| `--partition-key-mode` | `SESSION_ID_ENABLED`, `PARTITION_KEY_FIELDS`, `DOCUMENT_ID_FALLBACK_FIELD` | Selects `hpk` (`sessionid,docid`), `docid`, or `sessionid` atomically. |
| `--search` | search mode | .NET only. Skips inserts and requires an explicit `--partition-key-mode`. |
| `--warmup` | `SEARCH_WARMUP_ENABLED` | .NET search warmup toggle. Accepts optional `true` or `false`; defaults to `true` and runs 1,000 untimed queries. |
| `--queries-per-second` | `SEARCH_QUERIES_PER_SECOND` | .NET search query starts per second per logical client. Range: 1-100; default: 1. |
| `--total-queries` | `SEARCH_TOTAL_QUERIES` | .NET search queries shared globally across clients. Default: 1000. |

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\main.py --num-clients 40 --bulk-size 30 --total-docs 100000 --data-path .\data\data-file.json --container-name benchmark-100k --partition-key-mode docid
```

macOS/Linux:

```bash
./.venv/bin/python ./main.py --num-clients 40 --bulk-size 30 --total-docs 100000 --data-path ./data/data-file.json --container-name benchmark-100k --partition-key-mode docid
```

## Configuration

The benchmark loads `.env` and `main.py` can override common values from CLI arguments. The `.env.template` groups settings into Cosmos DB config, data loading, scenario/performance, metrics/diagnostics, and results. The table below lists the current knobs.

| Parameter | Data type | Example | Description |
|---|---|---:|---|
| `COSMOS_ENDPOINT` | string | `https://...documents.azure.com:443/` | Cosmos DB account endpoint. |
| `COSMOS_KEY` | string | blank or account key | Optional Cosmos DB account key. When blank, authentication uses `DefaultAzureCredential` / Entra ID. |
| `COSMOS_DATABASE_NAME` | string | `testdb` | Target database name. Must already exist. |
| `COSMOS_CONTAINER_NAME` | string | `benchmark-100k` | Target container name. Must already exist and have the desired partition key/vector policy. |
| `COSMOS_VECTOR_PATH` | JSON path | `/emb` | Vector property used by .NET search queries. Must match the container vector policy. |
| `COSMOS_VECTOR_DIMENSIONS` | int | `1536` | Number of random vector values generated for each .NET search query. Must match the vector policy. |
| `DATA_URL` | URL string | `https://source-url-here.com/example.json.bz2` | Source URL used by `src/download_data.py`. The file is downloaded into `DATA_DIR`. |
| `DATA_DIR` | path string | `./data` | Directory where `src/download_data.py` stores the downloaded file and optional decompressed JSON output. |
| `DATA_TYPE` | enum string | `fake` or `file` | Selects synthetic document generation or streaming JSON/JSONL input. Paths ending in `.bz2` are decompressed while reading. |
| `DOC_JSON_PATH` | path string | `./data/example.json` | Path to the JSON/JSONL file used by `src/benchmark.py`. May point to a plain file or a `.bz2` compressed file. Required when `DATA_TYPE=file`. |
| `DOC_JSON_FORMAT` | enum string | `jsonl` | JSON shape. Supported: `jsonl`, `array`, `multiple_values`. |
| `DOC_QUEUE_MULTIPLIER` | int | `30` | File-input queue capacity multiplier. Queue document capacity is approximately `NUM_CLIENTS * BULK_SIZE * DOC_QUEUE_MULTIPLIER`. Larger values buffer more documents from disk so inserts are less likely to wait on file loading, but consume more RAM. |
| `NUM_CLIENTS` | int | `1` | Number of worker client processes used to upload documents. Can be overridden with `--num-clients`. |
| `BULK_SIZE` | int | `30` | Number of documents each worker pulls into a local bulk before scheduling uploads. |
| `MAX_TOTAL_DOCS` | optional int | `100000` or blank | Optional cap on how many documents to upload. Blank means no cap for JSON mode. |
| `PARTITION_KEY_FIELDS` | comma-separated strings | `docid` | Ordered target-container partition key fields without leading slashes. Use `sessionid,docid` for a hierarchical key. Falls back to legacy `PARTITION_KEY_FIELD` when unset. |
| `PARTITION_KEY_FIELD` | string | `docid` | Legacy single partition-key setting retained for compatibility. |
| `DOCUMENT_ID_FALLBACK_FIELD` | string | `docid` | Field copied to Cosmos `id` when a file-input document has no `id`. |
| `COSMOS_ERROR_SAMPLE_LIMIT` | int | `3` | Number of detailed Cosmos write failures to print per worker. |
| `MAX_CONCURRENCY` / `MAX_IN_FLIGHT` | int | `30` | Max concurrent `create_item` calls per worker process. Values below `1` are treated as auto and resolve to `ceil(1.5 * BULK_SIZE)`. Total possible in-flight writes are roughly `NUM_CLIENTS * MAX_CONCURRENCY`. |
| `MAX_INSERT_RETRIES` | int | `3` | Number of quick retries for throttled or transient Cosmos write failures. Non-transient failures such as duplicate item conflicts fail fast. |
| `INSERT_RETRY_DELAY_MS` | int | `50` | Base retry delay in milliseconds when Cosmos does not return retry-after guidance. Retry-after headers are honored when present. |
| `CAPTURE_RU_CHARGES` | bool | `true` | Captures `x-ms-request-charge` through a per-request response hook. Set to `false` to reduce hot-path overhead; RU metrics will report zero. |
| `PARTITION_KEY_RANGE_RPS_ENABLED` | bool | `false` | Prints live `create_item` requests/sec by `x-ms-partition-key-range-id` when Cosmos returns that response header. Enables a response hook even when `CAPTURE_RU_CHARGES=false`. |
| `TOTAL_DOCS` | int | `1000000` | Number of fake docs generated when `DATA_TYPE=fake`. Also bounded by `MAX_TOTAL_DOCS` if set. |
| `PAYLOAD_BYTES` | int | `5000` | Synthetic payload size for fake docs only. |
| `FAKE_DATA_VECTOR_DIM` | int | `1536` | Number of random vector values generated for each fake document. |
| `SESSION_ID_ENABLED` | bool | `false` | Adds or replaces `sessionid` on fake and file-input documents. |
| `SESSION_ID_MIN_DOCS` | int | `10` | Inclusive minimum target number of consecutive documents per generated session. |
| `SESSION_ID_MAX_DOCS` | int | `1000` | Inclusive maximum target number of consecutive documents per generated session. |
| `SEARCH_WARMUP_ENABLED` | bool | `true` | Runs 1,000 untimed .NET vector search queries before measured search statistics begin. Disable with `false` or `--warmup false`. |
| `SEARCH_QUERIES_PER_SECOND` | int | `1` | .NET search query-start rate per logical client. CLI range is 1-100. |
| `SEARCH_TOTAL_QUERIES` | int | `1000` | Total .NET search queries divided across all logical clients. |
| `MAX_PENDING_BULKS` | int | auto | Maximum pending batch tasks per worker. Defaults from concurrency and batch size. |
| `LIVE_INTERVAL_SEC` | float | `1.0` | Backward-compatible default for `METRICS_SAMPLE_INTERVAL_SEC` when the newer setting is not present. |
| `METRICS_SAMPLE_INTERVAL_SEC` | float | `1.0` | Seconds between live metric refreshes and periodic throughput samples. |
| `METRICS_TIMING_SAMPLE_INTERVAL` | int | `1` | Records one service/latency/processing timing sample every N completed local bulks. Higher values reduce metrics overhead. |
| `METRICS_WARMUP_SEC` | float | `0.0` | Warmup duration after the first write request starts. Throughput and timing samples before this cutoff are excluded from final  summaries. |
| `CSV_OUTPUT_ENABLED` | bool | `true` | Writes final metrics to a CSV file when enabled. Set to `false` to disable CSV output. |
| `TEST_RESULTS_ROOT` | path string | `results` | Optional root folder for metrics CSV output. Defaults to `results`. |



During runs, watch these final CSV fields. Terminal live output uses the same concepts but renders `_per_` as `/` for readability, such as `current_docs/sec` and `avg_ru/operation`.

- `avg_ru_per_operation`: actual average RU charged per write.
- `throttles_w_retry_total`: if this rises, the workload is exceeding available RU or hitting partition limits. This counts 429 retry attempts, including writes that later succeed.
- `current_docs_per_sec` / `current_docs_per_sec_per_client`: successful insert throughput from the latest sample window, total and divided by configured client count.
- `mean_docs_per_sec` / `mean_docs_per_sec_per_client` / `max_docs_per_sec`: mean and peak successful insert throughput from sampled windows after warmup.
- `Partition key range stats`: live terminal-only diagnostics enabled by `PARTITION_KEY_RANGE_RPS_ENABLED=true`. Observed ranges are printed on one line, such as `pkrange_0=ops/sec=500.00 , pkrange_1=ops/sec=450.00`.
- `service_time_ms_mean` / `service_time_ms_p50` / `service_time_ms_p90` / `service_time_ms_p99`: time from each individual `create_item` request send until that request receives a response or error.
- `capture_ru_charges`: whether RU capture was enabled for the run. When `false`, RU metrics are intentionally zero.
- `metrics_timing_sample_interval`: how often bulk timing samples were retained for percentile metrics.


## Tuning Notes

- Increase `NUM_CLIENTS` to add more worker client processes.
- Increase `MAX_CONCURRENCY` to allow more simultaneous writes per process.
- Keep `BULK_SIZE` large enough that workers do not schedule tiny waves of work.
- Keep `DOC_QUEUE_MULTIPLIER` high enough that workers do not starve while the producer reads the JSON/JSONL file from disk. Increase it to reduce disk-loading bottlenecks, but remember that larger queues consume more RAM.
- If `throttles_w_retry_total` rises, reduce client pressure or increase autoscale max RU/s.