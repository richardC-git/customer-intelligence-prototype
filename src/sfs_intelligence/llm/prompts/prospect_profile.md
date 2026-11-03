Task: write a prospect profile that helps an SFS Group salesperson decide whether
and how to approach the company named in the SUBJECT section.

SFS Group is a global industrial company that makes precision components,
mechanical fastening systems, and application-specific solutions. It serves
industries including automotive, construction, electronics, medical technology,
and aerospace.

Return these fields:

- `subject_is_company`: false if the SUBJECT is not a real company or the
  documents are not about it. In that case use a one-sentence `summary` and
  leave `developments` and `opportunities` empty.
- `recommendation`: your verdict for the salesperson. `pursue` means the documents
  show a clear reason to approach now, `research_further` means promising but
  unproven, `low_priority` means a poor fit for SFS, and `not_enough_evidence`
  means the documents do not support a judgement.
- `recommendation_reason`: one or two plain sentences explaining the verdict, with
  the supporting ids in `source_ids`.
- `summary`: two to four plain sentences on what the company does and its market
  position.
- `developments`: recent developments such as expansion, leadership changes,
  hiring, or new products.
- `pricing_signals`: what the documents say about this company's pricing, such as
  price increases or price lists. Quote figures exactly and give the document's
  published date in the statement. Never estimate a price.
- `relationships`: who the company works with, such as named customers, suppliers,
  distributors, or partners, and how. Name only those the documents name.
- `opportunities`: where SFS could realistically sell and why, strongest first.
  Each needs a
  `rationale` tied to the documents. Use `product_lines` only for SFS offerings
  named above that the documents support; otherwise leave it empty.
- `limitations`: gaps in the evidence, such as conflicting documents or documents
  that may describe a different company with a similar name, most important first.

Each document shows when it was published. State the date of anything more than
two years old, and never describe old information as current. Items from documents
with an unknown date must say so. Mention undated documents once at most in
`limitations`, not one by one; list only the five most important limitations.

Every development, pricing signal, relationship, and opportunity must list in
`source_ids` the ids of the documents that support it. Use only ids from the
documents below. Prefer fewer well-supported items over many weak ones.
