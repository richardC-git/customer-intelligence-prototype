Task: write a competitor report that helps an SFS Group salesperson respond to the
company named in the SUBJECT section, which competes with SFS in construction
fastening. SFS sells roofing and cladding (RC) and flat roof (FR) fasteners and
fastening systems.

Return these fields:

- `subject_is_company`: false if the documents are not about this competitor. In
  that case use a one-sentence `summary` and leave the other lists empty.
- `key_takeaway`: the one thing a salesperson should know about this competitor
  right now, in one or two plain sentences, with supporting ids in `source_ids`.
- `summary`: two to four plain sentences on the competitor's current position.
- `developments`: recent news such as acquisitions, leadership changes, new
  products, or new facilities.
- `pricing_signals`: price increases, price lists, or discounting stated in the
  documents. Quote figures exactly and give the document's published date in the
  statement. Never estimate a price.
- `relationships`: who the competitor works with, such as named customers,
  suppliers, distributors, or partners, and how. Name only those the documents name.
- `implications`: what the documents suggest for SFS's sales conversations,
  strongest first.
- `limitations`: gaps in the evidence, such as old documents or documents that may
  describe a different company with a similar name, most important first.

Each document shows when it was published. State the date of anything more than
two years old, and never describe old information as current. Items from documents
with an unknown date must say so. Mention undated documents once at most in
`limitations`, not one by one; list only the five most important limitations.

Every development, pricing signal, relationship, and implication must list in `source_ids` the
ids of the documents that support it. Use only ids from the documents below.
Prefer fewer well-supported items over many weak ones.
