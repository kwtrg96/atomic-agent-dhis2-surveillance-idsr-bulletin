"""Unit tests for the "Render bulletin" atomic protocol (Quarto CLI, quarto render).

Citable source: Posit. Quarto (v1.8.27) [Computer software]. https://quarto.org
  Docs used: "quarto render" CLI reference; Word (docx) and Typst output formats.
Contract (from the IDSR Bulletin Agent wire diagram):
  checked bulletin text (Quarto markdown, .qmd, UTF-8) + figures (PNG)
  -> bulletin document (.docx via `--to docx`; .pdf via `--to typst`).
The protocol is treated as a black box: tests call only `quarto render`.
Tested with Quarto CLI 1.8.27 (installed separately, not via pip), pytest==8.4.2, Python 3.13.
"""
import re
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest

YAML = '---\ntitle: "IDSR Weekly Bulletin, Week 40"\n---\n\n'


def write_png(path: Path) -> None:
    """Invented 1x1 PNG so tests need no real data."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def render(tmp_path, body, fmt="docx", name="bulletin.qmd", encoding="utf-8", extra=()):
    """The same CLI call the glue makes; not new protocol code."""
    src = tmp_path / name
    if body is not None:
        src.write_bytes(body.encode(encoding) if isinstance(body, str) else body)
    return subprocess.run(["quarto", "render", str(src), "--to", fmt, *extra],
                          cwd=tmp_path, capture_output=True, text=True, timeout=120)


def docx_xml(path: Path) -> str:
    return zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")


# ---------- Typical inputs ----------
def test_renders_docx(tmp_path):
    r = render(tmp_path, YAML + "Suspected cholera cases rose this week.\n")
    out = tmp_path / "bulletin.docx"
    assert r.returncode == 0 and out.exists()
    assert zipfile.is_zipfile(out)


def test_renders_pdf_via_typst(tmp_path):
    r = render(tmp_path, YAML + "Suspected cholera cases rose this week.\n", fmt="typst")
    out = tmp_path / "bulletin.pdf"
    assert r.returncode == 0 and out.read_bytes()[:5] == b"%PDF-"


def test_title_from_yaml_in_docx(tmp_path):
    render(tmp_path, YAML + "Body.\n")
    assert "IDSR Weekly Bulletin, Week 40" in docx_xml(tmp_path / "bulletin.docx")


@pytest.mark.parametrize("number", ["1,234", "12.5%", "3/45", "<5", "0"])
def test_numbers_preserved_exactly(tmp_path, number):
    render(tmp_path, YAML + f"Value reported: {number} cases.\n")
    xml = docx_xml(tmp_path / "bulletin.docx")
    assert number.replace("<", "&lt;") in xml


def test_pipe_table_becomes_word_table(tmp_path):
    body = YAML + "| District | Cases |\n|---|---|\n| North | 12 |\n| South | 7 |\n"
    render(tmp_path, body)
    xml = docx_xml(tmp_path / "bulletin.docx")
    assert "<w:tbl>" in xml and "North" in xml and "12" in xml


def test_png_figure_embedded(tmp_path):
    write_png(tmp_path / "map.png")
    render(tmp_path, YAML + "![Cholera cases by district](map.png)\n")
    names = zipfile.ZipFile(tmp_path / "bulletin.docx").namelist()
    assert any(n.startswith("word/media/") and n.endswith(".png") for n in names)


def test_french_accents_preserved(tmp_path):
    text = "Rougeole : 12 cas suspects à Kédougou, déclarés en hôpital."
    render(tmp_path, YAML + text + "\n")
    assert text in docx_xml(tmp_path / "bulletin.docx")


def test_output_flag_sets_filename(tmp_path):
    r = render(tmp_path, YAML + "Body.\n", extra=("--output", "bulletin_w40_fr.docx"))
    assert r.returncode == 0 and (tmp_path / "bulletin_w40_fr.docx").exists()


# ---------- Edge cases ----------
def test_yaml_only_document_renders(tmp_path):
    r = render(tmp_path, YAML)
    assert r.returncode == 0 and (tmp_path / "bulletin.docx").exists()


def test_empty_file_renders(tmp_path):
    r = render(tmp_path, "")
    assert r.returncode == 0 and (tmp_path / "bulletin.docx").exists()


def test_long_document_renders_completely(tmp_path):
    body = YAML + "".join(f"District {i}: {i * 3} suspected cases.\n\n" for i in range(1, 1001))
    r = render(tmp_path, body)
    xml = docx_xml(tmp_path / "bulletin.docx")
    assert r.returncode == 0 and "District 1000: 3000 suspected cases." in xml


def test_markdown_symbols_in_district_names(tmp_path):
    render(tmp_path, YAML + "Saint-Louis & Ziguinchor (zone #2): 4 cases.\n")
    assert "Saint-Louis &amp; Ziguinchor (zone #2): 4 cases." in docx_xml(tmp_path / "bulletin.docx")


@pytest.mark.xfail(strict=True, reason="Known limitation: missing figure only warns; render exits 0")
def test_missing_figure_fails_render(tmp_path):
    r = render(tmp_path, YAML + "![Map](missing_map.png)\n")
    assert r.returncode != 0


def test_missing_figure_warns(tmp_path):
    r = render(tmp_path, YAML + "![Map](missing_map.png)\n")
    assert "Could not fetch resource" in (r.stdout + r.stderr)


# ---------- Invalid inputs ----------
def test_nonexistent_input_file_fails(tmp_path):
    r = render(tmp_path, None, name="does_not_exist.qmd")
    assert r.returncode != 0 and not (tmp_path / "does_not_exist.docx").exists()


def test_unknown_format_fails(tmp_path):
    r = render(tmp_path, YAML + "Body.\n", fmt="notaformat")
    assert r.returncode != 0


def test_malformed_yaml_fails(tmp_path):
    r = render(tmp_path, '---\ntitle: "unclosed\n  bad: [\n---\n\nBody.\n')
    assert r.returncode != 0


@pytest.mark.xfail(strict=True, reason="Known limitation: non-UTF-8 bytes silently become U+FFFD")
def test_non_utf8_input_fails(tmp_path):
    r = render(tmp_path, YAML + "Kédougou: 3 cases.\n", encoding="latin-1")
    assert r.returncode != 0

def test_two_pipe_tables_become_two_word_tables(tmp_path):
    table = "| District | Cases |\n|---|---|\n| North | 12 |\n| South | 7 |\n"
    r = render(tmp_path, YAML + table + "\nWeekly summary text.\n\n" + table)
    doc = Document(tmp_path / "bulletin.docx")
    assert len(doc.tables) == 3  # wrong on purpose: there are two tables