# Retrieval evaluation

Generated 2026-10-06 01:40 by `scripts/evaluate.py`. Corpus: 22 pages, 233 passages. Embedding model: `bge-small-en-v1.5`. A question counts as a hit at k when a passage from a page that answers it is among the top k results.

**All questions**

| Search mode | Questions | hit@1 | hit@3 | hit@6 | MRR | ms per query |
|---|---|---|---|---|---|---|
| vector | 52 | 88% | 96% | 98% | 0.926 | 68 |
| keyword | 52 | 77% | 94% | 96% | 0.860 | 68 |
| hybrid | 52 | 90% | 94% | 98% | 0.933 | 66 |

**Questions using the page's own terms**

| Search mode | Questions | hit@1 | hit@3 | hit@6 | MRR | ms per query |
|---|---|---|---|---|---|---|
| vector | 35 | 97% | 100% | 100% | 0.981 | 63 |
| keyword | 35 | 89% | 100% | 100% | 0.943 | 63 |
| hybrid | 35 | 97% | 100% | 100% | 0.986 | 62 |

**Questions that paraphrase the idea**

| Search mode | Questions | hit@1 | hit@3 | hit@6 | MRR | ms per query |
|---|---|---|---|---|---|---|
| vector | 17 | 71% | 88% | 94% | 0.813 | 76 |
| keyword | 17 | 53% | 82% | 88% | 0.690 | 76 |
| hybrid | 17 | 76% | 82% | 94% | 0.825 | 74 |

**Questions hybrid search did not answer within the top 6**

- How do I download data from a web address?
