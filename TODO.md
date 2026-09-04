# TODO

## Minimal Version

### Project foundation

- [x] Define the `daily-news` Conda environment with Python 3.13 and
  Apple-Silicon-conditional MLX-LM installation.
- [x] Create the Python application package under app/ with CLI, offline,
  online, knowledge, and provider-neutral LLM boundaries.
- [x] Add project dependency management, strict type checking, linting,
  formatting, and test configuration.
- [x] Add application configuration loading with clear validation errors.

### Topics and sources

- [x] Define the versioned topic configuration schema, including name,
  description, facets, examples, exclusions, sources, coverage lanes, and
  freshness window, plus topic and lane article quotas.
- [x] Add a configuration file containing the “AI services for engineering”
  topic.
- [x] Add a configuration file containing the “state-of-the-art AI papers”
  topic.
- [x] Validate topic IDs, source references, and unsupported configuration at
  CLI startup.
- [x] Add a CLI command that lists configured topics and their source coverage.
- [x] Configure global English and Japan coverage lanes for both initial topics,
  with China supported as an independently selectable lane.

### Collection and preparation

- [x] Define the source interface and normalized source-document result owned by
  the collection step.
- [x] Implement RSS and Atom collection with conditional requests, bounded
  retries, rate limits, and per-source failure reporting.
- [x] Implement one API-backed research-paper source for the AI papers topic.
- [x] Add the official Hacker News API as a bounded, partially fault-tolerant
  source for the AI engineering topic.
- [x] Add bounded, collect-command-only Bluesky search for linked AI engineering
  articles, retaining each post as provenance without operating a stream.
- [x] Resolve aggregator links found through social sources so summaries target
  the outbound article while every social and aggregator discussion is retained.
- [x] Preserve aggregator discussion links separately from canonical outbound
  article URLs and expose them in review, bookmarks, and feeds.
- [x] Support an overlapping configurable lookback window using publication,
  discovery, and fetch times.
- [x] Normalize titles, URLs, dates, authors, excerpts, language, and available
  source text while retaining original values and provenance.
- [x] Persist canonical BCP 47 content languages, source markets, and inferred
  article-market relevance with provenance.
- [x] Canonicalize URLs and prevent repeated fetches from creating duplicate
  canonical articles.
- [x] Persist collection runs, source documents, canonical articles, and
  preparation failures.
- [x] Add a CLI command that collects recent articles for one topic or all
  configured topics.

### Candidate queue

- [x] Associate collected articles with the topics whose configured sources
  produced them.
- [x] Create a high-recall review queue per topic and coverage lane without
  requiring automated MLX ranking.
- [x] Validate topic maximums and per-lane article targets and maximums.
- [x] Count canonical articles when applying quotas, assigning multi-lane
  articles one reproducible primary lane per edition.
- [x] Generate and cache English translations of non-English titles and excerpts
  with the local LLM, source lineage, and model-version metadata.
- [x] Generate and cache an English translation of extracted article text on
  demand using the local LLM.
- [x] Defer non-English articles whose required translation failed without
  recording negative feedback.
- [x] Exclude articles already given effective user_approved, user_borderline,
  user_rejected, or user_hidden feedback for the same topic.
- [x] Retain queue position and session progress so interrupted reviews can
  resume deterministically.

### Feedback CLI

- [x] Add a CLI command that starts or resumes review for a selected topic.
- [x] Allow review to select all coverage or one language or market lane.
- [x] Display one article at a time with its original title, source, publication
  time, language, market, excerpt, URL, and queue progress.
- [x] Cap review excerpts at 500 characters and point truncated items to
  `[s]ummary` for more context.
- [x] Show clearly labeled English translations for non-English articles and
  allow toggling translated extracted text.
- [x] Make readable languages configurable in preference order, translating
  only other languages into the first configured language.
- [x] Add an on-demand `[s]ummary` action that safely fetches public article
  HTML, extracts main text, and caches a short local-LLM summary without
  recording feedback or advancing review.
- [x] Add a `[b]ookmark` review action plus CLI listing and removal without
  recording feedback or advancing review.
- [x] Add `[yb]` to bookmark and record a positive decision in one action.
- [x] Allow the source article to be opened without recording a decision.
- [x] Record yes as user_approved, no as user_rejected, and maybe as
  user_borderline.
- [x] Offer optional feedback reasons for off-topic, weak, duplicate, stale,
  inaccessible, or another user-provided reason, with single-key CLI shortcuts.
- [x] Implement undo as an append-only compensating feedback event.
- [x] Allow a review session to save and quit without losing its position.
- [x] Add a CLI view of effective feedback and its event history for an article.

### Verification and documentation

- [x] Add a llama.cpp GGUF inference backend beside MLX-LM with Metal offload,
  explicit backend selection, and shared output safety behavior.
- [x] Let llama.cpp resolve and cache revision-pinned Hugging Face GGUF models
  without an inference server while retaining direct local-file support.
- [x] Configure the local inference provider and model in `settings.yaml`
  instead of process environment variables.
- [x] Test topic configuration validation and versioning.
- [x] Test RSS or Atom parsing, retries, lookback behavior, normalization, and
  duplicate handling with local fixtures.
- [x] Test persistence and effective-state derivation from append-only feedback
  events.
- [x] Test the review CLI for yes, no, maybe, undo, quit, and resume.
- [x] Add an end-to-end test that collects fixtures for both topics and records
  feedback through the CLI.
- [x] Document setup, configuration, collection, and review commands.
- [x] Record adopted and rejected technical choices in IMPLEMENTATION.md.
- [x] Maintain implementation progress and discovered issues in DEVLOG.md.
- [x] Add a Dockerfile for the portable application components and document
  that MLX execution requires a supported host environment.

## Minimal-Version Completion

- [x] Both configured topics can collect and persist recent source articles.
- [x] A user can select either topic and review all coverage or one configured
  coverage lane from the CLI.
- [x] Japanese and Chinese candidates are not presented for feedback until an
  English translation is available.
- [x] Yes, no, and maybe feedback survives restart and is auditable.
- [x] An interrupted review resumes without repeating decided articles.
- [x] One source failure does not prevent other sources or topics from being
  reviewed.
- [x] Topic and per-lane article quotas are honored without filling an edition
  below its quality threshold.
