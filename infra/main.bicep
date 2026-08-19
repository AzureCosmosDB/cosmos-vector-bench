targetScope = 'resourceGroup'

@description('Name of the existing Azure Cosmos DB for NoSQL account.')
param accountName string

@description('Name of the database to create or update.')
param databaseName string

@description('Name of the container to create or update.')
param containerName string

@description('Ordered container partition key paths. Multiple paths create a hierarchical partition key.')
@minLength(1)
@maxLength(3)
param partitionKeyPaths array = [
  '/docid'
]

@description('Autoscale maximum RU/s for the database. Azure Cosmos DB autoscale provisions between about 10% of this value and this value, subject to service minimums.')
@minValue(1000)
param autoscaleMaxThroughput int

@description('Path to the vector property in each document.')
param vectorPath string

@description('Vector index type for the container.')
@allowed([
  'quantizedFlat'
  'diskANN'
])
param vectorIndexType string

@description('Number of dimensions in the vector property.')
@minValue(1)
param vectorDimensions int

@description('Vector element data type.')
@allowed([
  'float32'
  'int8'
  'uint8'
])
param vectorDataType string = 'float32'

@description('Vector distance function.')
@allowed([
  'cosine'
  'dotproduct'
  'euclidean'
])
param vectorDistanceFunction string = 'cosine'

@description('Container default TTL in seconds. Use -1 to disable automatic expiration.')
param defaultTtlSeconds int = -1

var vectorExcludedPath = '${vectorPath}/*'

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: accountName
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  name: databaseName
  parent: account
  properties: {
    resource: {
      id: databaseName
    }
    options: {}
  }
}

resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  name: containerName
  parent: database
  properties: {
    resource: {
      id: containerName
      partitionKey: {
        paths: partitionKeyPaths
        kind: length(partitionKeyPaths) > 1 ? 'MultiHash' : 'Hash'
        version: 2
      }
      defaultTtl: defaultTtlSeconds
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
        excludedPaths: [
          {
            path: '/"_etag"/?'
          }
          {
            path: vectorExcludedPath
          }
        ]
        vectorIndexes: [
          {
            path: vectorPath
            type: vectorIndexType
          }
        ]
      }
      vectorEmbeddingPolicy: {
        vectorEmbeddings: [
          {
            path: vectorPath
            dataType: vectorDataType
            distanceFunction: vectorDistanceFunction
            dimensions: vectorDimensions
          }
        ]
      }
    }
    options: {
      autoscaleSettings: {
        maxThroughput: autoscaleMaxThroughput
      }
    }
  }
}

output databaseName string = database.name
output containerName string = container.name
output autoscaleMaxThroughput int = autoscaleMaxThroughput
output vectorPath string = vectorPath
output vectorIndexType string = vectorIndexType
output vectorDimensions int = vectorDimensions