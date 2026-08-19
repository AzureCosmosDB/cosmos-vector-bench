using '../../infra/main.bicep'

param accountName = 'cosmos-tests'
param databaseName = 'testdb'
param containerName = 's6-quantizedFlat-sessionid'
param partitionKeyPaths = ['/sessionid']
param autoscaleMaxThroughput = 1000000
param vectorPath = '/emb'
param vectorIndexType = 'quantizedFlat'
param vectorDimensions = 1536
param vectorDataType = 'float32'
param vectorDistanceFunction = 'cosine'
param defaultTtlSeconds = -1
