"""
Feature modules for MiniSocial, organized by domain (package-by-domain).

Each folder under this package is one domain of the platform (contributions
and later e.g. heatmap), holding its own layered files:

    entities.py      pure data objects
    usecases.py      ONLY business rules (receives repositories, no sqlite)
    repositories.py  ONLY data access (SQL, no business rules)

Dependency rule: app.py -> <domain>.usecases -> <domain>.entities +
<domain>.repositories, never backwards. A domain may use another domain's
repositories, but never in a cycle. Nothing in domains/ imports Flask.
"""