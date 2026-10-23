import io
import json
import unittest
import zipfile
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
import sys
import os
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent))

from src.utils.smil_extractor import AUDIO_DURATIONS_ENTRY, SmilExtractor

class TestSmilExtractorRobustness(unittest.TestCase):
    def setUp(self):
        self.extractor = SmilExtractor()

    def test_ms_timestamps(self):
        """Verify ms timestamps are converted to seconds correctly."""
        # Test 500ms -> 0.5s
        res = self.extractor._parse_timestamp("500ms")
        self.assertEqual(res, 0.5)

        # Test 1s
        res = self.extractor._parse_timestamp("1s")
        self.assertEqual(res, 1.0)

        # Test 1000ms
        res = self.extractor._parse_timestamp("1000ms")
        self.assertEqual(res, 1.0)

        # Test mixed/invalid
        self.assertEqual(self.extractor._parse_timestamp(""), 0.0)
        self.assertEqual(self.extractor._parse_timestamp("invalid"), 0.0)

    def test_malformed_ms_timestamp_returns_zero(self):
        """Verify non-numeric ms values return 0.0 instead of raising."""
        self.assertEqual(self.extractor._parse_timestamp("abcms"), 0.0)
        self.assertEqual(self.extractor._parse_timestamp("ms"), 0.0)
        self.assertEqual(self.extractor._parse_timestamp("12.34.56ms"), 0.0)

    def test_strip_namespaces(self):
        """Verify namespaces and prefixes are stripped correctly."""
        # Default namespace
        xml = '<smil xmlns="http://www.w3.org/ns/SMIL" version="3.0"><body><par>text</par></body></smil>'
        stripped = self.extractor._strip_namespaces(xml)
        self.assertNotIn('xmlns=', stripped)
        self.assertIn('<smil', stripped)
        
        # Named namespace
        xml = '<smil xmlns:epub="http://www.idpf.org/2007/ops"><epub:text>content</epub:text></smil>'
        stripped = self.extractor._strip_namespaces(xml)
        self.assertNotIn('xmlns:epub=', stripped)
        self.assertNotIn('epub:', stripped) # Check prefix removal in tag
        self.assertIn('<text>', stripped) # Check tag preservation
        self.assertIn('</text>', stripped)

        # Complex mixed
        xml = '<seq epub:textref="foo.html" xmlns:epub="ops">Content</seq>'
        stripped = self.extractor._strip_namespaces(xml)
        self.assertIn('<seq', stripped)
        self.assertNotIn('xmlns:epub=', stripped)
        # Attribute prefixes SHOULD be stripped now to prevent unbound prefix errors
        self.assertNotIn('epub:textref', stripped)
        self.assertIn(' textref=', stripped)
        # That strips the TAG prefix. It does NOT strip attribute prefixes.
        # This is expected behavior for _strip_namespaces as implemented.
        # But we should verification that it handles the TAGS correctly.
        
    def test_url_encoded_filenames(self):
        """Verify URL encoded filenames in SMIL are decoded before reading."""
        # Mock ZipFile
        mock_zf = MagicMock()
        
        smil_content = """
        <smil xmlns="http://www.w3.org/ns/SMIL" version="3.0">
            <body>
                <par>
                    <text src="Chapter%201.html#p1"/>
                    <audio src="audio/ch1.mp3" clipBegin="0s" clipEnd="10s"/>
                </par>
            </body>
        </smil>
        """
        
        # Mock read behavior
        def side_effect(path):
            str_path = str(path).replace('\\', '/')
            if str_path.endswith('.smil'):
                return smil_content.encode('utf-8')
            if str_path == 'OEBPS/Chapter 1.html': # Decoded path
                 return b'<html><body id="p1">Hello World</body></html>'
            if 'Chapter%201.html' in str_path: # Encoded path (Should NOT happen if fix works)
                 return b'<html><body id="p1">FAIL_ENCODED</body></html>'
            raise KeyError(f"File not found: {str_path}")
            
        mock_zf.read.side_effect = side_effect
        mock_zf.getinfo.side_effect = lambda x: True
        
        # We need to ensure _get_text_content logic uses the decoded path.
        # Run _process_smil_absolute
        # Note: path passed to process is the SMIL path.
        segments = self.extractor._process_smil_absolute(mock_zf, "OEBPS/chapter1.smil")
        
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]['text'], 'Hello World')
        self.assertNotEqual(segments[0]['text'], 'FAIL_ENCODED')

    def test_clip_times_are_placed_by_their_audio_file_chapter(self):
        """Verify each audio file's clip times start at that file's audiobook chapter."""
        opf = """<?xml version="1.0"?>
        <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
            <manifest>
                <item id="c1" href="ch1.html" media-type="application/xhtml+xml" media-overlay="m1"/>
                <item id="c2" href="ch2.html" media-type="application/xhtml+xml" media-overlay="m2"/>
                <item id="m1" href="ch1.smil" media-type="application/smil+xml"/>
                <item id="m2" href="ch2.smil" media-type="application/smil+xml"/>
            </manifest>
            <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
        </package>"""

        def smil(chapter, clips):
            pars = ''.join(
                f'<par><text src="ch{chapter}.html#p{n}"/>'
                f'<audio src="Audio/{audio}" clipBegin="{begin}s" clipEnd="{end}s"/></par>'
                for n, audio, begin, end in clips
            )
            return f'<smil xmlns="http://www.w3.org/ns/SMIL" version="3.0"><body>{pars}</body></smil>'

        def html(numbers):
            return '<html><body>' + ''.join(f'<p id="p{n}">Sentence {n}.</p>' for n in numbers) + '</body></html>'

        # The second text chapter starts in the first audio file and ends in the second.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('META-INF/container.xml',
                        '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
            zf.writestr('OEBPS/content.opf', opf)
            zf.writestr('OEBPS/ch1.smil', smil(1, [(1, '00000-00001.m4a', 0, 100), (2, '00000-00001.m4a', 100, 200)]))
            zf.writestr('OEBPS/ch2.smil', smil(2, [(3, '00000-00001.m4a', 200, 300),
                                                   (4, '00000-00002.m4a', 0, 50),
                                                   (5, '00000-00002.m4a', 50, 120)]))
            zf.writestr('OEBPS/ch1.html', html([1, 2]))
            zf.writestr('OEBPS/ch2.html', html([3, 4, 5]))
        chapters = [{'start': 0.0, 'end': 305.0}, {'start': 305.0, 'end': 430.0}]

        transcript = self.extractor.extract_transcript(buffer, chapters)

        self.assertEqual(
            [(s['start'], s['text']) for s in transcript],
            [(0.0, 'Sentence 1.'), (100.0, 'Sentence 2.'), (200.0, 'Sentence 3.'),
             (305.0, 'Sentence 4.'), (355.0, 'Sentence 5.')],
        )

    def test_audio_file_offsets_need_one_file_per_chapter(self):
        """Verify per-file offsets are used only when the audio files and chapters line up."""
        def epub_with(audio_files, archived_audio=()):
            pars = ''.join(
                f'<par><text src="ch1.html#p{n}"/><audio src="{name}" clipBegin="0s" clipEnd="1s"/></par>'
                for n, name in enumerate(audio_files)
            )
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as zf:
                zf.writestr('ch1.smil', f'<smil xmlns="http://www.w3.org/ns/SMIL"><body>{pars}</body></smil>')
                for name in archived_audio:
                    zf.writestr(name, b'')
            return zipfile.ZipFile(buffer)

        two_chapters = [{'start': 0.0, 'end': 10.0}, {'start': 10.0, 'end': 20.0}]
        three_chapters = two_chapters + [{'start': 20.0, 'end': 30.0}]

        self.assertEqual(self.extractor._audio_file_offsets(epub_with(['a.mp3', 'b.mp3']), ['ch1.smil'], two_chapters),
                         {'a.mp3': 0.0, 'b.mp3': 10.0})
        self.assertEqual(self.extractor._audio_file_offsets(epub_with(['a.mp3', 'b.mp3']), ['ch1.smil'], three_chapters), {})
        self.assertEqual(self.extractor._audio_file_offsets(epub_with(['a.mp3', 'a.mp3']), ['ch1.smil'], two_chapters), {})
        self.assertEqual(self.extractor._audio_file_offsets(epub_with(['a.mp3', 'b.mp3']), ['ch1.smil'], None), {})
        # An audio file the aligner left without clips still takes up its chapter.
        self.assertEqual(
            self.extractor._audio_file_offsets(
                epub_with(['Audio/a.mp3', 'Audio/c.mp3'], ['Audio/a.mp3', 'Audio/b.mp3', 'Audio/c.mp3']),
                ['ch1.smil'], three_chapters),
            {'a.mp3': 0.0, 'b.mp3': 10.0, 'c.mp3': 20.0})

    def test_stored_audio_lengths_place_each_file_whatever_the_chapters(self):
        """Verify recorded audio lengths set each file's start, even when the chapters don't line up."""
        def epub_with(lengths):
            names = ['00000-00001.m4a', '00000-00002.m4a', '00000-00003.m4a']
            pars = ''.join(
                f'<par><text src="ch1.html#p{n}"/><audio src="Audio/{name}" clipBegin="0s" clipEnd="1s"/></par>'
                for n, name in enumerate(names)
            )
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as zf:
                zf.writestr('OEBPS/ch1.smil', f'<smil xmlns="http://www.w3.org/ns/SMIL"><body>{pars}</body></smil>')
                for name in names:
                    zf.writestr(f'OEBPS/Audio/{name}', b'')
                zf.writestr(AUDIO_DURATIONS_ENTRY, json.dumps(
                    {f'OEBPS/Audio/{name}': length for name, length in zip(names, lengths) if length is not None}))
            return zipfile.ZipFile(buffer)

        two_chapters = [{'start': 0.0, 'end': 150.0}, {'start': 150.0, 'end': 240.0}]
        three_chapters = [{'start': 0.0, 'end': 100.0}, {'start': 100.0, 'end': 190.0}, {'start': 190.0, 'end': 240.0}]
        placed = {'00000-00001.m4a': 0.0, '00000-00002.m4a': 120.0, '00000-00003.m4a': 200.0}

        self.assertEqual(self.extractor._audio_file_offsets(epub_with([120.0, 80.0, 40.0]), ['OEBPS/ch1.smil'], two_chapters), placed)
        self.assertEqual(self.extractor._audio_file_offsets(epub_with([120.0, 80.0, 40.0]), ['OEBPS/ch1.smil'], None), placed)
        # A file with no recorded length falls back to one file per chapter.
        self.assertEqual(
            self.extractor._audio_file_offsets(epub_with([120.0, 80.0, None]), ['OEBPS/ch1.smil'], three_chapters),
            {'00000-00001.m4a': 0.0, '00000-00002.m4a': 100.0, '00000-00003.m4a': 190.0})

    def test_xml_parsing_with_prefixes(self):
        """Verify stripped XML is valid and parseable by ElementTree."""
        # This string would cause "unbound prefix" if xmlns:epub is stripped but epub:textref remains
        xml = '<seq epub:textref="foo.html" xmlns:epub="ops">Content</seq>'
        stripped = self.extractor._strip_namespaces(xml)
        
        try:
            root = ET.fromstring(stripped)
            self.assertEqual(root.tag, 'seq')
            # attribute name should be just 'textref' now
            self.assertIn('textref', root.attrib)
            self.assertEqual(root.attrib['textref'], 'foo.html')
        except ET.ParseError as e:
            self.fail(f"Stripped XML failed to parse: {e}")

    def test_malicious_container_entity_is_rejected_without_crash(self):
        """Defused XML should reject entity expansion and return no OPF path."""
        mock_zf = MagicMock()
        mock_zf.read.return_value = b"""<?xml version="1.0"?>
        <!DOCTYPE root [<!ENTITY boom SYSTEM "file:///etc/passwd">]>
        <container><rootfiles><rootfile full-path="&boom;"/></rootfiles></container>
        """

        self.assertIsNone(self.extractor._find_opf_path(mock_zf))

if __name__ == '__main__':
    unittest.main()
