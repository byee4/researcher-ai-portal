"""Static files storage helpers for resilient collectstatic behavior.

Plain-English summary:
Some third-party assets (notably django-plotly-dash support bundles) include
``sourceMappingURL`` comments that point to sourcemap files not shipped by the
package. Django's manifest post-processing treats those references like required
assets and raises ``MissingFileError`` during ``collectstatic``.

To keep hashed static URLs for real assets while avoiding brittle failures on
optional development sourcemaps, we remove only sourcemap rewrite patterns.
"""

from __future__ import annotations

from whitenoise.storage import CompressedManifestStaticFilesStorage


def _drop_sourcemap_patterns(patterns: tuple[tuple[str, tuple], ...]) -> tuple[tuple[str, tuple], ...]:
    """Return manifest patterns without sourceMappingURL rewrites."""

    cleaned: list[tuple[str, tuple]] = []
    for extension_glob, extension_patterns in patterns:
        kept_patterns = []
        for pattern_entry in extension_patterns:
            pattern = pattern_entry if isinstance(pattern_entry, str) else pattern_entry[0]
            if "sourceMappingURL" in pattern:
                continue
            kept_patterns.append(pattern_entry)
        cleaned.append((extension_glob, tuple(kept_patterns)))
    return tuple(cleaned)


class StableStaticFilesStorage(CompressedManifestStaticFilesStorage):
    """Manifest storage that ignores missing sourcemap comment targets."""

    patterns = _drop_sourcemap_patterns(CompressedManifestStaticFilesStorage.patterns)
