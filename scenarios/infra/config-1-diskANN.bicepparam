using '../../infra/main.bicep'

param accountName = '<cosmos-db-account_name>'
param databaseName = 'testdb'
param containerName = 's1-diskANN'
param partitionKeyPaths = ['/docid']
param autoscaleMaxThroughput = 100000
param vectorPath = '/emb'
param vectorIndexType = 'diskANN'
param vectorDimensions = 1536
param vectorDataType = 'float32'
param vectorDistanceFunction = 'cosine'
param defaultTtlSeconds = -1