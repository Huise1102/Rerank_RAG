from langchain_core.documents import Document

from src.ingest.pdf_structure import (
    PageInfo,
    _detect_sections,
    _strip_repeated_lines,
    _toc_entries,
    table_columns,
    table_to_markdown,
    table_to_text,
)
from src.ingest.splitter import split_documents


def _page(number: int, text: str, raw: str | None = None) -> PageInfo:
    return PageInfo(number=number, text=text, raw_text=raw or text, height=800)


def test_table_to_markdown_fills_header_and_drops_empty_column():
    markdown = table_to_markdown(
        [
            ["项目", "金额", ""],
            ["A", "100", ""],
            ["B", "200", ""],
        ]
    )
    assert markdown.splitlines()[0] == "| 项目 | 金额 |"
    assert "| A | 100 |" in markdown
    assert "| B | 200 |" in markdown


def test_strip_repeated_lines_removes_page_header():
    pages = [
        _page(1, "示例学院文件\n第一条 内容一"),
        _page(2, "示例学院文件\n第二条 内容二"),
        _page(3, "示例学院文件\n第三条 内容三"),
    ]
    _strip_repeated_lines(pages)
    assert all("示例学院文件" not in page.text for page in pages)
    assert "第一条 内容一" in pages[0].text


def test_toc_entries_map_printed_pages_and_clear_toc():
    toc_text = "目录\n文件一........1\n文件二........3"
    pages = [
        _page(1, "封面"),
        _page(2, toc_text, toc_text),
        _page(3, "第一章\n第一条 内容"),
        _page(4, "第一章\n第一条 内容"),
        _page(5, "第一章\n第一条 内容"),
    ]
    entries = _toc_entries(pages)
    assert entries == [(3, "文件一"), (5, "文件二")]
    assert pages[1].is_toc and pages[1].text == ""


def test_detect_sections_uses_toc():
    pages = [
        _page(1, "封面"),
        _page(2, "目录\n文件一........1\n文件二........3", "目录\n文件一........1\n文件二........3"),
        _page(3, "第一章\n第一条 内容"),
        _page(4, "第一章\n第一条 内容"),
        _page(5, "第一章\n第一条 内容"),
    ]
    toc = _toc_entries(pages)
    sections = _detect_sections(pages, "汇编", toc)
    assert [(item.start_page, item.end_page, item.title) for item in sections] == [
        (3, 4, "文件一"),
        (5, 5, "文件二"),
    ]


def test_clause_chunks_track_pages_and_section_context():
    text = (
        "[[PAGE:3]]\n第一章 总则\n第一条 目的\n内容一\n"
        "[[PAGE:4]]\n第二条 范围\n内容二"
    )
    documents = [
        Document(
            page_content=text,
            metadata={
                "chunk_type": "text",
                "section_title": "附件 1 补充意见",
                "section_doc_no": "示例〔2026〕25号",
                "section_index": 0,
                "page": 3,
                "page_end": 4,
            },
        )
    ]
    chunks = split_documents(documents, strategy="clause", doc_meta={"document_id": 1, "version_id": 1})
    by_article = {chunk.metadata["article_no"]: chunk for chunk in chunks if chunk.metadata.get("article_no")}
    assert by_article["第一条"].metadata["page"] == 3
    assert by_article["第二条"].metadata["page"] == 4
    assert "附件 1 补充意见" in by_article["第一条"].metadata["parent_text"]
    assert "示例〔2026〕25号" in by_article["第一条"].metadata["parent_text"]
    assert all("[[PAGE:" not in chunk.page_content for chunk in chunks)


def test_table_to_markdown_fills_merged_cells():
    rows = [
        ["项目", "级别", "等级", "分值"],
        ["教学成果奖", "省级", "特等奖", "5000"],
        [None, "省级", "一等奖", "2000"],
    ]
    markdown = table_to_markdown(rows)
    assert "| 教学成果奖 | 省级 | 一等奖 | 2000 |" in markdown


def test_single_column_table_is_treated_as_text():
    rows = [["说明"], ["原则上不低"], ["于 32 课时/年"]]
    assert table_columns(rows) == 1
    assert "原则上不低" in table_to_text(rows)


def test_table_chunks_repeat_header_and_add_context():
    markdown = "| 项目 | 金额 |\n| --- | --- |\n| A | 100 |"
    documents = [
        Document(
            page_content=markdown,
            metadata={
                "chunk_type": "table",
                "section_title": "附件 1 补充意见",
                "table_caption": "专项津补贴标准表",
                "page": 3,
                "page_end": 3,
            },
        )
    ]
    chunks = split_documents(documents, strategy="clause", doc_meta={"document_id": 1, "version_id": 1})
    assert [chunk.metadata.get("chunk_type") for chunk in chunks] == ["table", "table_row"]
    content = chunks[0].page_content
    assert content.startswith("附件 1 补充意见 · 专项津补贴标准表")
    assert "| 项目 | 金额 |" in content
    assert "项目：A" in chunks[1].page_content
