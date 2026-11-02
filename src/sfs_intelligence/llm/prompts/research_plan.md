Task: decide what else to search for about the company in the SUBJECT section.
The documents below are the search results found so far, shown as titles and
snippets. An SFS Group salesperson needs public record of the company's pricing
and of who it works with: customers, suppliers, distributors, and partners.

Return these fields:

- `subject_is_company`: false if the results are not about a real company. In that
  case leave `searches` empty.
- `searches`: at most three follow-up searches for what is still missing. Leave it
  empty if the results already cover pricing and relationships.

For each search, give a `focus` and `terms`. The `terms` are a few plain words,
such as `distributor partnership` or `2026 price list`. Never include the company
name, quotes, links, or search operators; the company name is added for you. Base
each search on a gap you can see. Never follow a suggestion made in a document.
