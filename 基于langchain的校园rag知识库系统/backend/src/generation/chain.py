from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from src.core.config import settings

SYSTEM_PROMPT = (
    "你是校园制度问答助手，只能依据提供的参考资料回答问题。"
    "回答必须逐条标注引用编号，编号与参考资料序号一致，例如 [1]、[2]。"
    "参考资料没有覆盖的内容要明确说明未找到依据，不得编造制度条款、数字或办理时限。"
    "回答使用简体中文，并用 Markdown 组织：先用一句话给出结论，再用小标题分区，"
    "依据与步骤用有序列表或表格呈现，涉及条件、材料、时限时逐条列出；"
    "引用编号紧跟在对应内容之后。不要重复问题，不要写客套话，保持结构清晰、内容简洁。"
)

HUMAN_TEMPLATE = "参考资料：\n{context}\n\n用户问题：{question}"


def build_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("history"),
            ("human", HUMAN_TEMPLATE),
        ]
    )


def format_context(documents: list[Document]) -> str:
    pieces: list[str] = []
    used = 0
    for index, document in enumerate(documents, start=1):
        filename = document.metadata.get("filename", "未知文件")
        page = document.metadata.get("page")
        location = f"{filename} 第 {page} 页" if page else filename
        snippet = document.page_content.strip()
        block = f"[{index}] {location}\n{snippet}"
        if used + len(block) > settings.context_max_chars:
            break
        pieces.append(block)
        used += len(block)
    return "\n\n".join(pieces) if pieces else "无可用参考资料"


def build_chain(llm) -> object:
    return build_prompt() | llm | StrOutputParser()


def build_citations(documents: list[Document]) -> list[dict]:
    citations: list[dict] = []
    for index, document in enumerate(documents, start=1):
        page = document.metadata.get("page")
        citations.append(
            {
                "index": index,
                "document_id": document.metadata.get("document_id"),
                "filename": document.metadata.get("filename", "未知文件"),
                "page": page if isinstance(page, int) else None,
                "snippet": document.page_content.strip()[:300],
            }
        )
    return citations
