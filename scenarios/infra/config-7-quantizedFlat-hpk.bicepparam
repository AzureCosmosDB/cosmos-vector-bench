using '../../infra/main.bicep'

param accountName = 'cosmos-tests'
param databaseName = 'testdb'
param containerName = 's7-quantizedFlat-hpk'
param partitionKeyPaths = [
  '/sessionid'
  '/docid'
]
param autoscaleMaxThroughput = 2000000
param vectorPath = '/emb'
param vectorIndexType = 'quantizedFlat'
param vectorDimensions = 1536
param vectorDataType = 'float32'
param vectorDistanceFunction = 'cosine'
param defaultTtlSeconds = -1
