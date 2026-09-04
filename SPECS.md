# Daily News - Initial Specification

## Goal

Collect recent articles from configured sources and maintain a daily stream of
relevant news for each configured topic.

The system should learn what belongs in a topic through explicit feedback while
retaining enough history to avoid repeatedly showing the same canonical article
and to evaluate future ranking approaches.

Initial topics are:

- AI services and tools for software engineering.
- State-of-the-art AI research papers.

Initial coverage should include global English-language sources and Japanese
sources. The source and ranking model must also support a China coverage lane
without requiring a separate topic definition.

The initial product displays source articles and can generate a short,
on-demand local-model summary during review. It does not place generated
editorial summaries in the feed or answer questions from article content.

## Core Principles

- APIs and RSS or Atom feeds are preferred over web crawling.
- Collection favors recall; publication favors precision.
- An embedding match is a recall signal, not proof of relevance.
- The MVP treats each canonical article as one independent feed item.
- Content language and geographic market are separate dimensions.
- Candidate coverage is balanced before global ranking so a high-volume language
  or market does not silently dominate the feed.
- Selected, rejected, borderline, and duplicate candidates are retained.
- Every automated decision retains its inputs, scores, reasons, and versioned
  configuration.
- Explicit feedback is authoritative; passive behavior is not treated as a
  strong signal.
- Evaluation controls whether additional retrieval or learning complexity is
  introduced.

## Initial Package Structure

The intended package boundaries are:

```text
app/
  config.py
  cli.py

  knowledge/
    store.py
    articles.py
    topics.py
    feedback.py
    migrations/

  llm/
    __init__.py
    runtime.py
    mlx.py
    llama_cpp.py

  offline/
    pipeline.py
    collection.py
    preparation.py
    ranking.py
    evaluation.py

  online/
    labeling.py
    feed.py

tests/
  knowledge/
  offline/
  online/
```

This is a boundary map, not a requirement to create empty modules. A file or
subpackage should be introduced when its capability is implemented.

`knowledge/` owns database access and canonical records shared across flows.
Article-specific persistence stays with `articles.py`, topic definitions with
`topics.py`, and append-only decisions with `feedback.py`. `store.py` owns
connections, transactions, and shared database mechanics.

`llm/runtime.py` owns the provider-neutral inference contracts, errors, prompts,
and output-safety policy shared by local backends.
`llm/mlx.py` owns MLX imports, model loading, tokenization, and Apple-specific
behavior. `llm/llama_cpp.py` owns lazy llama.cpp imports, local or pinned remote
GGUF resolution, the application-owned model cache, chat completion parsing,
and accelerator configuration. Importing the public `llm` package must not
import either third-party runtime eagerly.

`offline/pipeline.py` coordinates collection, preparation, ranking, and
evaluation. Those peer steps own their local types, prompts, and validation and
do not invoke one another. Source-specific integrations remain in
`collection.py` until independent growth justifies a `collection/` subpackage.

`online/labeling.py` owns the interactive feedback session. `online/feed.py`
owns feed reading and hide-and-replace behavior. `cli.py` parses commands,
constructs concrete dependencies, and invokes these workflows without
implementing their policy.

Dependencies flow inward:

```text
cli -> offline / online
offline -> knowledge / llm / config
online -> knowledge / config
llm/mlx -> llm/runtime / config
llm/llama_cpp -> llm/runtime / config
knowledge -> config
```

`knowledge` and `llm` must not import workflow modules. `offline` and `online`
must not import one another. The offline coordinator translates between peer
step contracts rather than allowing sideways imports.

## Product Concepts

### Topic

A topic is a versioned editorial policy, not a single search query. It contains:

- A name and natural-language description.
- Independently meaningful facets of the topic.
- Positive examples and hard negative examples.
- Explicit exclusions.
- Optional known entities and lexical terms used as seeds, not allowlists.
- Source preferences, coverage lanes, and collection scope.
- Freshness and publication limits.
- Ranking and selection policy.

Changing a topic creates a new definition version. Historical decisions remain
associated with the version that produced them.

### Language and market coverage

A coverage lane applies one topic definition to a particular market and set of
accepted languages. It allows views such as “hot in Japan” or “hot in China”
without copying and independently maintaining the topic.

For example:

```yaml
coverage:
  - id: global-english
    markets: [global]
    languages: [en]
  - id: japan
    markets: [JP]
    languages: [ja, en]
  - id: china
    markets: [CN]
    languages: [zh-Hans, en]
```

Language uses canonical BCP 47 tags such as `en`, `ja`, `zh-Hans`, and
`zh-Hant`. Markets use ISO country codes where applicable, with an explicit
`global` value for sources or articles without a single-country scope.

A source declares its publishing languages and home or specialist markets. An
article records its detected language separately from the markets it discusses.
Neither language nor source location alone proves market relevance: an English
article may be important China coverage, while a Chinese-language article may
describe a global release. Derived market assignments retain their evidence,
confidence, and classifier version.

Candidate generation runs independently within each configured coverage lane.
Lane results are then merged for the complete topic feed. A topic may configure
soft targets or maximum shares to preserve diversity, but a quota must not force
low-quality articles into the feed. Users can also review or display one lane
independently.

### Article quotas

Edition size is configurable at two levels:

- The topic defines the maximum number of canonical articles in one edition.
- Each topic-and-coverage-lane combination may define a soft target and a hard
  maximum number of articles.

For example:

```yaml
edition:
  max_articles: 12

coverage:
  - id: global-english
    target_articles: 6
    max_articles: 8
  - id: japan
    target_articles: 3
    max_articles: 4
  - id: china
    target_articles: 3
    max_articles: 4
```

A target guides allocation among qualifying articles; it is not a minimum and
must not lower the publication threshold. A maximum is a hard cap. An edition
may contain fewer articles than any configured target or topic maximum when
there are not enough qualifying candidates.

Quotas count canonical articles. Translations and repeated fetches of the same
canonical article do not consume additional quota. An article matching multiple
lanes is assigned one primary lane for quota accounting within an edition and
counts once toward the topic maximum. That assignment and its evidence are
persisted so selection can be reproduced.

Selection first considers candidates within each lane, then merges and
deduplicates identical canonical articles. Remaining topic capacity is filled
by the highest-ranked qualifying articles whose lane maxima are not exhausted.

Topic facets and lexical expansions may have localized forms. The system may
use multilingual retrieval or translated derived text for matching, but topic
assessment remains linked to the original source text.

### Translation

English translations are required for reviewing non-English articles. The
original title, excerpt, and extracted text remain authoritative and are always
retained when storage is permitted.

The CLI displays the original title and a clearly labeled English translation.
It also displays a translated excerpt and can show an on-demand translation of
the extracted article text. Translation is faithful rendering, not an editorial
summary; the output still links to the original article.

Translation uses the provider-neutral local inference contract. MLX-LM and
llama.cpp are supported in-process backends on Macs; no external translation
service is required.

The llama.cpp backend accepts either an explicit local GGUF path or a Hugging
Face repository, filename, and immutable commit revision. It downloads remote
artifacts into a Daily News cache and reuses them offline. It must not depend on
a separately running inference or model-management server.

Translations are cached derived records. They retain source and target
languages, input content hash, model and prompt versions, creation time, and
lineage to the translated fields. A changed source version produces a new
translation rather than overwriting the old one.

A non-English item without a usable English translation is deferred from human
review. Translation failure is recorded as a processing outcome and must not be
converted into negative topic feedback.

Repeat prevention initially operates only on canonical article identity across
languages and markets. Different publications covering the same underlying
event remain independent articles in the MVP.

### Source document

A source document is one fetched representation of a URL. It retains source,
fetch time, response metadata, extracted content, and extraction status.

### Article

An article is the canonical representation of a published item. It may have
multiple source-document versions as its page changes or is fetched again.

At minimum, an article retains:

- Canonical URL and source.
- Original title and available excerpt.
- Author and language when available.
- Relevant markets and the provenance of inferred market assignments.
- Reported publication and update times when available.
- Discovery and fetch times.
- Confidence and provenance for derived dates and metadata.
- Canonical extracted text when storage is permitted.

### Article, assessment, and feedback lifecycle

The visible lifecycle combines processing with topic-scoped assessment and
feedback:

```text
collected -> prepared -> candidate
                           |-- yes feedback ---> user_approved
                           |-- maybe feedback -> user_borderline
                           |-- no feedback ----> user_rejected
                           `-- policy ---------> selected

user_approved ---------------------------------> selected
user_borderline -------- optional policy -----> selected
selected -------------------------------------> user_hidden
```

Preparation may also mark an unusable collected item `preparation_failed`, and
an automated topic assessment may mark it `system_rejected`.
These names describe related records and effective dispositions rather than one
mutable global status. Collection and preparation state belongs to the source
document or canonical article. Candidacy, approval, rejection, and selection
are scoped to a topic because the same article may be selected for one topic
and rejected for another. Hiding is scoped to the topic feed, and its reversal
is retained as another feedback event.

- `collected`: the source document and available metadata have been stored,
  whether or not later processing succeeds.
- `preparation_failed`: preparation found the item malformed, inaccessible, or
  otherwise unusable. The reason and stage are required.
- `system_rejected`: automated topic assessment found the item irrelevant,
  stale, duplicate, or otherwise ineligible. The reason is retained.
- `candidate`: the article is plausibly relevant to a topic and remains in its
  ranked pool even when it is not currently selected. Candidates may be marked
  primary, borderline, or reserve without changing lifecycle state.
- `user_approved`: explicit `yes` feedback says the article belongs in the
  topic and is worth showing. Approval raises its publication eligibility but
  does not require that it appear in a particular edition.
- `user_borderline`: explicit `maybe` feedback says the article is related
  but not a firm positive. It remains available for evaluation and optional
  fallback selection.
- `user_rejected`: explicit `no` feedback says the article should not be
  shown for this topic.
- `selected`: the article was assigned to a topic edition and a feed entry was
  created. Selection may be automatic; prior human approval is not required.
- `user_hidden`: the user removed a selected entry from the topic feed. The next
  eligible candidate may replace it. Hiding does not automatically mean the
  article is off-topic, so an optional reason distinguishes irrelevance from
  lack of interest, weakness, duplication, or another cause.

Transitions never delete the underlying article, assessment, feed entry, or
feedback history. A new topic definition or ranking version may reassess a
previously `system_rejected` or `user_borderline` article by creating a new
assessment.
A `user_rejected` or `user_hidden` article must not silently reappear for the
same topic unless the user reverses the decision or the system identifies a
material update.

Feedback events are immutable inputs to this lifecycle. `yes`, `no`, `maybe`,
and `hide` create new events; they do not overwrite earlier assessments or feed
entries. `undo` creates a compensating event, and the current effective
disposition is derived from the event history. A later automated assessment may
record a different prediction but must not silently override effective explicit
feedback.

### Article identity

The MVP does not group different articles into stories. Canonical URL identity
prevents repeated fetches of the same page from creating new articles.
Research-paper identifiers such as DOI, arXiv ID, or OpenReview ID may
deterministically identify versions of the same paper. These identity rules do
not attempt to cluster independent coverage of one event.

### Topic assessment

An assessment records how one article was evaluated for one topic. It
retains candidate-source signals, reranking results, decision, explanation,
topic version, and model or algorithm versions.

### Feed entry

A feed entry records that an article was presented for a topic on a particular
date.

### Feedback event

Feedback is append-only. An event records the topic, article, action,
optional reason, timestamp, session, and the versions of the topic and ranking
system that produced the item. Undo creates a compensating event rather than
deleting history.

## Collection

The source priority is:

1. Official or supported APIs.
2. RSS or Atom feeds.
3. Sitemaps and structured page metadata.
4. Public HTML pages.
5. Browser-rendered pages for explicitly supported sources.

Each source configuration defines its fetch method, schedule, rate limit, retry
policy, publishing languages, market coverage, attribution, and permitted
content retention.

Collection must respect access restrictions, robots policy, and source rate
limits. It must not bypass authentication, paywalls, CAPTCHAs, or technical
access controls. A blocked or failing source reduces coverage but does not fail
the complete collection run.

Daily processing should use an overlapping lookback window rather than a strict
24-hour publication cutoff. Eligibility considers reported publication time,
discovery time, previous assessments, and previous feed appearances. This
prevents delayed feeds, unreliable dates, or failed runs from losing articles.

Hacker News uses its official versioned Firebase API. Collection reads a
configurable bounded prefix of `topstories`, fetches each referenced item, and
retains live top-level stories. Dead, deleted, and non-story items are ignored.
An outbound story URL is canonical when present; Ask HN and other stories
without one use their Hacker News discussion URL. When an outbound URL exists,
the distinct Hacker News discussion URL is retained as an article link and
displayed alongside the original article in review, bookmarks, and feeds.
Individual item failures make the source result partial without discarding
successfully fetched items.

Because Hacker News is a broad source rather than a topic-specific publication,
its articles enter a topic candidate pool only when their title or available
text matches a configured topic term or entity. The signal favors recall and is
not itself an automated relevance decision.

Bluesky collection uses the public AppView search API only during an explicit
collection run. Each configured search is bounded by `max_items` and may
paginate within that run, but no background stream or scheduled poller is
maintained. Only posts containing an external HTTP or HTTPS article link are
collected. The outbound article URL is canonical; the Bluesky post URL is
retained as a discussion link, and the complete returned post remains source
metadata. Linkless posts are ignored. Multiple posts may therefore provide
independent provenance for one deduplicated canonical article. Bluesky remains
a broad source, so its candidates must also pass topic lexical matching.

When a social post links to a supported aggregator discussion rather than
directly to an article, collection resolves the aggregator through its supported
API before preparation. The final outbound article URL is canonical, while the
social post and every intermediate aggregator page are retained as discussion
links. Resolution is bounded, detects loops, and degrades to the last reachable
URL on failure. This ensures article fetching and summaries target the article
while preserving the complete discovery path. Hacker News is the first
resolver; additional aggregators may be registered without changing social
source adapters.

## Preparation

Before topic ranking, the system:

1. Validates and normalizes collected metadata.
2. Resolves canonical URLs.
3. Extracts permitted article content.
4. Detects repeated versions of the same article.
5. Resolves stable research-paper identifiers when available.
6. Records preparation failures without discarding the source document.

Original source values and provenance are retained even when normalized values
are derived.

## Candidate Generation and Ranking

Candidate generation is intentionally broader than final selection. Depending
on measured need, its union may include:

- Recent articles from trusted or topic-specific sources.
- Lexical retrieval using topic facets, entities, and expanded terms.
- Semantic retrieval against separate topic facets.
- Previously borderline candidates with meaningful new evidence.
- A small exploration sample outside the normal retrieval results.

No single lexical query or embedding threshold is allowed to define topic
membership. Embeddings may contribute candidates but must not directly select
feed entries.

A local model performs late topic assessment over the bounded candidate set.
The assessment distinguishes at least:

- Relevance to the topic.
- Significance or usefulness.
- Whether the same canonical article was previously shown.
- Confidence in the decision.

The main feed uses a conservative selection policy. Borderline candidates
remain available for review or replacement without being presented as primary
recommendations.

Model inference runs locally. MLX-LM accepts a model repository identifier;
llama.cpp accepts a local GGUF path and requests full accelerator offload.
Workflow code depends only on the provider-neutral inference contract. The
provider and model repository or path are selected under `llm` in
`settings.yaml`; an omitted or null model disables local inference.

## Feedback CLI

The initial interactive interface presents one candidate at a time. It shows
the original title, source, available publication time, language, market,
excerpt, and URL. Displayed original and translated excerpts are capped at 500
characters, with an explicit `[s]ummary for more` hint when truncated; the
stored source excerpt remains unchanged. An ordered, non-empty
`preferred_languages` setting defines which article languages are shown
natively. Articles outside that set are translated into its first language,
and the translated extracted text can be shown on demand.
Uncached automatic translation reports the field, source language, and local
model while it is running. Translations must not display or cache model
reasoning traces.

Required decisions are:

- `yes`: this belongs in the topic and is worth showing.
- `no`: this should not be shown for the topic.
- `maybe`: it is related, but relevance, significance, or confidence is
  insufficient for a firm decision.

The user can open the source article, toggle a durable topic-scoped bookmark,
undo the latest decision, and save and resume a session. Bookmarking does not
record feedback or advance the review queue. Saved bookmarks can be listed
across all topics or for one topic and removed explicitly. The `yb` compound
action ensures the bookmark is saved, records `yes`, and
advances the queue as a normal positive decision. Optional reason codes may
distinguish off-topic, weak, duplicate, stale, inaccessible, or another
user-provided reason. Each standard
reason has a visible single-key shortcut in the interactive prompt.

The `summary` action fetches the current public HTML article only when requested,
checks the site's robots policy, bounds the response size, extracts main text,
and asks the configured local model for a short English summary. It does not
record feedback or advance the queue. Extracted content versions and summaries
are cached with URL, content hash, model, and prompt lineage. Fetch, extraction,
or inference failure leaves the candidate in place and is not negative feedback.
Displayed and cached summaries must end with a complete sentence and must never
contain the model's reasoning trace.

Candidate ordering should prioritize informative judgments: model disagreement,
items near decision thresholds, underrepresented topic facets, suspected
duplicates, underrepresented coverage lanes, and a bounded exploration sample.

## Feed Behavior

For each topic and edition, the system returns a ranked list of original source
articles without generating an editorial summary. Selection respects the topic
maximum and lane allocations defined by the applicable topic version.

The same canonical article must not be shown again for the same topic unless a
new source version is explicitly eligible as a material update. Separate
articles covering the same event may both appear in the MVP.

Hiding an entry reveals the next eligible candidate from the stored ordering
without requiring collection or complete reranking. The hide action is also
recorded as explicit feedback.

## Persistence and Reproducibility

The system stores collected documents, on-demand article-content versions,
cached summaries, canonical articles, topic versions, assessments, feed
entries, feedback events, and processing-run metadata.

Derived records retain sufficient version information to reproduce or compare
decisions, including applicable topic, prompt, model, retrieval, and ranking
versions. Reprocessing must not erase historical decisions.

## Evaluation

Each topic should develop a labeled evaluation set containing clear positives,
clear negatives, borderline cases, duplicates, and relevant articles without
obvious keywords.

Evaluation should measure at least:

- Recall before late reranking.
- Precision of primary feed selections.
- Repeated-canonical-article rate across editions.
- Agreement with explicit `yes`, `no`, and `maybe` feedback.
- Coverage across topic facets and sources.
- Recall and selection quality sliced by language and market.
- Translation success rate and deferred-review count by language.
- Inference time and resource use.

Ranking, retrieval, topic-definition, or model changes are replayed against
historical candidates before adoption. More complex learned ranking is added
only when the accumulated labels and measured failure modes justify it.

## Delivery Phases

1. **Collection and storage:** configure topics and sources, collect through RSS
   or APIs, normalize articles, and retain processing history.
2. **Feedback loop:** construct a broad candidate queue and label it through the
   `yes` / `no` / `maybe` CLI.
3. **Ranking baseline:** apply local late reranking, compare it with accumulated
   labels, and establish conservative publication thresholds.
4. **Daily feed:** create topic editions, prevent repeated articles, and support
   hide-and-replace behavior.
5. **Measured expansion:** add crawl adapters, retrieval channels, source
   discovery, or learned ranking only in response to evaluated needs.

## Explicitly Deferred

- Cross-article topic or story analysis: grouping independent articles about the
  same event across sources, languages, or markets; choosing canonical coverage;
  identifying alternate coverage; and distinguishing repetition from a
  material update or local perspective. Introduce this only with a labeled
  evaluation set for multilingual clustering quality.
- Automatic feed summaries, editorial synthesis across articles, or
  conversational answers. A short single-article summary remains available
  only as an explicit review action.
- A graphical or web interface.
- Autonomous topic creation.
- Personalized ranking for multiple users.
- Training or fine-tuning a model from early feedback.
- Distributed crawling or processing infrastructure.
- Browser automation for unsupported or restricted sites.
- A large controlled taxonomy or knowledge graph.
