using '../../infra/main.bicep'

param accountName = '<existing-account-name>'
param databaseName = 'testdb'
param containerName = 's4-quantizedFlat-sessionid'
param partitionKeyPaths = ['/sessionid']
param autoscaleMaxThroughput = 150000
param vectorPath = '/emb'
param vectorIndexType = 'quantizedFlat'
param vectorDimensions = 1536
param vectorDataType = 'float32'
param vectorDistanceFunction = 'cosine'
param defaultTtlSeconds = -1
