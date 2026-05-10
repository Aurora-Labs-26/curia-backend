# Future Things To Do

## Clustering — find more interesting multi-source episode ideas

Current approach uses cosine similarity on full-text embeddings. Voyage-3 scores 0.80+ across unrelated articles, making threshold-based clustering unreliable. Most sources end up as solos.

### Options (in order of impact)

**1. LLM-driven clustering**
Replace cosine similarity entirely. Send all source primitives to an LLM and ask it to group sources that could make interesting episodes together. It can see thematic connections, tensions, and counterpoints that embeddings miss.
- More expensive per run
- Produces semantically meaningful clusters
- Most impactful change

**2. Cluster on primitives, not full text**
Embed only `core_tensions` and `counterpoints` per source instead of full article text. Two sources with related tensions could connect well even if topics are different.
- Cheap to implement
- Better signal than full-text embeddings

**3. Tag-based grouping**
Add a domain/theme tag at ingest time (e.g. "ambition", "cognition", "markets", "relationships"). Cluster within tags first, then look for cross-tag combinations with a connecting thread.
- Requires tagging step at ingest
- More predictable and debuggable than embedding similarity

**4. Two-pass clustering**
Keep cosine for rough grouping, then add a second LLM pass on candidate pairs/triples asking: "does a tension or unexpected connection exist between these sources that could sustain a 12-minute episode?" Filter out pairs where the answer is no.
- Builds on existing infrastructure
- LLM pass adds meaningful filter on top of noisy cosine signal

---

## Other

- Idea generator: recommend format based on source primitives at the cluster level, not just standalone
- TTS: add reference WAVs for Arjun and Emeka speakers
