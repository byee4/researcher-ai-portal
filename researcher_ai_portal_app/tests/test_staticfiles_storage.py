from __future__ import annotations

from researcher_ai_portal.staticfiles import StableStaticFilesStorage


def test_stable_staticfiles_storage_skips_sourcemap_rewrites():
    """Ensure collectstatic does not fail on missing third-party sourcemaps."""

    all_patterns = []
    for _glob, ext_patterns in StableStaticFilesStorage.patterns:
        for entry in ext_patterns:
            pattern = entry if isinstance(entry, str) else entry[0]
            all_patterns.append(pattern)

    assert all("sourceMappingURL" not in pattern for pattern in all_patterns)
    assert any(("url\\(" in pattern) or ("@import" in pattern) for pattern in all_patterns)
