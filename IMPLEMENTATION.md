# Implementation

## Python environment

- Use Conda with the environment name `daily-news`.
- Create the environment from `environment.yml`.
- Use Python 3.13: it is the newest conservative choice across the expected ML,
  retrieval, crawling, database, and CLI packages.
- Defer Python 3.14 until the complete dependency set is tested against it.
- Install MLX-LM and llama-cpp-python only on Apple Silicon through environment
  markers so the base environment remains portable.
- Keep application dependencies in project packaging once it is introduced;
  `environment.yml` owns the interpreter and platform-specific runtime setup.

MLX-LM and llama.cpp are supported inference backends on Macs. Application
workflows depend on the provider-neutral `llm` contract and do not import either
third-party runtime eagerly. The typed `llm.provider` and `llm.model` settings
select exactly one backend from `settings.yaml`; a null model disables inference.
Model selection does not depend on process environment.

The llama.cpp integration uses `llama-cpp-python` in process rather than a
separately managed HTTP server. It accepts a local GGUF path or resolves an exact
Hugging Face repository, filename, and 40-character commit revision through
`huggingface_hub`. Remote files live under the Daily News cache and are reused
offline. The backend relies on the GGUF's embedded chat template, bounds context
at 16,384 tokens, requests all-layer accelerator offload, and uses deterministic
chat completion. Cache model versions contain the backend and either the
resolved local path or complete immutable remote identity. Shared prompt and
output safety policy lives in `llm/runtime.py`, while artifact resolution,
model loading, and response-shape handling remain backend-local.

The same local inference contract translates articles whose language is absent
from the ordered `preferred_languages` setting into its first entry.
Translations are cached by
source content hash, model version, and prompt version; no external translation
API is required. Translation uses the same non-thinking chat template and
reasoning-trace filter as summarization. Cache reads include prompt lineage so
an obsolete or contaminated translation cannot survive a prompt correction.

Create the environment with:

```shell
conda env create --file environment.yml
conda activate daily-news
```

Do not:

- Develop or install project packages in Conda's base environment.
- Assume MLX is importable merely because the provider-neutral `llm` package
  is importable.
- Run llama.cpp behind a local HTTP service; this application only needs one
  in-process model and benefits from simpler lifecycle and error handling.
- Read another model manager's internal manifest or blob storage. Resolve
  upstream GGUF artifacts directly so ownership and cache lineage stay explicit.
- Infer a backend from a model filename or identifier; the explicit provider
  setting keeps startup behavior observable.
- Duplicate ordinary application dependencies between `environment.yml` and
  future project packaging.

## Dataframes

- Prefer Polars when tabular or dataframe processing provides a clear benefit.
- Do not add a dataframe dependency until a concrete use case requires it.
- Use pandas only when an essential integration requires pandas objects or when
  measured behavior makes it the better fit. Keep any pandas-specific boundary
  local to that integration.

## Edition quotas

- Configure a hard article maximum per topic.
- Configure optional soft article targets and hard maxima per coverage lane.
- Count canonical articles, not fetch versions or translations.
- Never treat a target as a minimum that bypasses the publication threshold.
- Persist the primary quota lane selected for a multi-lane article.

## Article identity

- The MVP treats each canonical article as an independent feed item.
- Deduplicate repeated fetches by canonical URL and versions of a research paper
  by stable identifier when available.
- Do not implement semantic cross-article topic or story grouping until its
  multilingual quality can be evaluated.

## Application and persistence

- Use a single local SQLite database with foreign keys enabled. Canonical
  articles are mutable snapshots, while topic definitions, source documents,
  collection runs, translations, feed entries, and feedback events retain the
  lineage needed for audit and replay.
- Keep schema creation idempotent in the knowledge-store boundary for the first
  version. Adopt numbered migrations when the first deployed schema change is
  required; introducing a migration framework before then adds machinery
  without a migration to express.
- Use Pydantic to reject unsupported YAML fields and invalid identifiers,
  language tags, market tags, freshness values, and quotas at CLI startup.
  Persist a definition hash and reject edits that reuse an existing topic
  version number.
- Use Typer for a compact interactive CLI and HTTPX plus feedparser for RSS,
  Atom, and the arXiv API. Use the same HTTP boundary with explicit JSON parsing
  for Hacker News. Keep the Bluesky adapter in its own cohesive module because
  its AppView response parsing, pagination, embed handling, and AT URI identity
  change independently from feed and Hacker News collection.

## Collection baseline

- Conditional requests use the latest successful ETag and Last-Modified value.
  Retry only transient HTTP statuses and transport/parser failures, with a
  bounded exponential delay that is never shorter than the source rate limit.
  Honor numeric or HTTP-date `Retry-After` response headers up to five minutes;
  arXiv uses four attempts with 5/10/20-second exponential delays when the
  server does not provide one. Print the delay and next attempt before waiting
  so a throttled collection run does not appear hung.
- Fetch at most the configured `max_items` from the official Hacker News
  `topstories` endpoint. Reuse one HTTP client across item requests, preserve
  the Hacker News ID as `hackernews:<id>`, and treat individual item failures as
  a partial source result. The official API currently declares no rate limit;
  the adapter still supports a configurable delay between item requests.
- Treat Hacker News as a broad source. Its successfully prepared articles enter
  a topic queue only when title or text matches that topic's terms or entities;
  topic-specific feeds continue to enter directly.
- Query Bluesky's public `app.bsky.feed.searchPosts` endpoint only as part of an
  explicit `collect` command. Bound examined posts by `max_items`, paginate only
  within that command, and degrade later-page or individual-record failures to
  a partial result. Prefer the external-card URL and title, fall back to a
  rich-text facet link and bounded post-text title, and ignore linkless posts.
  Store the Bluesky post URL as a discussion link and its full response object
  as source metadata; do not assign the post's AT URI as the linked article's
  stable identity because many posts may reference the same article.
- Run social outbound URLs through the resolver registry in
  `offline/link_resolution.py`. A resolver owns recognition and API lookup for
  one aggregator; the orchestration follows at most three hops, rejects loops,
  retains every intermediate discussion URL, and preserves resolution metadata.
  Hacker News resolution parses the item ID, reads the official Firebase item
  endpoint, and replaces the canonical URL and title only when the item is a
  live story with a valid outbound HTTP(S) URL. A failed resolution is reported
  as a partial source result and leaves the last reachable URL usable.
- Persist aggregator discussion pages as typed `article_links` independently of
  the canonical outbound URL. Avoid storing a duplicate link for self-posts,
  where the discussion page is already canonical, and expose retained links in
  review, bookmark listings, and generated feed output.
- Store every valid fetched item as a source-document version, including items
  outside a topic's lookback. Only fresh items enter the topic candidate pool.
- Infer market relevance conservatively from source markets and a versioned
  lexical China/Japan signal. This establishes provenance without claiming
  that source geography and article relevance are identical.
- Reject scheduled HTML-source crawling, browser automation, and semantic story
  clustering for this version. HTML source placeholders are explicitly disabled
  so they remain visible configuration work without breaking collection. The
  review flow may fetch one public HTML article on explicit user request.

## Review and feed baseline

- Render each interactive review candidate as a Rich terminal panel. Give the
  original title visual priority, align source/language/market/date metadata,
  separate excerpts and translations with rules, and keep the canonical link
  isolated at the bottom. Cap both original and translated excerpt rendering at
  500 characters and direct the reader to `[s]ummary` for more context without
  mutating persisted source text. Rendering changes do not alter review-session
  policy or feedback persistence.
- Store topic-scoped bookmarks independently from append-only feedback so
  saving an article for later never changes review or publication eligibility.
  `[b]ookmark` toggles the current item in place; `bookmarks` lists saved
  articles and `unbookmark` removes one explicitly. `[yb]` idempotently ensures
  the bookmark exists before recording `yes`, so it never removes an existing
  bookmark, and then advances like a normal decision.
- Implement `[s]ummary` as an on-demand review action using a separate bounded
  HTTP client and Trafilatura main-text extraction. Check robots.txt, reject
  credentialed or non-public destinations (including redirects), accept only
  HTML/XHTML, and stop after 3 MB. Cache content-version metadata and summaries
  by content hash, model, target language, and prompt version. The feed remains
  original-source-only and summaries never count as feedback.
- Ask the provider-neutral local inference contract for at most three factual
  English sentences. Apply the tokenizer's instruction/chat template, limit
  model input to 40,000 extracted characters, and cap generation at 512 tokens.
  Strip model-generated word-count metadata and remove an incomplete trailing
  sentence if generation still reaches that bound; fail instead of caching an
  output that contains no complete sentence. Strip a delimited thinking block
  when a final answer follows it, and reject an undelimited reasoning trace so
  internal reasoning is never presented as the summary. Do not add an external
  summarization service.
- Report cache lookup, HTML fetch, extraction, model loading/generation, and
  persistence stages directly in the review CLI because first-time model
  download and inference may take noticeable time. Report model loading and
  summary generation separately, while suppressing Hugging Face's own progress
  bars so third-party rendering does not disrupt the review UI.
- Report each uncached automatic title, excerpt, or content translation with its
  source language and configured model name so the CLI explains the wait and
  makes the active model observable.
- Load `preferred_languages` from optional `settings.yaml`, defaulting to
  `[en]` for backward compatibility. Require at least one unique BCP 47 tag;
  list order is semantic because the first entry is the translation target.
- Load the local inference provider and model from the same typed settings file.
  A missing file or null model keeps inference disabled for backward
  compatibility with custom configurations and non-MLX hosts.
- Wrap both MLX import and model-repository loading failures in the
  provider-neutral inference error. Preserve compatibility for the obsolete
  model identifier previously shown in the README by mapping it to the verified
  `mlx-community/Qwen3-4B-Instruct-2507-4bit` repository.
- Use `mlx-community/Qwen3.6-35B-A3B-4bit` in the project configuration for the
  preferred higher-quality tier, while retaining Qwen 3 4B as a documented
  low-memory alternative.
  Pass `enable_thinking=False` directly to the tokenizer template because short
  summaries need a direct response rather than a reasoning trace. Do not use
  the nested server/API `chat_template_kwargs` shape at this in-process boundary.
- Build deterministic, recency-ordered candidate queues and persist the full
  article-ID/primary-lane ordering in each review session. Explicit feedback
  excludes an article from later review unless an append-only undo compensates
  it.
- Cache faithful translations by article field, input hash, source and target
  languages, model, and prompt version. Review in a non-preferred language is
  deferred until both title and any available excerpt have usable translations.
- Publish only effective `yes` feedback in the first edition policy. This is a
  deliberate conservative quality threshold; automated late ranking remains a
  later delivery phase.
- Allocate qualifying articles toward lane soft targets first, then fill by
  recency while enforcing lane and topic hard maxima. Targets never make an
  unapproved article eligible.
