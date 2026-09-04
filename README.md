# Daily News

Daily News is a local, auditable crawler and review queue for two initial
topics: AI services for software engineering and state-of-the-art AI papers.
It prefers feeds and official APIs, stores every collection and feedback event
in SQLite, and publishes only explicitly approved articles in this first
version.

The AI engineering topic includes Hacker News through its official Firebase
API and linked-article discovery through Bluesky's public search API. Both are
bounded broad sources whose results must also match the topic's configured
terms before entering review. Bluesky is queried only when `daily-news collect`
runs; there is no background poller or stream. Scheduled HTML source crawling
is not implemented, so configured HTML source placeholders remain disabled.
During review, an explicit summary action can fetch one public article page on
demand.

Aggregator links discovered through Bluesky are resolved before preparation.
For example, a Bluesky post linking to a Hacker News discussion produces the
linked article as `LINK` and retains both the Bluesky post and Hacker News page
as `DISCUSS` links. Summary and article-text actions therefore fetch the linked
article rather than the discussion page.

## Setup

Create the Python 3.13 environment and install the application:

```shell
conda env create --file environment.yml
conda activate daily-news
python -m pip install --editable '.[dev]'
```

List the configured topics and coverage lanes:

```shell
daily-news topics
```

The default configuration is under `config/` and the default database is
`daily-news.sqlite3`. Override them with `--config` and `--database`, or the
`DAILY_NEWS_CONFIG` and `DAILY_NEWS_DATABASE` environment variables.

Set readable languages and the local inference model in `config/settings.yaml`:

```yaml
preferred_languages: [en, ja]

llm:
  provider: llama_cpp
  model:
    repository: bartowski/Qwen_Qwen3.5-27B-GGUF
    filename: Qwen3.5-27B-Q4_K_M.gguf
    revision: dfc4776eacea43ff9f528d75eca3e5f490ed9399
```

Articles in English or Japanese are then shown without translation. Articles
in any other language are translated into the first entry (`en` here).

## Collect and review

Collect every topic or just one:

```shell
daily-news collect
daily-news collect --topic ai-engineering
```

Review all eligible coverage or one lane:

```shell
daily-news review ai-engineering
daily-news review ai-papers --lane global-english
```

The review prompt accepts `yes`, `no`, `maybe`, `bookmark`, `open`, `summary`,
`text`, `undo`, and `quit` (or their first letters). Press `b` to toggle a
durable bookmark without recording feedback or advancing the queue. Press `s`
to fetch the current HTML article, extract its main text, and generate a short
local-model summary. The action is cached and does not record feedback or
advance the session. Review excerpts are capped at 500 characters and show an
`[s]ummary for more` hint when truncated. Press `yb` to bookmark and record
`yes` in one action. Sessions resume at their stored position. List or remove
saved items with:

```shell
daily-news bookmarks
daily-news bookmarks ai-engineering
daily-news unbookmark ai-engineering 42
```
Use the article ID shown by the database/API boundary to inspect its immutable
feedback history:

```shell
daily-news feedback ai-engineering 42
```

Non-preferred-language title and excerpt review, and the summary shortcut,
require the local model selected in `settings.yaml`. The configuration above
downloads a pinned Qwen 3.5 27B GGUF into the Daily News model cache and runs it
directly with llama.cpp. The initial download is about 16.5 GB; subsequent runs
reuse the cached artifact and can work offline.

```shell
daily-news review ai-engineering --lane japan
```

The configured Qwen model prioritizes summary quality and requires substantial
unified memory. Set `llm.model: null` to disable local inference. The summarizer
rejects reasoning traces because this task needs a short direct answer. If
translation is unavailable or fails, the item is deferred and no negative
feedback is recorded. Model download, licensing, memory use, and suitability
remain the operator's responsibility.

For a GGUF already downloaded on the host, use its path instead of the structured
Hugging Face reference:

```yaml
llm:
  provider: llama_cpp
  model: /absolute/path/to/model.gguf
```

The Conda environment installs `llama-cpp-python` on Apple Silicon alongside
MLX-LM. If the application was installed without that environment, add the
optional dependency with a Metal-enabled build:

```shell
CMAKE_ARGS="-DGGML_METAL=on" python -m pip install --editable '.[llama]'
```

The llama.cpp backend reads the chat template embedded in the GGUF, uses a
16,384-token context, and offloads every supported layer to Metal. Remote models
must pin a 40-character commit revision. Changing the provider, artifact, or
revision creates separate translation and summary cache entries. No inference
server is started or contacted.

MLX-LM remains available by selecting `provider: mlx` and setting `model` to an
MLX Hugging Face model identifier such as
`mlx-community/Qwen3.6-35B-A3B-4bit`.

Create the conservative approved-only edition, or hide an entry and allow the
next eligible approved item to replace it:

```shell
daily-news feed ai-engineering
daily-news hide ai-engineering 42 --reason weak
```

## Verification

```shell
ruff format --check app tests
ruff check app tests
mypy app
pytest -q
```

## Container

The container includes portable collection, persistence, English review, and
feed behavior. The accelerated MLX and llama.cpp runtimes are host concerns and
are not installed in the Linux container.

```shell
docker build -t daily-news .
docker run --rm -v daily-news-data:/data daily-news topics
docker run --rm -it -v daily-news-data:/data daily-news collect
docker run --rm -it -v daily-news-data:/data daily-news review ai-engineering
```
