# OpenAI Vector Test Scenarios

These scenarios benchmark Cosmos DB vector document ingestion with the [ESRally](https://esrally.readthedocs.io/) OpenAI vector corpus.

The run commands below use the .NET implementation in `src_dotnet/CosmosVectorBench.csproj`.

## Dataset

Use this source file:

```text
https://rally-tracks.elastic.co/openai_vector/open_ai_corpus-initial-indexing.json.bz2
```

Download the `.bz2` file and decompress it once before running scenarios. The benchmark can stream paths ending in `.bz2` directly, but using compressed input can limit app-side throughput because decompression runs in the producer while workers are uploading. Use the decompressed `.json` file for steadier ingestion throughput.

| Scenario | num_clients | bulk_size | Target bulk ops/sec/client | Docs/sec/client | Docs/sec total |
|---:|---:|---:|---:|---:|---:|
| 1 | 10 | 18 | 0.8 | 14.4 | 144 |
| 2 | 20 | 20 | 1.2 | 24 | 480 |
| 3 | 30 | 30 | 1.33 | 40 | 1,200 |
| 4 | 10 | 40 | 2.5 | 100 | 1,000 |
| 5 | 40 | 10 | 0.2 | 2 | 80 |
| 6 | 50 | 240 | max | — | — |
| 7 | 75 | 500 | max | — | — |

Scenario 6 is an unthrottled max-throughput run backed by a 1,000,000 RU/s (autoscale max) container, so it has no per-client rate target. With 50 clients at a 240 `bulk_size`, it keeps at least 12,000 document writes (50 × 240) in flight at once.

Scenario 7 is an unthrottled max-throughput run backed by a 2,000,000 RU/s (autoscale max) container, so it has no per-client rate target. With 75 clients at a 500 `bulk_size`, it keeps at least 37,500 document writes (75 × 500) in flight at once.

## One-Time Setup

Install the [.NET 9 SDK](https://dotnet.microsoft.com/download). The dataset download utility uses Python, so create a Python environment and install its dependencies as well. If `COSMOS_KEY` is blank, sign in for `DefaultAzureCredential` authentication.

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

Build the .NET benchmark project:

```powershell
dotnet build .\src_dotnet\CosmosVectorBench.csproj -c Release
```

### Cosmos DB Permissions

Scenario provisioning and benchmark inserts use separate Cosmos DB permission planes:

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

Create or choose the target Cosmos DB for NoSQL account, database, and container before running the scenarios. The benchmark does not create these resources. For the OpenAI vector corpus, the container should have the vector policy and indexing policy you want to test, and its partition key should align with `docid`.

Use a new container, or make sure the target container is empty before each scenario run. The writer uses create operations, so items that already exist with the same `id` and partition key are not overwritten; they fail as duplicate-item errors.

Set the shared `.env` values in the root folder:

```dotenv
COSMOS_ENDPOINT=https://<account>.documents.azure.com:443/
COSMOS_KEY=
COSMOS_DATABASE_NAME=testdb
COSMOS_CONTAINER_NAME=<container>

DATA_URL=https://rally-tracks.elastic.co/openai_vector/open_ai_corpus-initial-indexing.json.bz2
DATA_DIR=./data
DATA_TYPE=file
DOC_JSON_PATH=./data/open_ai_corpus-initial-indexing.json
DOC_JSON_FORMAT=jsonl

PARTITION_KEY_FIELDS=docid
DOCUMENT_ID_FALLBACK_FIELD=docid
SESSION_ID_ENABLED=false
SESSION_ID_MIN_DOCS=10
SESSION_ID_MAX_DOCS=1000
DOC_QUEUE_MULTIPLIER=30
MAX_CONCURRENCY=30
COSMOS_ERROR_SAMPLE_LIMIT=0

CSV_OUTPUT_ENABLED=true
TEST_RESULTS_ROOT=results
```

Set the existing Cosmos DB account name before provisioning. You can either pass it to the helper scripts, or edit the scenario Bicep parameter files directly. Each file under `scenarios/infra/` starts with this placeholder:

```bicep
param accountName = '<existing-account-name>'
```

Replace it with your account name in the `config-*-quantizedFlat*.bicepparam` or `config-*-diskANN.bicepparam` files you plan to deploy, or set/pass `accountName` when running a helper script.

Download and decompress the source file. This is the recommended scenario setup because compressed input can limit app-side throughput during benchmark runs:

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\src\download_data.py
```

macOS/Linux:

```bash
./.venv/bin/python ./src/download_data.py
```

After download, verify the benchmark input points to the decompressed file:

```dotenv
DOC_JSON_PATH=./data/open_ai_corpus-initial-indexing.json
```


## Run Commands

Each scenario has one `DiskANN` parameter file and three `quantizedFlat` partition-key variants under `scenarios/infra/`:

| Mode | Parameter file suffix | Container suffix | Runtime fields |
|---|---|---|---|
| `docid` | `quantizedFlat-docid.bicepparam` | `quantizedFlat` | `docid` |
| `sessionid` | `quantizedFlat-sessionid.bicepparam` | `quantizedFlat-sessionid` | `sessionid` |
| `hpk` | `quantizedFlat-hpk.bicepparam` | `quantizedFlat-hpk` | `sessionid,docid` |

The client defaults to process or `.env` configuration, which is `docid` in the repository setup. Passing `--partition-key-mode` selects the fields and enables generated `sessionid` values when required. The examples below show the original `docid` quantizedFlat flow. DiskANN continues to use its matching `config-*-diskANN.bicepparam` file and container name.

Before provisioning, edit the selected `.bicepparam` file and set `accountName` to your existing Cosmos DB account name. For manual deployments, use the scenario-specific files in `scenarios/infra/`, not the generic `infra/main.bicepparam`. The commands below create the matching container, point the benchmark process at that container, read the decompressed `.json` file, and write final metrics to `results/` when `CSV_OUTPUT_ENABLED=true`.

To run all five configs, use the helper scripts in this folder. They default to `quantizedFlat`; pass `diskANN` to run the DiskANN parameter files instead. Set `resourceGroup` to the resource group that contains the existing Cosmos DB account before running either script.

Windows PowerShell:

```powershell
# Either form works in PowerShell.
$env:resourceGroup = '<account-resource-group-name>'
$env:accountName = '<cosmos-account-name>'
# $resourceGroup = '<account-resource-group-name>'
# $accountName = '<cosmos-account-name>'

.\scenarios\run-openai.ps1
.\scenarios\run-openai.ps1 -PartitionKeyMode sessionid
.\scenarios\run-openai.ps1 -PartitionKeyMode hpk
.\scenarios\run-openai.ps1 -IndexType diskANN
# .\scenarios\run-openai.ps1 -IndexType diskANN -ResourceGroup '<account-resource-group-name>' -AccountName '<cosmos-account-name>'
```

macOS/Linux:

```bash
# Either form works in Bash.
export resourceGroup='<account-resource-group-name>'
export accountName='<cosmos-account-name>'

bash ./scenarios/run-openai.sh
bash ./scenarios/run-openai.sh --partition-key-mode sessionid
bash ./scenarios/run-openai.sh --partition-key-mode hpk
bash ./scenarios/run-openai.sh --index-type diskANN
# bash ./scenarios/run-openai.sh --index-type diskANN --resource-group '<account-resource-group-name>' --account-name '<cosmos-account-name>'
```

### Config 1

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'

az deployment group create --resource-group $resourceGroup --parameters .\scenarios\infra\config-1-quantizedFlat-docid.bicepparam
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --bulk-size 18 --num-clients 10 --total-docs 180000 --data-path .\data\open_ai_corpus-initial-indexing.json --container-name s1-quantizedFlat
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'

az deployment group create --resource-group "$resourceGroup" --parameters ./scenarios/infra/config-1-quantizedFlat-docid.bicepparam
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --bulk-size 18 --num-clients 10 --total-docs 180000 --data-path ./data/open_ai_corpus-initial-indexing.json --container-name s1-quantizedFlat
```

### Config 2

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'

az deployment group create --resource-group $resourceGroup --parameters .\scenarios\infra\config-2-quantizedFlat-docid.bicepparam
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --bulk-size 20 --num-clients 20 --total-docs 300000 --data-path .\data\open_ai_corpus-initial-indexing.json --container-name s2-quantizedFlat
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'

az deployment group create --resource-group "$resourceGroup" --parameters ./scenarios/infra/config-2-quantizedFlat-docid.bicepparam
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --bulk-size 20 --num-clients 20 --total-docs 300000 --data-path ./data/open_ai_corpus-initial-indexing.json --container-name s2-quantizedFlat
```

### Config 3

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'

az deployment group create --resource-group $resourceGroup --parameters .\scenarios\infra\config-3-quantizedFlat-docid.bicepparam
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --bulk-size 30 --num-clients 30 --total-docs 1200000 --data-path .\data\open_ai_corpus-initial-indexing.json --container-name s3-quantizedFlat
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'

az deployment group create --resource-group "$resourceGroup" --parameters ./scenarios/infra/config-3-quantizedFlat-docid.bicepparam
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --bulk-size 30 --num-clients 30 --total-docs 1200000 --data-path ./data/open_ai_corpus-initial-indexing.json --container-name s3-quantizedFlat
```

### Config 4

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'

az deployment group create --resource-group $resourceGroup --parameters .\scenarios\infra\config-4-quantizedFlat-docid.bicepparam
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --bulk-size 40 --num-clients 10 --total-docs 400000 --data-path .\data\open_ai_corpus-initial-indexing.json --container-name s4-quantizedFlat
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'

az deployment group create --resource-group "$resourceGroup" --parameters ./scenarios/infra/config-4-quantizedFlat-docid.bicepparam
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --bulk-size 40 --num-clients 10 --total-docs 400000 --data-path ./data/open_ai_corpus-initial-indexing.json --container-name s4-quantizedFlat
```

### Config 5

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'

az deployment group create --resource-group $resourceGroup --parameters .\scenarios\infra\config-5-quantizedFlat-docid.bicepparam
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --bulk-size 10 --num-clients 40 --total-docs 90000 --data-path .\data\open_ai_corpus-initial-indexing.json --container-name s5-quantizedFlat
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'

az deployment group create --resource-group "$resourceGroup" --parameters ./scenarios/infra/config-5-quantizedFlat-docid.bicepparam
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --bulk-size 10 --num-clients 40 --total-docs 90000 --data-path ./data/open_ai_corpus-initial-indexing.json --container-name s5-quantizedFlat
```

### Config 6

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'

az deployment group create --resource-group $resourceGroup --parameters .\scenarios\infra\config-6-quantizedFlat-docid.bicepparam
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --bulk-size 240 --num-clients 50 --total-docs 2000000 --data-path .\data\open_ai_corpus-initial-indexing.json --container-name s6-quantizedFlat
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'

az deployment group create --resource-group "$resourceGroup" --parameters ./scenarios/infra/config-6-quantizedFlat-docid.bicepparam
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --bulk-size 240 --num-clients 50 --total-docs 2000000 --data-path ./data/open_ai_corpus-initial-indexing.json --container-name s6-quantizedFlat
```

### Config 7

Config 7 can be run against three quantizedFlat containers to compare partition distribution under the same 75-client, 500-document bulk, 2,000,000-document workload. Provision and test one container at a time unless you intend to keep multiple 2,000,000 RU/s autoscale-max containers. Delete each test container when its run is complete.

#### Config 7 with `/docid`

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'
$accountName = '<cosmos-account-name>'

az deployment group create --resource-group $resourceGroup --parameters .\scenarios\infra\config-7-quantizedFlat-docid.bicepparam
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --bulk-size 500 --num-clients 75 --total-docs 2000000 --data-path .\data\open_ai_corpus-initial-indexing.json --container-name s7-quantizedFlat --partition-key-mode docid
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'
accountName='<cosmos-account-name>'

az deployment group create --resource-group "$resourceGroup" --parameters ./scenarios/infra/config-7-quantizedFlat-docid.bicepparam
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --bulk-size 500 --num-clients 75 --total-docs 2000000 --data-path ./data/open_ai_corpus-initial-indexing.json --container-name s7-quantizedFlat --partition-key-mode docid
```

#### Config 7 with `/sessionid`

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'
$accountName = '<cosmos-account-name>'

az deployment group create --resource-group $resourceGroup --parameters .\scenarios\infra\config-7-quantizedFlat-sessionid.bicepparam
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --bulk-size 500 --num-clients 75 --total-docs 2000000 --data-path .\data\open_ai_corpus-initial-indexing.json --container-name s7-quantizedFlat-sessionid --partition-key-mode sessionid
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'
accountName='<cosmos-account-name>'

az deployment group create --resource-group "$resourceGroup" --parameters ./scenarios/infra/config-7-quantizedFlat-sessionid.bicepparam
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --bulk-size 500 --num-clients 75 --total-docs 2000000 --data-path ./data/open_ai_corpus-initial-indexing.json --container-name s7-quantizedFlat-sessionid --partition-key-mode sessionid
```

#### Config 7 with hierarchical `/sessionid`, `/docid`

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'
$accountName = '<cosmos-account-name>'

az deployment group create --resource-group $resourceGroup --parameters .\scenarios\infra\config-7-quantizedFlat-hpk.bicepparam
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --bulk-size 500 --num-clients 75 --total-docs 2000000 --data-path .\data\open_ai_corpus-initial-indexing.json --container-name s7-quantizedFlat-hpk --partition-key-mode hpk
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'
accountName='<cosmos-account-name>'

az deployment group create --resource-group "$resourceGroup" --parameters ./scenarios/infra/config-7-quantizedFlat-hpk.bicepparam
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --bulk-size 500 --num-clients 75 --total-docs 2000000 --data-path ./data/open_ai_corpus-initial-indexing.json --container-name s7-quantizedFlat-hpk --partition-key-mode hpk
```

#### Config 7 vector search in HPK mode

After populating the Config 7 hierarchical `/sessionid`, `/docid` container, use `--search --partition-key-mode hpk` to run HPK-scoped vector queries without inserting documents. The benchmark samples 100 `sessionid` values and passes each selected value as an HPK prefix through `QueryRequestOptions.PartitionKey`. Warmup is enabled by default to prime a brand-new Cosmos DB container for vector search before measurement; it runs 1,000 untimed queries before collecting search statistics. This priming is not needed for a steady-state production container, so add `--warmup false` to skip it. The measured workload defaults to one query/sec per client and 1,000 total queries unless overridden.

Windows PowerShell:

```powershell
dotnet run --project .\src_dotnet\CosmosVectorBench.csproj -c Release -- --search --partition-key-mode hpk --container-name s7-quantizedFlat-hpk --num-clients 100 --queries-per-second 1 --total-queries 10000
```

macOS/Linux:

```bash
dotnet run --project ./src_dotnet/CosmosVectorBench.csproj -c Release -- --search --partition-key-mode hpk --container-name s7-quantizedFlat-hpk --num-clients 100 --queries-per-second 1 --total-queries 10000
```

To compare scalar partition keys, run the same search against `s7-quantizedFlat` with `--partition-key-mode docid`, or against `s7-quantizedFlat-sessionid` with `--partition-key-mode sessionid`.

The vector query is `TOP 10` ordered by `VectorDistance`, has no `WHERE` clause, and passes the selected value through `QueryRequestOptions.PartitionKey`. For `hpk` mode, Microsoft guidance notes that also placing the prefix in `WHERE` guarantees efficient routing; this benchmark intentionally omits that clause, so use the reported RU and diagnostics to verify routing behavior for the target account.

The .NET implementation uses the native Cosmos SDK `AllowBulkExecution` write path and in-process logical clients. It reads the decompressed `.json` file only (`.bz2` input is not supported).

## Reading Results

Review the generated CSV in `results/` after each run. The most useful fields are:

| Field | Use |
|---|---|
| `current_docs_per_sec`, `current_docs_per_sec_per_client` | Latest successful insert throughput sample, total and divided by configured client count. |
| `mean_docs_per_sec`, `mean_docs_per_sec_per_client`, `max_docs_per_sec` | Mean and peak successful insert throughput from sampled windows after warmup. |
| `avg_ru_per_operation` | Actual RU charged per inserted document. |
| `throttles_w_retry_total` | Cosmos DB 429 retry attempts, including writes that later succeed. Increase RU/s or reduce client pressure if this rises. |
| `service_time_ms_mean`, `service_time_ms_p50`, `service_time_ms_p90`, `service_time_ms_p99` | Time from each individual Cosmos `create_item` request send until that request receives a response or error. |
| `errors_total` | Non-throttle failures that need inspection before trusting a run. |

Keep the same decompressed dataset path for repeatability, and change only one pressure knob at a time when comparing scenarios.


## Recommendations

Use autoscale throughput for these write-heavy test runs. The table below gives initial autoscale max RU/s recommendations for `quantizedFlat` and `DiskANN` vector indexes.

Increase autoscale max RU/s if `throttles_w_retry_total` rises, or reduce `NUM_CLIENTS` or `MAX_CONCURRENCY` if you want to hold RU/s constant.

## Clean Up

The scenario deployments create Cosmos DB containers in an existing database. Delete the scenario containers when you are done with a run.

If you used the helper scripts and want to remove all scenario containers for one index type, set `indexType` to `quantizedFlat` or `diskANN`:

Windows PowerShell:

```powershell
$resourceGroup = '<account-resource-group-name>'
$accountName = '<cosmos-account-name>'
$databaseName = 'testdb'
$indexType = 'diskANN'

foreach ($scenario in 1..5) {
  $containerName = "s$scenario-$indexType"
  az cosmosdb sql container delete `
    --resource-group $resourceGroup `
    --account-name $accountName `
    --database-name $databaseName `
    --name $containerName `
    --yes
}
```

macOS/Linux:

```bash
resourceGroup='<account-resource-group-name>'
accountName='<cosmos-account-name>'
databaseName='testdb'
indexType='diskANN'

for scenario in 1 2 3 4 5; do
  containerName="s${scenario}-${indexType}"
  az cosmosdb sql container delete \
    --resource-group "$resourceGroup" \
    --account-name "$accountName" \
    --database-name "$databaseName" \
    --name "$containerName" \
    --yes
done
```

For a single scenario container created from an individual Bicep parameter file, delete the matching container name from that file. For example, `config-3-quantizedFlat-docid.bicepparam` creates `s3-quantizedFlat`:

```powershell
az cosmosdb sql container delete `
  --resource-group <account-resource-group-name> `
  --account-name <cosmos-account-name> `
  --database-name testdb `
  --name s3-quantizedFlat `
  --yes
```

```bash
az cosmosdb sql container delete \
  --resource-group '<account-resource-group-name>' \
  --account-name '<cosmos-account-name>' \
  --database-name testdb \
  --name s3-quantizedFlat \
  --yes
```