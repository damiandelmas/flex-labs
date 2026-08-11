# XOLogic catalogue query guidance

Begin with `@orient`. Treat `xologic_content_objects`, source observations,
products, specification occurrences, taxonomy paths, links, and resources as
the native evidence surface. Use category and subcategory together; an
accessory subcategory is not the fixture category requested. Apply requested
constraints conjunctively and do not present near-matches as matches.

`preferred_spec_occurrences` is a convenient SQL interpretation, not a rewrite
of source evidence. Query `spec_occurrences` and `spec_conflicts` when values
disagree. Preserve exact product URLs returned by SQL.
