import io
import zipfile

import pytest

from brodar.artifacts import Slide,presentation,read_presentation


def test_real_editable_pptx_preserves_case_and_unicode():
    body=presentation('API Research',[Slide(title='API_KEY and Русский',bullets=['Uzbek o‘quvchi','https://Example.com/Case'],notes='Keep this note')])
    assert body[:2]==b'PK'
    deck=read_presentation(body)
    text='\n'.join(s.text for s in deck.slides[0].shapes if s.has_text_frame)
    assert 'API_KEY and Русский' in text and 'https://Example.com/Case' in text
    assert 'Keep this note' in deck.slides[0].notes_slide.notes_text_frame.text


def test_uploaded_pptx_xml_entities_are_rejected_before_parsing():
    body=io.BytesIO()
    with zipfile.ZipFile(body,'w') as archive:
        archive.writestr('[Content_Types].xml','<!DOCTYPE root [<!ENTITY x SYSTEM "file:///not-a-real-file">]><root>&x;</root>')
    with pytest.raises(ValueError,match='entity declarations'):read_presentation(body.getvalue())


def test_dense_slides_request_splitting_instead_of_silent_truncation():
    with pytest.raises(ValueError,match='Split dense content'):
        Slide(title='Too dense',body='x'*1900)
