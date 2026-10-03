"""
Contributions domain — user contribution activity (posts + comments),
one count per user per day. The streak feature interprets that data;
later features (e.g. the heatmap) can too.

Layering: entities (pure data), usecases (business rules only),
repositories (SQL only). Nothing here imports Flask; app.py calls
usecases, never repositories.
"""
