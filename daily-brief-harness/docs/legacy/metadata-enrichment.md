Metadata Enrichment Plan

1. Shortlist articles

Don't enrich everything.

Use your existing RSS metadata (title, source, recency, topic) to narrow the pool to roughly 30–50 candidates.

2. Fetch publisher pages concurrently

Use an async HTTP client (httpx or aiohttp) so all 30–50 URLs are fetched in parallel.

Google News links will automatically redirect to the publisher.

3. Extract metadata

Look for these tags in this order:

Metadata Priority
Description og:description → twitter:description → meta[name="description"]
Image og:image
Published time article:published_time
Section article:section (if available)

For your current needs, description is the only essential field.

4. Merge

If a better description was found, replace the RSS description.

Otherwise keep the original RSS data.

5. Continue the pipeline

Your ranker and podcast generator now work with enriched articles instead of raw RSS articles.
