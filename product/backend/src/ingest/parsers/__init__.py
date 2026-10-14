"""各格式解析器：按扩展名分派到对应解析器，统一产出 ``LoadedPage`` 结构。

- ``pdf_parser.parse_pdf``   pdfplumber 逐页抽取 + 页眉页脚/断行清洗
- ``docx_parser.parse_docx`` python-docx 逐段落抽取，段落序号作 page
- ``text_parser.parse_text`` txt 与 md 直接读取（md 保留原文标记），整体作为一页
"""
