# Data Folder

This folder is the local cache for benchmark input data. The downloader writes the configured source file into this folder and can decompress `.bz2` files next to the downloaded archive. For benchmark runs, decompress the `.bz2` file first and point the writer at the plain JSON/JSONL file so the producer does not spend CPU decompressing during uploads. Using compressed input can limit app-side throughput because the loader has to decompress records while the upload workers are trying to stay busy.

Generated and downloaded data files in this folder are ignored by Git. Keep this README tracked, but do not commit the large corpus files.

## Configure `.env`

Set these values before loading the ESRally OpenAI vector corpus:

```dotenv
DATA_URL=https://rally-tracks.elastic.co/openai_vector/open_ai_corpus-initial-indexing.json.bz2
DATA_DIR=./data
DATA_TYPE=file
DOC_JSON_PATH=./data/open_ai_corpus-initial-indexing.json
DOC_JSON_FORMAT=jsonl
PARTITION_KEY_FIELDS=docid
DOCUMENT_ID_FALLBACK_FIELD=docid
```

`DATA_URL` is the remote file to download. `DATA_DIR` is where the downloaded and decompressed files are written. `DOC_JSON_PATH` should point to the plain JSON/JSONL file for repeatable throughput runs. The benchmark can stream a `.bz2` path directly, but that adds decompression work during each run and can limit client-side write throughput. Every loaded document must contain all `PARTITION_KEY_FIELDS` after optional session enrichment. If a source document is missing `id`, the writer copies `DOCUMENT_ID_FALLBACK_FIELD` into `id` before upload.

## Download Data

Run the downloader before running the benchmark. It decompresses `.bz2` files by default, which is the recommended setup for performance testing.

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\src\download_data.py
```

macOS/Linux:

```bash
./.venv/bin/python ./src/download_data.py
```

To download only and skip decompression, use `--no-decompress`. This *can be* useful when disk space matters more than benchmark throughput, but it is **not the recommended path for max-RPS runs** because compressed input can limit app-side throughput. **For optimal performance, decompress data first.**

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe .\src\download_data.py --no-decompress
```

macOS/Linux:

```bash
./.venv/bin/python ./src/download_data.py --no-decompress
```

With the default URL, the loader creates these files:

```text
data/open_ai_corpus-initial-indexing.json.bz2
data/open_ai_corpus-initial-indexing.json
```

Run the benchmark with `DATA_TYPE=file` and `DOC_JSON_PATH` pointing at the decompressed local file:

```dotenv
DOC_JSON_PATH=./data/open_ai_corpus-initial-indexing.json
```

Direct `.bz2` input is still supported when needed:

```dotenv
DOC_JSON_PATH=./data/open_ai_corpus-initial-indexing.json.bz2
```

The benchmark reader infers compression from the `.bz2` file name. Reading the compressed file directly avoids keeping a decompressed copy, but it uses CPU to decompress during each benchmark run and can limit app-side ingestion throughput. Prefer the decompressed `.json` file when comparing ingestion throughput.