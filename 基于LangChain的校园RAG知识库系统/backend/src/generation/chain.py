from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from src.core.config import settings

SYSTEM_PROMPT = (
    "你是校园知识库问答助手，只能依据提供的参考资料回答问题。"
    "回答必须逐条标注引用编号，编号与参考资料序号一致，例如 [1]、[2]。"
    "参考资料没有覆盖的内容要明确说明未找到依据，不得编造条款、数字或办理时限。"
    "回答使用简体中文，并用 Markdown 组织：先用一句话给出结论，再用小标题分区，"
    "依据与步骤用有序列表或表格呈现，涉及条件、材料、时限时逐条列出；"
    "引用编号紧跟在对应内容之后。不要重复问题，不要写客套话，保持结构清晰、内容简洁。"
)

HUMAN_TEMPLATE = "参考资料：\n{context}\n\n用户问题：{question}"

DISCLAIMER = "本回答由系统根据知识库自动生成，仅供参考，具体以正式文件为准。"
REFUSAL = "根据现有知识库无法确定，本问题未找到可核验的制度依据。"


def build_prompt(no_think: bool = False) -> ChatPromptTemplate:
    human = HUMAN_TEMPLATE + ("\n/no_think" if no_think else "")
    return ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("history"),
            ("human", human),
        ]
    )


def _citation_label(index: int, metadata: dict) -> str:
    title = str(metadata.get("doc_title") or metadata.get("filename") or "未知文件").strip("《》")
    version = metadata.get("version_label")
    article = metadata.get("article_no")
    page = metadata.get("page")
    parts = [f"[{index}]《{title}》"]
    if version:
        parts.append(f"（{version}）")
    if article:
        parts.append(str(article))
    if page and page != "unknown":
        parts.append(f"第 {page} 页")
    return "，".join(parts)


def format_context(documents: list[Document]) -> str:
    pieces: list[str] = []
    used = 0
    for index, document in enumerate(documents, start=1):
        metadata = document.metadata
        location = _citation_label(index, metadata)
        snippet = document.page_content.strip()
        parent = str(metadata.get("parent_text") or "").strip()
        if parent and snippet not in parent:
            snippet = f"{parent}\n{snippet}"
        block = f"{location}\n{snippet}"
        if used + len(block) > settings.context_max_chars:
            break
        pieces.append(block)
        used += len(block)
    return "\n\n".join(pieces) if pieces else "无可用参考资料"


def build_chain(llm, no_think: bool = False) -> object:
    return build_prompt(no_think) | llm | StrOutputParser()


def build_citations(documents: list[Document]) -> list[dict]:
    citations: list[dict] = []
    for index, document in enumerate(documents, start=1):
        metadata = document.metadata
        page = metadata.get("page")
        citations.append(
            {
                "index": index,
                "chunk_id": metadata.get("chunk_id"),
                "document_id": metadata.get("document_id"),
                "version_id": metadata.get("version_id"),
                "source": metadata.get("filename", "未知文件"),
                "filename": metadata.get("filename", "未知文件"),
                "doc_title": metadata.get("doc_title") or metadata.get("filename", "未知文件"),
                "doc_no": metadata.get("doc_no") or None,
                "page": page if isinstance(page, int) else None,
                "page_end": metadata.get("page_end") if isinstance(metadata.get("page_end"), int) else None,
                "article_no": metadata.get("article_no") or None,
                "version_label": metadata.get("version_label") or None,
                "effective_date": metadata.get("effective_date") or None,
                "department": metadata.get("department_name") or None,
                "label": _citation_label(index, metadata),
                "text": document.page_content.strip()[:300],
                "snippet": document.page_content.strip()[:300],
                "preview_url": (
                    f"/api/v1/documents/{metadata.get('document_id')}/preview"
                    if metadata.get("document_id")
                    else None
                ),
            }
        )
    return citations
