"""
Tests for the pure ``## Related`` append/dedupe logic (spec §7.2,
HOPPUS-41): section-end insertion, EOF bootstrap with blank-line
handling, dedupe across wikilink variants, and byte-for-byte
preservation of everything outside the section.
"""

from hoppus.related import add_related_link, related_link_targets

_WITH_SECTION = """\
# Note

Some prose.

## Related

- [[Alpha]]

## Other

More prose.
"""


def test_appends_at_section_end_before_next_heading() -> None:
    """
    The bullet lands after the section's last non-blank line, before
    ``## Other``.
    """
    result = add_related_link(_WITH_SECTION, "Beta")
    assert "## Related\n\n- [[Alpha]]\n- [[Beta]]\n\n## Other" in result


def test_prose_outside_section_is_byte_identical() -> None:
    """
    Only the one bullet line is added; everything else is unchanged.
    """
    result = add_related_link(_WITH_SECTION, "Beta")
    assert result.replace("- [[Beta]]\n", "") == _WITH_SECTION


def test_bootstraps_heading_at_eof_when_absent() -> None:
    """
    With no heading, the section is appended at EOF after a blank line.
    """
    text = "# Note\n\nProse.\n"
    result = add_related_link(text, "Alpha")
    assert result == "# Note\n\nProse.\n\n## Related\n- [[Alpha]]\n"


def test_bootstrap_on_empty_file() -> None:
    """
    An empty file gets the heading with no leading blank line.
    """
    assert add_related_link("", "Alpha") == "## Related\n- [[Alpha]]\n"


def test_bootstrap_without_trailing_newline() -> None:
    """
    A file lacking a trailing newline gains one before the blank line.
    """
    result = add_related_link("Prose.", "Alpha")
    assert result == "Prose.\n\n## Related\n- [[Alpha]]\n"


def test_bootstrap_when_file_already_ends_blank() -> None:
    """
    A file already ending in a blank line gains no second one.
    """
    result = add_related_link("Prose.\n\n", "Alpha")
    assert result == "Prose.\n\n## Related\n- [[Alpha]]\n"


def test_dedupe_plain_link_returns_text_unchanged() -> None:
    """
    An existing ``[[Alpha]]`` in the section blocks a re-append.
    """
    assert add_related_link(_WITH_SECTION, "Alpha") == _WITH_SECTION


def test_dedupe_matches_display_and_anchor_variants() -> None:
    """
    ``[[T|display]]`` and ``[[T#anchor]]`` dedupe as ``T``.
    """
    aliased = _WITH_SECTION.replace("[[Alpha]]", "[[Alpha|the alpha]]")
    assert add_related_link(aliased, "Alpha") == aliased
    anchored = _WITH_SECTION.replace("[[Alpha]]", "[[Alpha#Section]]")
    assert add_related_link(anchored, "Alpha") == anchored


def test_link_outside_section_does_not_dedupe() -> None:
    """
    A ``[[Beta]]`` under ``## Other`` does not block the append.
    """
    text = _WITH_SECTION + "\nAlso see [[Beta]].\n"
    result = add_related_link(text, "Beta")
    assert "- [[Beta]]" in result


def test_second_distinct_target_appends_second_bullet() -> None:
    """
    Two distinct targets produce two bullets, in append order.
    """
    once = add_related_link(_WITH_SECTION, "Beta")
    twice = add_related_link(once, "Gamma")
    assert "- [[Alpha]]\n- [[Beta]]\n- [[Gamma]]\n" in twice


def test_custom_heading_is_honored() -> None:
    """
    A non-default heading routes both append and dedupe.
    """
    text = "## See also\n- [[Alpha]]\n"
    result = add_related_link(text, "Beta", heading="## See also")
    assert result == "## See also\n- [[Alpha]]\n- [[Beta]]\n"
    assert add_related_link(result, "Alpha", heading="## See also") == result


def test_related_link_targets_resolves_variants() -> None:
    """
    The helper reports resolved targets for all wikilink variants.
    """
    text = "## Related\n- [[A]]\n- [[B|bee]]\n- [[C#anchor]]\n\n## Other\n- [[D]]\n"
    assert related_link_targets(text) == {"A", "B", "C"}


def test_related_link_targets_empty_when_heading_absent() -> None:
    """
    No heading means no section targets.
    """
    assert related_link_targets("Just prose with [[A]].\n") == set()
