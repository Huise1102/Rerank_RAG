import asyncio
from datetime import datetime

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from src.agent.loop import run_agent
from src.agent.state import AgentState
from src.retrieval.scope import RetrievalScope


def make_scope() -> RetrievalScope:
    return RetrievalScope(
        user_id=1,
        role="staff",
        department_id=3,
        now=datetime(2026, 11, 13, 9, 0, 0),
        allow_public=True,
        allow_department=True,
    )


def retrieve_call(query: str = "补办学生证") -> dict:
    return {
        "name": "retrieve",
        "args": {"query": query, "strategy": "hybrid_rerank", "top_k": 3},
        "id": "call-1",
        "type": "tool_call",
    }


class FakeModel:
    def __init__(self, responses: list[AIMessage]):
        self.responses = list(responses)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.responses:
            return self.responses.pop(0)
        return AIMessage(content="已完成")

    def invoke(self, messages):
        return AIMessage(content="兜底回答")


def collect(state: AgentState, model: FakeModel, monkeypatch) -> list[dict]:
    async def fake_retrieve(*args, **kwargs):
        return [
            Document(
                page_content="补办学生证需要携带身份证到教务处办理，三个工作日内完成。",
                metadata={
                    "chunk_id": "c1",
                    "document_id": 10,
                    "filename": "学生证管理办法.md",
                    "version_label": "v1",
                    "article_no": "第三条",
                },
            )
        ]

    monkeypatch.setattr("src.agent.loop.retrieve_documents", fake_retrieve)

    async def run():
        events = []
        async for event in run_agent(state, model, None, max_rounds=3, max_retrievals=3):
            events.append(event)
        return events

    return asyncio.run(run())


def test_agent_stops_at_three_rounds(monkeypatch):
    state = AgentState(question="学生证怎么补办", kb_id=1, scope=make_scope())
    model = FakeModel([AIMessage(content="", tool_calls=[retrieve_call()]) for _ in range(5)])
    events = collect(state, model, monkeypatch)
    assert state.rounds == 3
    assert state.retrievals <= 3
    assert state.final_answer
    assert any(item["action"] == "answer" for item in state.trace)
    assert any(event.get("type") == "delta" for event in events)


def test_agent_requires_retrieval_before_answer(monkeypatch):
    state = AgentState(question="随便回答", kb_id=1, scope=make_scope())
    model = FakeModel([AIMessage(content="直接回答") for _ in range(4)])
    collect(state, model, monkeypatch)
    assert state.retrievals == 0
    assert state.evidence == []
    assert state.final_answer


def test_agent_answer_after_retrieval(monkeypatch):
    state = AgentState(question="学生证怎么补办", kb_id=1, scope=make_scope())
    model = FakeModel(
        [
            AIMessage(content="", tool_calls=[retrieve_call()]),
            AIMessage(content="携带身份证到教务处办理 [1]。"),
        ]
    )
    collect(state, model, monkeypatch)
    assert state.retrievals == 1
    assert len(state.evidence) == 1
    assert "[1]" in state.final_answer


def test_agent_can_ask_clarification(monkeypatch):
    state = AgentState(question="怎么办", kb_id=1, scope=make_scope())
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "ask_clarification",
                        "args": {"question": "请说明要办理的具体事项"},
                        "id": "call-2",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    collect(state, model, monkeypatch)
    assert state.clarifications == 1
    assert state.final_answer == "请说明要办理的具体事项"
    assert state.retrievals == 0
