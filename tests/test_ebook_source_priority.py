import unittest

from src.utils.ebook_sources import (
    DEFAULT_EBOOK_SOURCE_ORDER,
    resolve_ebook_source_order,
)


class TestResolveEbookSourceOrder(unittest.TestCase):
    def test_blank_preference_keeps_default_order(self):
        for blank in ('', '   ', ',,', None):
            self.assertEqual(
                resolve_ebook_source_order(blank),
                DEFAULT_EBOOK_SOURCE_ORDER,
                msg=f"blank preference {blank!r} changed the order",
            )

    def test_default_order_is_grimmory_first(self):
        # The historical behaviour every existing install depends on.
        self.assertEqual(DEFAULT_EBOOK_SOURCE_ORDER[0], 'Booklore')

    def test_named_source_is_promoted(self):
        order = resolve_ebook_source_order('BookOrbit')
        self.assertEqual(order[0], 'BookOrbit')
        self.assertEqual(order[1], 'Booklore')

    def test_display_name_grimmory_resolves_to_booklore(self):
        # The settings UI and logs say 'Grimmory'; the canonical key is 'Booklore'.
        self.assertEqual(
            resolve_ebook_source_order('Grimmory,BookOrbit'),
            resolve_ebook_source_order('Booklore,BookOrbit'),
        )

    def test_every_source_retained_exactly_once(self):
        order = resolve_ebook_source_order('Kavita,BookOrbit')
        self.assertEqual(sorted(order), sorted(DEFAULT_EBOOK_SOURCE_ORDER))
        self.assertEqual(len(order), len(set(order)))

    def test_unlisted_sources_keep_relative_order(self):
        order = resolve_ebook_source_order('CWA')
        remainder = [name for name in order if name != 'CWA']
        expected = [name for name in DEFAULT_EBOOK_SOURCE_ORDER if name != 'CWA']
        self.assertEqual(remainder, expected)

    def test_unknown_names_are_ignored(self):
        self.assertEqual(
            resolve_ebook_source_order('Nonsense,BookOrbit,AlsoNonsense'),
            resolve_ebook_source_order('BookOrbit'),
        )

    def test_repeated_name_does_not_duplicate(self):
        order = resolve_ebook_source_order('BookOrbit,bookorbit,BOOKORBIT')
        self.assertEqual(order.count('BookOrbit'), 1)
        self.assertEqual(order[0], 'BookOrbit')

    def test_whitespace_and_case_tolerated(self):
        self.assertEqual(
            resolve_ebook_source_order('  bookorbit ,  local file  '),
            ('BookOrbit', 'Local File') + tuple(
                n for n in DEFAULT_EBOOK_SOURCE_ORDER if n not in ('BookOrbit', 'Local File')
            ),
        )

    def test_full_explicit_order_is_honoured(self):
        reversed_order = tuple(reversed(DEFAULT_EBOOK_SOURCE_ORDER))
        self.assertEqual(
            resolve_ebook_source_order(','.join(reversed_order)),
            reversed_order,
        )

    def test_list_preference_accepted(self):
        self.assertEqual(
            resolve_ebook_source_order(['BookOrbit', 'Kavita']),
            resolve_ebook_source_order('BookOrbit,Kavita'),
        )

    def test_available_subset_is_respected(self):
        order = resolve_ebook_source_order('CWA', available=('Booklore', 'BookOrbit'))
        self.assertEqual(order, ('Booklore', 'BookOrbit'))


if __name__ == '__main__':
    unittest.main()
