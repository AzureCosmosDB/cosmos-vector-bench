using '../../infra/main.bicep'

param accountName = '<cosmos-db-account-name>'
param databaseName = 'testdb'
param containerName = 's1-quantizedFlat'
param partitionKeyPaths = ['/docid']
param autoscaleMaxThroughput = 100000
param vectorPath = '/emb'
param vectorIndexType = 'quantizedFlat'
param vectorDimensions = 1536
param vectorDataType = 'float32'
param vectorDistanceFunction = 'cosine'
param defaultTtlSeconds = -1