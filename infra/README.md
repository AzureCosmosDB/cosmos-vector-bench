# Cosmos DB Benchmark Infrastructure

This folder contains a Bicep template for creating a Cosmos DB for NoSQL database and vector container in an existing Cosmos DB account.

The template creates or updates:

- A SQL database under an existing account.
- A SQL container with autoscale throughput.
- A vector embedding policy.
- A vector index in the indexing policy.
- An excluded regular indexing path for the vector field to reduce write RU cost.

## Files

| File | Purpose |
|---|---|
| `main.bicep` | Bicep template for the database and container. |
| `main.bicepparam` | Deployment config file. Edit this file directly, or leave its environment-variable expressions in place and set values from your shell or `azd env`. |
| `azure.yaml` | Minimal Azure Developer CLI project file for running `azd provision` from this folder. |

## Prerequisites

Install and sign in with Azure CLI and Azure Developer CLI:

```powershell
az login
azd auth login
```

The Cosmos DB account must already exist. This template intentionally does not create the account. Deploy to the same resource group as the existing account.

## Configuration

Configure deployments by editing `main.bicepparam` directly. That is the simplest path for repeatable benchmark scenarios because the chosen database, container, RU/s, vector path, index type, and dimensions stay in one file.

The checked-in `main.bicepparam` also supports environment variables through `readEnvironmentVariable(...)`. You can keep those expressions and set values with `azd env set`, PowerShell environment variables, or shell exports instead of hard-coding values in the file.

The requested scenario knobs are `COSMOS_DATABASE_NAME`, `COSMOS_CONTAINER_NAME`, `COSMOS_AUTOSCALE_MAX_RU`, `COSMOS_VECTOR_PATH`, `COSMOS_VECTOR_INDEX_TYPE`, and `COSMOS_VECTOR_DIMENSIONS`.

| Environment variable | Default | Description |
|---|---|---|
| `AZURE_RESOURCE_GROUP` | none | Resource group that contains the existing Cosmos DB account. |
| `AZURE_LOCATION` | none | Azure region used by `azd` for deployment metadata. Use the account region. |
| `COSMOS_ACCOUNT_NAME` | none | Existing Cosmos DB account name. |
| `COSMOS_DATABASE_NAME` | `testdb` | Database name to create or update. |
| `COSMOS_CONTAINER_NAME` | `benchmark-openai-vector` | Container name to create or update. |
| `COSMOS_AUTOSCALE_MAX_RU` | `100000` | Autoscale max RU/s for the container. |
| `COSMOS_VECTOR_PATH` | `/emb` | JSON path for the vector field. |
| `COSMOS_VECTOR_INDEX_TYPE` | `diskANN` | Vector index type: `quantizedFlat` or `diskANN`. |
| `COSMOS_VECTOR_DIMENSIONS` | `1536` | Vector dimension count. |
| `COSMOS_PARTITION_KEY_PATHS` | `/docid` | Comma-separated ordered container partition key paths. Use `/sessionid,/docid` for a hierarchical key. |
| `COSMOS_VECTOR_DATA_TYPE` | `float32` | Vector data type: `float32`, `float16`, `int8`, or `uint8`. |
| `COSMOS_VECTOR_DISTANCE_FUNCTION` | `cosine` | Distance function: `cosine`, `dotproduct`, or `euclidean`. |
## Example: DiskANN Container

Edit `main.bicepparam` for the DiskANN scenario:

```bicep
param accountName = '<existing-account-name>'
param databaseName = 'testdb'
param containerName = 'benchmark-openai-diskann'
param autoscaleMaxThroughput = 117000
param vectorPath = '/embedding'
param vectorIndexType = 'diskANN'
param vectorDimensions = 1536
param partitionKeyPaths = ['/docid']
```

Then provision with `azd`:

```powershell
cd infra
azd env new openai-diskann
azd env set AZURE_RESOURCE_GROUP <account-resource-group-name>
azd env set AZURE_LOCATION eastus
azd provision
```

## Example: Quantized Flat Container

Edit `main.bicepparam` for the quantized flat scenario:

```bicep
param accountName = '<existing-account-name>'
param databaseName = 'testdb'
param containerName = 'benchmark-openai-quantizedflat'
param autoscaleMaxThroughput = 60000
param vectorPath = '/embedding'
param vectorIndexType = 'quantizedFlat'
param vectorDimensions = 1536
param partitionKeyPaths = [
  '/sessionid'
  '/docid'
]
```

Then provision with `azd`:

```powershell
cd infra
azd env new openai-quantizedflat
azd env set AZURE_RESOURCE_GROUP <account-resource-group-name>
azd env set AZURE_LOCATION eastus
azd provision
```

## Direct Bicep Deployment

You can also deploy without `azd` by passing the `.bicepparam` file to Azure CLI. Edit `main.bicepparam` first, then deploy it:

```powershell
cd infra
az deployment group create `
  --resource-group <resource-group-name> `
  --parameters main.bicepparam
```

If you prefer not to edit `main.bicepparam`, leave the `readEnvironmentVariable(...)` expressions in place and set environment variables directly before deployment:

```powershell
cd infra
$env:COSMOS_ACCOUNT_NAME = '<existing-account-name>'
$env:COSMOS_DATABASE_NAME = 'testdb'
$env:COSMOS_CONTAINER_NAME = 'benchmark-openai-vector'
$env:COSMOS_AUTOSCALE_MAX_RU = '60000'
$env:COSMOS_VECTOR_PATH = '/embedding'
$env:COSMOS_VECTOR_INDEX_TYPE = 'quantizedFlat'
$env:COSMOS_VECTOR_DIMENSIONS = '1536'

az deployment group create `
  --resource-group <resource-group-name> `
  --parameters main.bicepparam
```

## Delete Benchmark Resources

This template targets an existing Cosmos DB account, so cleanup usually means deleting the benchmark container or database, not the account.

Delete only the benchmark container:

```powershell
az cosmosdb sql container delete `
  --resource-group <resource-group-name> `
  --account-name <existing-account-name> `
  --database-name <database-name> `
  --name <container-name> `
  --yes
```

Delete the benchmark database and all containers inside it:

```powershell
az cosmosdb sql database delete `
  --resource-group <resource-group-name> `
  --account-name <existing-account-name> `
  --name <database-name> `
  --yes
```

If you created a dedicated resource group only for this benchmark and it does not contain shared resources, you can delete the whole resource group:

```powershell
az group delete `
  --name <resource-group-name> `
  --yes
```

## Notes

Use a new or empty container for benchmark runs. The .NET writer uses create operations, so rerunning the same input against a populated container creates duplicate-item errors instead of overwriting existing documents.