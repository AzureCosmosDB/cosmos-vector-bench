using '../../infra/main.bicep'

param accountName = '<existing-account-name>'
param databaseName = 'testdb'
param containerName = 's4-diskANN'
param partitionKeyPaths = ['/docid']
param autoscaleMaxThroughput = 450000
param vectorPath = '/emb'
param vectorIndexType = 'diskANN'
param vectorDimensions = 1536
param vectorDataType = 'float32'
param vectorDistanceFunction = 'cosine'
param defaultTtlSeconds = -1