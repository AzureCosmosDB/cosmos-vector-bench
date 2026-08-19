using './main.bicep'

param accountName = readEnvironmentVariable('COSMOS_ACCOUNT_NAME')
param databaseName = readEnvironmentVariable('COSMOS_DATABASE_NAME', 'testdb')
param containerName = readEnvironmentVariable('COSMOS_CONTAINER_NAME', 'benchmark-openai-vector')

param partitionKeyPaths = split(readEnvironmentVariable('COSMOS_PARTITION_KEY_PATHS', '/docid'), ',')
param autoscaleMaxThroughput = int(readEnvironmentVariable('COSMOS_AUTOSCALE_MAX_RU', '100000'))

param vectorPath = readEnvironmentVariable('COSMOS_VECTOR_PATH', '/emb')
param vectorIndexType = readEnvironmentVariable('COSMOS_VECTOR_INDEX_TYPE', 'quantizedFlat')
param vectorDimensions = int(readEnvironmentVariable('COSMOS_VECTOR_DIMENSIONS', '1536'))

param vectorDataType = readEnvironmentVariable('COSMOS_VECTOR_DATA_TYPE', 'float32')
param vectorDistanceFunction = readEnvironmentVariable('COSMOS_VECTOR_DISTANCE_FUNCTION', 'cosine')
param defaultTtlSeconds = int(readEnvironmentVariable('COSMOS_CONTAINER_DEFAULT_TTL_SECONDS', '-1'))