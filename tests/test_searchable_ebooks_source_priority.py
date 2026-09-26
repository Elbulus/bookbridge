"""EBOOK_SOURCE_PRIORITY decides which provider owns a file two of them index.

Grimmory and BookOrbit are commonly pointed at one shared disk, which makes the
filenames identical. The candidate pool dedupes on filename across providers, so
whichever runs first claims the book and the other can never contribute it.
"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch


SHARED = 'A Court of Frost and Starlight - Sarah J Maas.epub'
ORBIT_ONLY = 'Brimstone - Callie Hart.epub'


class _FakeBooklore:
    def is_configured(self):
        return True

    def get_all_books(self):
        return [{'fileName': SHARED, 'title': 'A Court of Frost and Starlight', 'id': 11}]

    def search_books(self, term):
        return self.get_all_books()


class _FakeBookorbit:
    def is_configured(self):
        return True

    def search_ebooks(self, term):
        return [
            {'fileName': SHARED, 'title': 'A Court of Frost and Starlight', 'id': 91},
            {'fileName': ORBIT_ONLY, 'title': 'Brimstone', 'id': 92},
        ]

    def get_all_ebooks(self):
        return self.search_ebooks(None)


class _NotConfigured:
    def is_configured(self):
        return False


class _FakeBundle:
    def __init__(self):
        self.booklore_client = _FakeBooklore()
        self.bookorbit_client = _FakeBookorbit()
        self.bookfusion_client = _NotConfigured()
        self.kavita_client = _NotConfigured()
        self.abs_client = None
        self.library_service = None


class TestSearchableEbookSourcePriority(unittest.TestCase):
    def _sources_by_name(self, priority):
        from src import web_server

        bundle = _FakeBundle()
        token = web_server._active_bundle.set(bundle)
        env = dict(os.environ)
        env['EBOOK_SOURCE_PRIORITY'] = priority
        try:
            # A directory that does not exist keeps the Local File collector inert.
            with patch.object(web_server, 'EBOOK_DIR', Path('/nonexistent-ebook-dir'), create=True), \
                    patch.dict(os.environ, env, clear=True):
                results = web_server.get_searchable_ebooks('court of frost')
        finally:
            web_server._active_bundle.reset(token)
        return {r.name: r.source for r in results}

    def test_default_order_attributes_shared_file_to_grimmory(self):
        by_name = self._sources_by_name('')
        self.assertEqual(by_name[SHARED], 'Grimmory')

    def test_priority_attributes_shared_file_to_bookorbit(self):
        by_name = self._sources_by_name('BookOrbit')
        self.assertEqual(by_name[SHARED], 'BookOrbit')

    def test_grimmory_still_contributes_when_bookorbit_leads(self):
        # Reordering must not drop the deprioritised provider, only demote it.
        by_name = self._sources_by_name('BookOrbit')
        self.assertIn(ORBIT_ONLY, by_name)
        self.assertEqual(len(by_name), 2)

    def test_shared_file_is_never_duplicated(self):
        from src import web_server

        bundle = _FakeBundle()
        token = web_server._active_bundle.set(bundle)
        try:
            with patch.object(web_server, 'EBOOK_DIR', Path('/nonexistent-ebook-dir'), create=True), \
                    patch.dict(os.environ, {'EBOOK_SOURCE_PRIORITY': 'BookOrbit'}):
                results = web_server.get_searchable_ebooks('court of frost')
        finally:
            web_server._active_bundle.reset(token)
        names = [r.name for r in results]
        self.assertEqual(len(names), len(set(names)))

    def test_unknown_priority_falls_back_to_default(self):
        by_name = self._sources_by_name('Nonsense')
        self.assertEqual(by_name[SHARED], 'Grimmory')


if __name__ == '__main__':
    unittest.main()
