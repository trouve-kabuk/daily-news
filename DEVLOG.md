# Development Log

## 2026-09-03

- 2026-09-03 11:50 Defined the initial Conda environment as `daily-news` on
  Python 3.13. MLX-LM is installed conditionally on Apple Silicon, behind the
  provider-neutral LLM boundary.
- 2026-09-03 11:51 Chose Polars as the preferred dataframe library. A dataframe
  dependency will be added only when an implemented use case requires it.
- 2026-09-03 11:56 Added language and market coverage lanes for global English,
  Japan, and China. Non-English review requires cached English translation from
  the local LLM; translation failure defers feedback rather than implying
  rejection.
- 2026-09-03 11:57 Defined configurable edition quotas: a topic-wide story cap
  plus per-coverage-lane soft targets and hard maxima. Quotas count deduplicated
  stories and never force publication below the quality threshold.
- 2026-09-03 12:01 Simplified the MVP identity model so each canonical article
  is an independent feed item and quotas count articles. Semantic cross-article
  topic or story analysis remains explicit future work without a TODO item.
- 2026-09-03 13:10 Implemented the first runnable version: strict configuration,
  RSS/Atom and arXiv collection, conditional requests and retry isolation,
  canonical SQLite persistence, coverage-aware resumable queues, MLX-backed
  translation caching, append-only feedback and undo, approved-only editions,
  hide-and-replace behavior, CLI commands, and portable packaging.
- 2026-09-03 13:10 Added local feed fixtures and unit/integration coverage for
  version validation, lookback, normalization, duplicate prevention, partial
  source failure, translation deferral, review resume and undo, feedback
  durability, both initial topics, and hard quota enforcement. Automated late
  ranking and broader crawling remain later measured phases from `SPECS.md`.
- 2026-09-03 13:10 Verified that the four configured live endpoints return
  parseable RSS, Atom, or arXiv API documents. The Dockerfile was inspected but
  could not be built in this workspace because the Docker CLI is unavailable.
- 2026-09-03 17:13 Added the official Hacker News Firebase API as a broad source
  for the AI engineering topic and advanced that topic definition to version 2.
  The adapter bounds top-story item fan-out, reuses its HTTP client, preserves
  HN IDs and discussion URLs, filters dead or non-story records, and degrades to
  a partial source result when individual item requests fail.
- 2026-09-03 17:13 Kept the concurrently added Anthropic, Google, and Microsoft
  HTML source placeholders but marked them disabled because HTML scraping is
  not implemented. Confirmed the live Hacker News API returned 500 integer top
  story IDs and successfully parsed the first referenced item.
- 2026-09-03 17:36 Improved interactive review readability with a responsive
  Rich terminal panel: prominent title, human-readable source name, emphasized
  language and market, aligned publication metadata, separated excerpt and
  translation sections, and an isolated clickable link. Added a regression test
  for the framed metadata layout.
- 2026-09-03 17:48 Added the `[s]ummary` review action. It checks public-address
  and robots-policy constraints, follows only validated redirects, accepts
  bounded HTML/XHTML, extracts main text with Trafilatura, generates at most
  three English sentences through the provider-neutral local inference
  contract, and caches content and summary lineage. Summary display does not
  advance review or create feedback.
- 2026-09-03 17:55 Added visible summary-stage progress for cache lookup, HTML
  fetch, extraction, model loading/generation, and persistence. Fixed an escaped
  Hugging Face 404 by wrapping MLX model-load failures as recoverable inference
  errors, corrected the documented Qwen repository, and aliased the obsolete
  identifier so existing environment configuration keeps working.
- 2026-09-03 18:02 Increased the short-summary generation budget, constrained
  the requested output length, and added a completeness guard that removes a
  trailing sentence fragment or rejects an entirely incomplete response. Bumped
  prompt lineage so previously cached truncated summaries are regenerated.
- 2026-09-03 18:10 Suppressed Hugging Face Hub download progress bars during
  lazy MLX model loading and split the review status into explicit `Loading AI
  model` and `Generating summary` stages.
- 2026-09-03 18:16 Added visible single-key shortcuts to the optional feedback
  reason prompt, including `[w]eak`, while retaining full reason names and
  free-form input.
- 2026-09-03 18:17 Removed generated word-count annotations from summaries and
  removed the numeric word-limit trigger from the prompt. Applied Qwen's
  tokenizer chat template for better instruction-following, bumped summary
  lineage to regenerate affected cache entries, and documented a larger
  30B-A3B MLX model as an opt-in quality tier.
- 2026-09-03 18:20 Updated the opt-in high-quality model from Qwen 3 30B-A3B to
  Qwen 3.6 35B-A3B. Retained Qwen 3 4B only as the low-memory tier and disabled
  thinking through the chat template for concise summary generation.
- 2026-09-03 18:25 Fixed Qwen reasoning leakage by passing `enable_thinking`
  directly to the in-process tokenizer rather than using the nested server API
  shape. Added a fail-closed output boundary that removes properly delimited
  thinking or rejects an undelimited trace, and bumped summary cache lineage.
- 2026-09-03 18:35 Added durable topic-scoped bookmarks. The review CLI now
  toggles the current article with `[b]ookmark` without feedback or queue
  movement, while new `bookmarks` and `unbookmark` commands support retrieval
  and cleanup outside the review session.
- 2026-09-03 18:40 Added `[yb]` as an atomic positive review shortcut. It
  idempotently saves the article bookmark, records `yes`, and advances the queue.
- 2026-09-03 18:48 Made automatic translation progress visible with the source
  language, field, and configured model. Applied the non-thinking chat template
  and reasoning filter to translation, corrected cache reads to include prompt
  lineage, and bumped that lineage so earlier contaminated translations are
  regenerated.
- 2026-09-03 19:00 Added ordered `preferred_languages` application settings.
  Review now leaves any configured preferred language untouched and translates
  all others into the first entry, carrying that target through model calls,
  cache identity, progress messages, and rendering.
- 2026-09-03 19:10 Added typed article-link persistence for aggregator
  discussions. Hacker News outbound stories now retain their HN discussion URL,
  and review panels, bookmark listings, and feed output expose it alongside the
  canonical article link without duplicating self-post URLs.
- 2026-09-04 10:00 Improved transient throttling recovery after an arXiv HTTP
  429. Retries now honor numeric and HTTP-date `Retry-After` headers with a
  five-minute safety bound, and the arXiv source has a wider four-attempt
  exponential fallback while retaining its mandatory three-second floor. The
  CLI reports retry delay and attempt progress before each wait.

## 2026-09-04

- 2026-09-04 10:45 Made llama.cpp model acquisition serverless and owned by the
  application. Configuration can now name a local file or an immutable Hugging
  Face repository artifact; remote GGUFs are cached for offline reuse and their
  complete source identity participates in inference cache lineage. Switched
  the default to the pinned Qwen 3.5 27B Q4_K_M conversion.
- 2026-09-04 10:30 Added llama.cpp as a second in-process local inference
  backend. Typed application settings select MLX or a local llama.cpp GGUF, and
  llama-cpp-python loads lazily with a bounded context and full Metal offload.
  Both backends now share prompts and fail-closed output cleanup while retaining
  backend-local loading and response validation.
- 2026-09-04 09:27 Added Bluesky linked-article discovery through the public
  AppView search API. The adapter lives in its own module, runs only during an
  explicit collect command, bounds and paginates examined posts, ignores
  linkless posts, preserves full post metadata, and records every Bluesky post
  as a discussion link for the deduplicated outbound article. Added strict
  source configuration, an English AI-engineering search, topic version 3, and
  coverage for duplicate links, pagination, malformed records, and partial
  results. Live verification used `api.bsky.app`; the documented
  `public.api.bsky.app` host returned a CDN-level 403 from the target runtime.
- 2026-09-04 09:27 Added per-source CLI progress before collection begins while
  keeping terminal output outside the offline collection workflow.
- 2026-09-04 09:54 Moved local inference model selection from environment
  variables into the typed `llm` section of `settings.yaml`. The project now
  selects `mlx-community/Qwen3.6-35B-A3B-4bit`, while missing or null model
  configuration leaves inference disabled for backward compatibility.
- 2026-09-04 10:02 Added bounded, registry-based aggregator link resolution for
  social discoveries. Bluesky links to Hacker News now resolve through the
  official Firebase item API so the outbound article becomes canonical for
  summaries, while both the Bluesky post and Hacker News page remain typed
  discussion links. Generalized collected documents to retain multiple
  discussion URLs and kept resolver-specific behavior in its own module for
  future aggregator additions.
- 2026-09-04 10:17 Capped original and translated review excerpts at 500
  displayed characters. Truncated excerpts now end with an explicit
  `[s]ummary for more` hint while stored source text remains intact.
