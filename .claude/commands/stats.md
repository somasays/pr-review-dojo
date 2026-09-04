Print progress statistics. Follow CLAUDE.md: never print answer-key contents.

1. Read `exercises/INDEX.md`. Build a table by domain: exercises created, test exercises reviewed (review score present), average review score, fix exercises graded, average fix score. Teach exercises count as created only.
2. Collect grade comments: for each row with a review score, fetch the exercise PR's comments (`gh api repos/{owner}/{repo}/issues/<pr>/comments`) and find the comment starting with `## Review grade`. Parse the Defects table and collect catalog ids where `Found` is `no`. Count occurrences across all graded exercises.
3. Print the domain table, then "Most missed defect ids" with the top three ids, their count, and the one-line catalog description for each (from `exercises/catalog/<domain>.md`; the id prefix maps to the domain). Print nothing else.
